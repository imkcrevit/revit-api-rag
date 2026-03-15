"""
FastAPI routes for MCP Bridge — alternative to MCP protocol for web UI access.

Prefix: /api/v1/bridge
"""
from __future__ import annotations

import asyncio
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

# Global user unit preference (default: mm)
_user_unit: str = "mm"


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


# -- API Explorer Routes -------------------------------------------------------

class ParameterizeRequest(BaseModel):
    code: str
    source_query: str = ""


class UnitSettingRequest(BaseModel):
    unit: str  # "mm", "m", or "feet"


class ApiSearchRequest(BaseModel):
    query: str
    top_k: int = 15


class ApiCodeGenRequest(BaseModel):
    api_name: str
    api_context: str
    user_hint: str = ""


@bridge_router.get("/unit")
async def get_unit():
    """Get the current user unit preference."""
    return {"unit": _user_unit}


@bridge_router.post("/unit")
async def set_unit(req: UnitSettingRequest):
    """Set the user unit preference (mm, m, or feet)."""
    global _user_unit
    if req.unit not in ("mm", "m", "feet"):
        raise HTTPException(400, f"Invalid unit '{req.unit}'. Must be mm, m, or feet.")
    _user_unit = req.unit
    return {"unit": _user_unit, "status": "updated"}


@bridge_router.get("/project-units")
async def get_project_units():
    """Query Revit for the project's display unit settings."""
    try:
        client = await _get_revit_client()
        code = (
            'var doc = document;\n'
            'var units = doc.GetUnits();\n'
            'var lengthSpec = Autodesk.Revit.DB.SpecTypeId.Length;\n'
            'var formatOptions = units.GetFormatOptions(lengthSpec);\n'
            'var unitTypeId = formatOptions.GetUnitTypeId();\n'
            'return new {\n'
            '    LengthUnit = unitTypeId.TypeId,\n'
            '    DisplayName = Autodesk.Revit.DB.LabelUtils.GetLabelForUnit(unitTypeId)\n'
            '};\n'
        )
        resp = await client.send_code(code)
        if resp.success and resp.result:
            result = resp.result if isinstance(resp.result, dict) else {}
            unit_id = result.get("LengthUnit", "")
            display = result.get("DisplayName", "")
            # Map Revit unit type to our simplified units
            detected = "mm"
            if "millimeters" in unit_id.lower() or "millimeters" in display.lower():
                detected = "mm"
            elif "meters" in unit_id.lower() and "milli" not in unit_id.lower():
                detected = "m"
            elif "feet" in unit_id.lower() or "foot" in unit_id.lower():
                detected = "feet"
            return {
                "revit_unit": unit_id,
                "display_name": display,
                "detected": detected,
                "current_setting": _user_unit,
            }
        return {"error": resp.error or "Failed to query project units",
                "current_setting": _user_unit}
    except (ConnectionError, OSError):
        return {"error": "Cannot connect to Revit", "current_setting": _user_unit}
    except Exception as e:
        return {"error": str(e), "current_setting": _user_unit}


@bridge_router.post("/parameterize")
async def parameterize_code(req: ParameterizeRequest):
    """Use LLM to convert hardcoded values into {placeholder} parameters for solidification."""
    from server.app.deps import get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator

    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(None, llm)  # retriever not needed for parameterization

    param_code, parameters = gen.parameterize(req.code, req.source_query)
    return {"code": param_code, "parameters": parameters}


@bridge_router.post("/api-search")
async def api_search(req: ApiSearchRequest):
    """Search Revit API docs via RAG — returns reranked results."""
    from server.app.deps import get_retriever
    retriever = get_retriever()
    results = retriever.search(req.query, api_top_k=req.top_k, code_top_k=3)

    api_items = []
    for item in results.api_items:
        api_items.append({
            "name": item.name,
            "full_id": item.full_id,
            "summary": item.summary,
            "syntax": item.syntax,
            "parameters": item.parameters,
            "remark": item.remark,
            "distance": round(item.distance, 4),
        })

    sdk_items = []
    for item in results.sdk_items:
        sdk_items.append({
            "project": item.project,
            "content": item.content[:500],
            "mentioned_apis": item.mentioned_apis,
            "distance": round(item.distance, 4),
        })

    return {
        "rewritten_query": results.rewritten_query,
        "api_items": api_items,
        "sdk_items": sdk_items,
    }


