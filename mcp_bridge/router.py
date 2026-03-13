"""
FastAPI routes for MCP Bridge — alternative to MCP protocol for web UI access.

Prefix: /api/v1/bridge
"""
from __future__ import annotations

import json
import time
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mcp_bridge.tool_store import ToolStore
from mcp_bridge.interactive import IntentClassifier, RevitQueryExecutor
from mcp_bridge import sandbox

bridge_router = APIRouter(prefix="/api/v1/bridge", tags=["mcp-bridge"])
_tool_store = ToolStore()
_classifier = IntentClassifier()


def _get_bridge_config() -> dict:
    """Read mcp_bridge config section."""
    try:
        from server.app.deps import get_config
        return get_config().get("mcp_bridge", {})
    except Exception:
        return {}


async def _get_revit_client():
    """Get pooled Revit client using config."""
    from mcp_bridge.client_pool import RevitClientPool
    cfg = _get_bridge_config()
    return await RevitClientPool.get_client(
        host=cfg.get("revit_host", "localhost"),
        port=cfg.get("revit_port", 18080),
        timeout=cfg.get("command_timeout", 60),
        connect_timeout=cfg.get("connect_timeout", 5),
    )


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
    skip_review: bool = False


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

    # Security review
    safe, warnings = sandbox.review(code)

    return {"code": code, "rag_context": meta, "safe": safe, "warnings": warnings}


@bridge_router.post("/generate-stream")
async def generate_code_stream(req: GenerateRequest):
    """RAG + LLM -> stream C# code generation via SSE."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm)

    async def event_stream():
        # Phase 1: RAG retrieval
        yield f"event: rag\ndata: {json.dumps('Searching API docs and SDK examples...')}\n\n"

        results = retriever.search(req.query, api_top_k=req.api_top_k, code_top_k=req.code_top_k)
        ctx = retriever.build_context(results)

        yield f"event: rag\ndata: {json.dumps(f'Found {len(results.api_items)} API docs + {len(results.sdk_items)} SDK examples')}\n\n"

        # Phase 2: Stream code generation
        from mcp_bridge.code_generator import SYSTEM_EXECUTE
        system = SYSTEM_EXECUTE.format(
            revit_version=gen.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
            selections_context="",
        )

        accumulated = ""
        for token in llm.generate_stream(req.query, system_prompt=system):
            accumulated += token
            yield f"event: token\ndata: {json.dumps(token)}\n\n"

        # Phase 3: Extract code and review
        code = gen._extract_code(accumulated)
        safe, warnings = sandbox.review(code)

        done_data = {
            "code": code,
            "rag_context": {
                "query": req.query,
                "rewritten_query": results.rewritten_query,
                "api_count": len(results.api_items),
                "sdk_count": len(results.sdk_items),
            },
            "safe": safe,
            "warnings": warnings,
        }
        yield f"event: done\ndata: {json.dumps(done_data)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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

    safe, warnings = sandbox.review(code)
    return {"code": code, "rag_context": meta, "safe": safe, "warnings": warnings}


# -- Execution Routes ----------------------------------------------------------

@bridge_router.post("/execute")
async def execute_code(req: ExecuteRequest):
    """Send C# code to Revit via TCP socket."""
    # Security review unless explicitly skipped
    if not req.skip_review:
        safe, warnings = sandbox.review(req.code)
        if not safe:
            return {"success": False, "error": "Security review failed", "warnings": warnings}

    try:
        client = await _get_revit_client()
        resp = await client.send_code(req.code, req.parameters)
        return {"success": resp.success, "result": resp.result, "error": resp.error}
    except (ConnectionError, OSError):
        raise HTTPException(502, "Cannot connect to Revit plugin (port 18080). Is Revit running?")
    except Exception as e:
        raise HTTPException(500, str(e))


