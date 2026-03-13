"""
FastAPI routes for MCP Bridge — alternative to MCP protocol for web UI access.

Prefix: /api/v1/bridge
"""
from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_bridge.revit_client import RevitClient
from mcp_bridge.tool_store import ToolStore
from mcp_bridge.interactive import IntentClassifier, RevitQueryExecutor

bridge_router = APIRouter(prefix="/api/v1/bridge", tags=["mcp-bridge"])
_tool_store = ToolStore()
_classifier = IntentClassifier()


# -- Request Models ------------------------------------------------------------

class GenerateRequest(BaseModel):
    query: str
    api_top_k: int = 15
    code_top_k: int = 5


class GenerateWithSelectionsRequest(BaseModel):
    query: str
    selections: dict = {}
    api_top_k: int = 15
    code_top_k: int = 5


class ExecuteRequest(BaseModel):
    code: str
    parameters: list | None = None


class SolidifyRequest(BaseModel):
    name: str
    code: str
    description: str = ""
    parameters: list[dict] = []
    tags: list[str] = []
    source_query: str = ""


class RunToolRequest(BaseModel):
    name: str
    params: dict = {}


class ClassifyRequest(BaseModel):
    query: str


class QueryRevitRequest(BaseModel):
    command: str
    params: dict = {}


# -- Code Generation Routes ---------------------------------------------------

@bridge_router.post("/generate")
async def generate_code(req: GenerateRequest):
    """RAG + LLM -> generate C# code for Revit execution."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm)

    code, meta = gen.generate(req.query, req.api_top_k, req.code_top_k)
    return {"code": code, "rag_context": meta}


@bridge_router.post("/generate-with-selections")
async def generate_with_selections(req: GenerateWithSelectionsRequest):
    """RAG + LLM -> generate C# code using user selections from interactive workflow."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm)

    code, meta = gen.generate(
        req.query, req.api_top_k, req.code_top_k, selections=req.selections
    )
    return {"code": code, "rag_context": meta}


# -- Execution Routes ----------------------------------------------------------

@bridge_router.post("/execute")
async def execute_code(req: ExecuteRequest):
    """Send C# code to Revit via TCP socket."""
    client = RevitClient()
    try:
        await client.connect()
        resp = await client.send_code(req.code, req.parameters)
        return {"success": resp.success, "result": resp.result, "error": resp.error}
    except ConnectionError:
        raise HTTPException(502, "Cannot connect to Revit plugin (port 8080). Is Revit running?")
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await client.disconnect()


@bridge_router.post("/generate-and-execute")
async def generate_and_execute(req: GenerateRequest):
    """Full pipeline: RAG generate -> send to Revit -> return result."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm)
    code, meta = gen.generate(req.query, req.api_top_k, req.code_top_k)

    client = RevitClient()
    try:
        await client.connect()
        resp = await client.send_code(code)
        return {
            "code": code,
            "rag_context": meta,
            "execution": {
                "success": resp.success,
                "result": resp.result,
                "error": resp.error,
            },
        }
    except ConnectionError:
        return {
            "code": code,
            "rag_context": meta,
            "execution": {
                "success": False,
                "error": "Cannot connect to Revit plugin (port 8080)",
            },
        }
    finally:
        await client.disconnect()


# -- Tool Solidification Routes ------------------------------------------------

@bridge_router.post("/solidify")
async def solidify_tool(req: SolidifyRequest):
    """Save successful code as a reusable named tool."""
    tool = _tool_store.solidify(
        name=req.name,
        code=req.code,
        description=req.description,
        parameters=req.parameters,
        tags=req.tags,
        source_query=req.source_query,
    )
    return {"status": "solidified", "name": tool.name, "display_name": tool.display_name}


@bridge_router.get("/tools")
async def list_tools():
    """List all solidified tools."""
    tools = _tool_store.list_tools()
    return [
        {
            "name": t.name,
            "display_name": t.display_name,
            "description": t.description,
            "parameters": t.parameters,
            "tags": t.tags,
            "execution_count": t.execution_count,
        }
        for t in tools
    ]


@bridge_router.get("/tools/{name}")
async def get_tool(name: str):
    """Get a solidified tool by name."""
    tool = _tool_store.load(name)
    if not tool:
        raise HTTPException(404, f"Tool '{name}' not found")
    return {
        "name": tool.name,
        "display_name": tool.display_name,
        "description": tool.description,
        "code_template": tool.code_template,
        "parameters": tool.parameters,
        "tags": tool.tags,
        "execution_count": tool.execution_count,
        "source_query": tool.source_query,
    }


@bridge_router.post("/tools/{name}/run")
async def run_tool(name: str, req: RunToolRequest):
    """Execute a solidified tool with parameters."""
    code = _tool_store.render_code(name, req.params)
    if code is None:
        raise HTTPException(404, f"Tool '{name}' not found")

    client = RevitClient()
    try:
        await client.connect()
        resp = await client.send_code(code)
        if resp.success:
            _tool_store.record_usage(name)
        return {"success": resp.success, "tool": name, "result": resp.result, "error": resp.error}
    except ConnectionError:
        raise HTTPException(502, "Cannot connect to Revit plugin (port 8080)")
    finally:
        await client.disconnect()


@bridge_router.delete("/tools/{name}")
async def delete_tool(name: str):
    """Delete a solidified tool."""
    if _tool_store.delete(name):
        return {"status": "deleted", "name": name}
    raise HTTPException(404, f"Tool '{name}' not found")


# -- Interactive Selection Routes ----------------------------------------------

@bridge_router.post("/classify-intent")
async def classify_intent(req: ClassifyRequest):
    """Classify user query to determine if interactive selection is needed."""
    result = _classifier.classify(req.query)
    return result


@bridge_router.post("/query-revit")
async def query_revit(req: QueryRevitRequest):
    """Execute a monorepo pre-built command to query Revit model data."""
    client = RevitClient()
    try:
        await client.connect()
        executor = RevitQueryExecutor(client)

        if req.command == "get_available_family_types":
            categories = req.params.get("categoryList", [])
            data = await executor.get_family_types(categories)
            return {"result": data}
        elif req.command == "get_levels":
            data = await executor.get_levels()
            return {"result": data}
        elif req.command == "get_selected_elements":
            data = await executor.get_selected_elements()
            return {"result": data}
        else:
            resp = await client.send_command(req.command, req.params)
            return {"result": resp.result, "error": resp.error}
    except ConnectionError:
        raise HTTPException(502, "Cannot connect to Revit plugin (port 8080)")
    finally:
        await client.disconnect()


@bridge_router.post("/trigger-selection")
async def trigger_selection():
    """Trigger Revit selection mode and return selected elements."""
    client = RevitClient()
    try:
        await client.connect()
        executor = RevitQueryExecutor(client)
        elements = await executor.trigger_selection()
        return {"elements": elements}
    except ConnectionError:
        raise HTTPException(502, "Cannot connect to Revit plugin (port 8080)")
    finally:
        await client.disconnect()


@bridge_router.get("/revit-health")
async def revit_health():
    """Check if Revit plugin is reachable."""
    client = RevitClient()
    try:
        import time
        t0 = time.monotonic()
        await client.connect()
        ok = await client.ping()
        latency = round((time.monotonic() - t0) * 1000)
        return {"revit_connected": ok, "latency_ms": latency}
    except Exception:
        return {"revit_connected": False, "latency_ms": None}
    finally:
        await client.disconnect()