@bridge_router.post("/api-codegen")
async def api_codegen(req: ApiCodeGenRequest):
    """Generate a code example for a specific API member."""
    from server.app.deps import get_config
    from pipeline.llm_client import create_llm_client

    config = get_config()
    llm = create_llm_client(config)

    system = f"""\
You are a Revit 2026 API expert. Generate a short, runnable C# code example
demonstrating the API member below.

## Execution Context
The code runs inside:
```csharp
public static object Execute(Document document, object[] parameters)
{{
    // YOUR CODE HERE
}}
```
Auto-injected usings: System, System.Linq, System.Collections.Generic,
Autodesk.Revit.DB, Autodesk.Revit.UI.

## Rules
- Output ONLY the method body (no class/namespace/using)
- DO NOT create a Transaction (already wrapped)
- Use `document` (not `doc` or `uidoc`)
- Return a meaningful result object
- Add step comments: `// Step 1: ...`
- Use Revit internal units (feet)

## API Reference
{req.api_context}
"""
    prompt = f"Generate a code example for: {req.api_name}"
    if req.user_hint:
        prompt += f"\nUser hint: {req.user_hint}"

    raw = llm.generate_text(prompt, system_prompt=system)

    # Extract code
    import re
    m = re.search(r"```(?:csharp|cs)?\s*\n(.*?)```", raw, re.DOTALL)
    code = m.group(1).strip() if m else raw.strip()

    return {"code": code, "api_name": req.api_name}


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
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit)

    code, meta = gen.generate(req.query, req.api_top_k, req.code_top_k)

    # Security review
    safe, warnings = sandbox.review(code)

    return {"code": code, "rag_context": meta, "safe": safe, "warnings": warnings}