@bridge_router.post("/generate-and-execute")
async def generate_and_execute(req: GenerateRequest):
    """Full pipeline: RAG generate -> security review -> send to Revit -> retry on compile error."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator
    from mcp_bridge.retry import is_compile_error, retry_on_compile_error

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm)
    code, meta = gen.generate(req.query, req.api_top_k, req.code_top_k)

    # Security review
    safe, warnings = sandbox.review(code)
    if not safe:
        return {
            "code": code,
            "rag_context": meta,
            "safe": False,
            "warnings": warnings,
            "execution": {"success": False, "error": "Blocked by security review"},
        }

    try:
        client = await _get_revit_client()
        resp = await client.send_code(code)

        # Retry on compile errors
        attempts_log = []
        if not resp.success and is_compile_error(resp.error):
            code, success, result, error, attempts_log = await retry_on_compile_error(
                generator=gen,
                revit_client=client,
                user_query=req.query,
                error_msg=resp.error,
                code=code,
                max_retries=2,
            )
            return {
                "code": code,
                "rag_context": meta,
                "safe": True,
                "warnings": [],
                "execution": {
                    "success": success,
                    "result": result,
                    "error": error,
                },
                "retry_attempts": attempts_log,
            }

        return {
            "code": code,
            "rag_context": meta,
            "safe": True,
            "warnings": [],
            "execution": {
                "success": resp.success,
                "result": resp.result,
                "error": resp.error,
            },
            "retry_attempts": [],
        }
    except (ConnectionError, OSError):
        return {
            "code": code,
            "rag_context": meta,
            "safe": True,
            "warnings": [],
            "execution": {
                "success": False,
                "error": "Cannot connect to Revit plugin (port 18080)",
            },
            "retry_attempts": [],
        }


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

    try:
        client = await _get_revit_client()
        resp = await client.send_code(code)
        if resp.success:
            _tool_store.record_usage(name)
        return {"success": resp.success, "tool": name, "result": resp.result, "error": resp.error}
    except (ConnectionError, OSError):
        raise HTTPException(502, "Cannot connect to Revit plugin (port 18080)")


@bridge_router.get("/tools/{name}/choices")
async def get_tool_choices(name: str):
    """Query Revit for dynamic parameter choices (levels, family types, elements).

    Returns {param_name: [{label, value}, ...]} for each dynamic parameter.
    """
    dynamic_params = _tool_store.get_dynamic_params(name)
    if not dynamic_params:
        return {}

    try:
        client = await _get_revit_client()
        executor = RevitQueryExecutor(client)
        choices: dict[str, list[dict]] = {}

        for p in dynamic_params:
            source = p["choices_from"]
            items: list[dict] = []

            if source == "levels":
                levels = await executor.get_levels()
                items = [
                    {"label": f"{lv.get('Name', '?')} ({lv.get('ElevationMm', 0)}mm)",
                     "value": lv.get("Name", "")}
                    for lv in levels
                ]
            elif source.startswith("family_types:"):
                category = source.split(":", 1)[1]
                types = await executor.get_family_types([category])
                items = [
                    {"label": t.get("name", t.get("Name", str(t))),
                     "value": t.get("name", t.get("Name", str(t)))}
                    for t in types
                ]
            elif source == "floor_types":
                code = (
                    'var types = new FilteredElementCollector(document)\n'
                    '    .OfClass(typeof(FloorType))\n'
                    '    .Cast<FloorType>()\n'
                    '    .Select(ft => new { Name = ft.Name, Id = ft.Id.Value })\n'
                    '    .ToList();\n'
                    'return types;'
                )
                resp = await client.send_code(code)
                if resp.success and resp.result:
                    data = resp.result if isinstance(resp.result, list) else [resp.result]
                    items = [
                        {"label": ft.get("Name", str(ft)),
                         "value": ft.get("Name", str(ft))}
                        for ft in data
                    ]
            elif source.startswith("elements:"):
                category = source.split(":", 1)[1]
                code = (
                    f'var elems = new FilteredElementCollector(document)\n'
                    f'    .OfCategory(BuiltInCategory.{category})\n'
                    f'    .WhereElementIsNotElementType()\n'
                    f'    .Select(e => new {{ Id = e.Id.Value, Name = e.Name }})\n'
                    f'    .ToList();\n'
                    f'return elems;'
                )
                resp = await client.send_code(code)
                if resp.success and resp.result:
                    data = resp.result if isinstance(resp.result, list) else [resp.result]
                    items = [
                        {"label": f"{el.get('Name', '?')} (ID: {el.get('Id', '?')})",
                         "value": el.get("Id", "")}
                        for el in data
                    ]

            choices[p["name"]] = items

        return choices
    except (ConnectionError, OSError):
        raise HTTPException(502, "Cannot connect to Revit plugin (port 18080)")


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
    try:
        client = await _get_revit_client()
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
    except (ConnectionError, OSError):
        raise HTTPException(502, "Cannot connect to Revit plugin (port 18080)")


@bridge_router.post("/trigger-selection")
async def trigger_selection():
    """Trigger Revit selection mode and return selected elements."""
    try:
        client = await _get_revit_client()
        executor = RevitQueryExecutor(client)
        elements = await executor.trigger_selection()
        return {"elements": elements}
    except (ConnectionError, OSError):
        raise HTTPException(502, "Cannot connect to Revit plugin (port 18080)")


@bridge_router.get("/revit-health")
async def revit_health():
    """Check if Revit plugin is reachable."""
    from mcp_bridge.client_pool import RevitClientPool
    cfg = _get_bridge_config()
    try:
        t0 = time.monotonic()
        ok = await RevitClientPool.ping(
            host=cfg.get("revit_host", "localhost"),
            port=cfg.get("revit_port", 18080),
        )
        latency = round((time.monotonic() - t0) * 1000)
        return {"revit_connected": ok, "latency_ms": latency}
    except Exception:
        return {"revit_connected": False, "latency_ms": None}