@bridge_router.post("/generate-stream")
async def generate_code_stream(req: GenerateWithSelectionsRequest):
    """RAG + LLM -> stream C# code generation via SSE. Supports optional selections."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit)
    selections = req.selections if req.selections else None

    async def event_stream():
        def _p(msg: str):
            return f"event: progress\ndata: {json.dumps(msg)}\n\n"

        # ── Phase 1: RAG Retrieval ──
        yield _p("Query Rewrite — optimizing search keywords...")

        search_query = retriever.rewrite_query(req.query)
        yield _p(f"Query Rewrite done → \"{search_query[:60]}\"")

        yield _p("Embedding — vectorizing query...")
        query_embedding = retriever._embedder.embed_query(search_query)

        yield _p("Vector Search — querying API docs & SDK examples...")
        api_raw = retriever._api_collection.query(
            query_embeddings=[query_embedding], n_results=req.api_top_k,
        )
        code_raw = retriever._code_collection.query(
            query_embeddings=[query_embedding], n_results=req.code_top_k,
        )

        yield _p("Hydrating — fetching full records from database...")
        api_items = retriever._hydrate_api(api_raw)
        sdk_items = retriever._hydrate_sdk(code_raw)

        from pipeline.retriever import SearchResults
        results = SearchResults(
            query=req.query, rewritten_query=search_query,
            api_items=api_items, sdk_items=sdk_items,
        )

        yield _p(f"Retrieved {len(api_items)} API docs + {len(sdk_items)} SDK examples")
        yield f"event: rag\ndata: {json.dumps(f'Found {len(api_items)} API + {len(sdk_items)} SDK')}\n\n"

        # ── Phase 2: Context Assembly ──
        yield _p("Combining API docs + SDK code into RAG context...")
        ctx = retriever.build_context(results)
        api_ctx_len = len(ctx.get("api_context", ""))
        code_ctx_len = len(ctx.get("code_context", ""))
        yield _p(f"RAG context ready — API: {api_ctx_len} chars, SDK: {code_ctx_len} chars")

        yield _p("Assembling system prompt (rules + context + unit config)...")
        from mcp_bridge.code_generator import SYSTEM_EXECUTE, CodeGenerator as CG
        selections_ctx = CG._build_selections_context(selections) if selections else ""
        system = SYSTEM_EXECUTE.format(
            revit_version=gen.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)"),
            selections_context=selections_ctx,
            unit_context=CG.UNIT_CONTEXTS.get(gen.user_unit, CG.UNIT_CONTEXTS["mm"]),
        )
        yield _p(f"System prompt assembled — {len(system)} chars total")

        # ── Phase 3: LLM Generation (streaming) ──
        yield _p("LLM generating C# code (streaming)...")
        accumulated = ""
        for token in llm.generate_stream(req.query, system_prompt=system):
            accumulated += token
            yield f"event: token\ndata: {json.dumps(token)}\n\n"

        # ── Phase 4: Post-processing ──
        yield _p("Extracting code from LLM response...")
        code = gen._extract_code(accumulated)

        yield _p("Security review — scanning for unsafe patterns...")
        safe, warnings = sandbox.review(code)
        yield _p(f"Security review done — {'Safe' if safe else 'WARNING: ' + '; '.join(warnings)}")

        yield _p("Done — code ready for review")
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
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit)

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
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit)
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
    """Save successful code as a reusable named tool, then sync to Revit plugin."""
    tool = _tool_store.solidify(
        name=req.name,
        code=req.code,
        description=req.description,
        parameters=req.parameters,
        tags=req.tags,
        source_query=req.source_query,
    )

    # Sync to Revit plugin via TCP (best-effort, don't block on failure)
    revit_synced = False
    try:
        client = await _get_revit_client()
        resp = await client.send_command("manage_solidified_tools", {
            "action": "register",
            "name": tool.name,
            "code_template": tool.code_template,
            "description": tool.description,
            "parameters": req.parameters,
            "source_query": req.source_query,
        })
        revit_synced = resp.success
    except Exception:
        pass  # Revit may not be connected or command not yet available

    return {
        "status": "solidified",
        "name": tool.name,
        "display_name": tool.display_name,
        "revit_synced": revit_synced,
    }


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
    import logging
    _logger = logging.getLogger("mcp_bridge.router")

    dynamic_params = _tool_store.get_dynamic_params(name)
    if not dynamic_params:
        return {}

    async def _fetch_choices():
        client = await _get_revit_client()
        executor = RevitQueryExecutor(client)
        choices: dict[str, list[dict]] = {}

        for p in dynamic_params:
            source = p["choices_from"]
            items: list[dict] = []
            _logger.info(f"[choices] fetching {p['name']!r} from source={source!r}")

            if source == "levels":
                levels = await executor.get_levels()
                _logger.info(f"[choices] levels returned: {len(levels)} items, raw={levels[:2] if levels else 'EMPTY'}")
                items = [
                    {"label": f"{lv.get('Name', '?')} ({lv.get('ElevationMm', 0)}mm)",
                     "value": lv.get("Name", "")}
                    for lv in levels
                ]
            elif source.startswith("family_types:"):
                category = source.split(":", 1)[1]
                _logger.info(f"[choices] fetching family_types for category={category!r}")
                types = await executor.get_family_types([category])
                _logger.info(f"[choices] family_types returned: {len(types)} items")
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

            _logger.info(f"[choices] {p['name']!r}: {len(items)} final items")
            choices[p["name"]] = items

        return choices

    try:
        return await asyncio.wait_for(_fetch_choices(), timeout=15.0)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Revit query timed out (15s)")
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
    """Check if Revit plugin is reachable — returns rich connection info."""
    from datetime import datetime, timezone, timedelta
    from mcp_bridge.client_pool import RevitClientPool
    cfg = _get_bridge_config()
    tz_cn = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_cn).strftime("%Y-%m-%d %H:%M:%S")
    try:
        t0 = time.monotonic()
        client = await RevitClientPool.get_client(
            host=cfg.get("revit_host", "localhost"),
            port=cfg.get("revit_port", 18080),
            timeout=cfg.get("command_timeout", 60),
            connect_timeout=cfg.get("connect_timeout", 5),
        )
        resp = await client.send_command("say_hello", {"message": "ping"})
        latency = round((time.monotonic() - t0) * 1000)
        host = cfg.get("revit_host", "localhost")
        port = cfg.get("revit_port", 18080)
        return {
            "revit_connected": resp.success,
            "latency_ms": latency,
            "detail": resp.result if resp.success else resp.error,
            "bridge_version": "v0.2",
            "protocol": "JSON-RPC 2.0 / TCP",
            "endpoint": f"{host}:{port}",
            "timestamp": now_str,
        }
    except Exception as e:
        return {
            "revit_connected": False,
            "latency_ms": None,
            "detail": str(e),
            "bridge_version": "v0.2",
            "timestamp": now_str,
        }
