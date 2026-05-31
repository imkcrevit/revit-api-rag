"""
FastAPI routes for MCP Bridge — alternative to MCP protocol for web UI access.

Prefix: /api/v1/bridge

Supports two Revit connection modes:
- TCP: direct socket to localhost (local dev / SSH tunnel)
- WebSocket: Revit plugin connects to server (multi-user remote)
  Frontend sends X-Slot-Id header to route to the correct Revit instance.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mcp_bridge.tool_store import ToolStore
from mcp_bridge.interactive import IntentClassifier, RevitQueryExecutor
from mcp_bridge import sandbox

# ── Slot context — set per-request by router dependency ──────────────────
_request_slot_id: ContextVar[str | None] = ContextVar("slot_id", default=None)


async def _set_slot_ctx(request: Request):
    """Extract X-Slot-Id from request header and store in ContextVar."""
    _request_slot_id.set(request.headers.get("x-slot-id"))


bridge_router = APIRouter(
    prefix="/api/v1/bridge",
    tags=["mcp-bridge"],
    dependencies=[Depends(_set_slot_ctx)],
)
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
    """Get Revit client — WebSocket slot if available, else TCP fallback."""
    slot_id = _request_slot_id.get(None)

    # Try WebSocket slot first
    if slot_id:
        from mcp_bridge.ws_relay import get_slot_manager, WebSocketRevitClient
        mgr = get_slot_manager()
        conn = mgr.get_connection(slot_id)
        if conn:
            cfg = _get_bridge_config()
            return WebSocketRevitClient(
                mgr, slot_id, timeout=cfg.get("command_timeout", 60),
            )

    # Fallback to TCP (local dev / SSH tunnel)
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
    selections: dict = Field(default_factory=dict)
    api_top_k: int = 15
    code_top_k: int = 5
    tool_context: dict | None = None


class ExecuteRequest(BaseModel):
    code: str
    parameters: list | None = None
    skip_review: bool = False


class SolidifyRequest(BaseModel):
    name: str
    code: str
    description: str = ""
    parameters: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_query: str = ""


class UpdateToolRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    code_template: str | None = None
    parameters: list[dict] | None = None
    tags: list[str] | None = None
    source_query: str | None = None
    preconditions: list[str] | None = None
    applies_when: list[str] | None = None
    not_for: list[str] | None = None


class ReviewCodeRequest(BaseModel):
    code: str


class RunToolRequest(BaseModel):
    name: str
    params: dict = Field(default_factory=dict)


class ClassifyRequest(BaseModel):
    query: str


class OrchestrateRequest(BaseModel):
    query: str
    session_id: str | None = None


class QueryRevitRequest(BaseModel):
    command: str
    params: dict = Field(default_factory=dict)


# -- API Explorer Routes -------------------------------------------------------

class ParameterizeRequest(BaseModel):
    code: str
    source_query: str = ""
    thinking: str = ""          # LLM thinking chain from code generation
    selections: dict = Field(default_factory=dict)  # user's interactive selections


class UnitSettingRequest(BaseModel):
    unit: str  # "mm", "m", or "feet"


class ApiSearchRequest(BaseModel):
    query: str
    top_k: int = 15
    fast: bool = False  # skip query rewriting for speed


class ApiCodeGenRequest(BaseModel):
    api_name: str
    api_context: str
    user_hint: str = ""


_intent_registry_cache: dict | None = None


def _get_intent_registry() -> dict:
    """Load intent registry for deterministic tool matching."""
    global _intent_registry_cache
    if _intent_registry_cache is None:
        import yaml
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "intent_bridge" / "schemas" / "intent_registry.yaml"
        )
        if path.exists():
            _intent_registry_cache = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            _intent_registry_cache = {}
    return _intent_registry_cache


def _match_tool_from_intent_registry(query: str):
    """Match saved tools by intent display names and mapped command names."""
    q = query.strip().lower()
    if not q:
        return None

    intents = _get_intent_registry().get("intents", {})

    def close_match(phrase: str) -> bool:
        p = phrase.strip().lower()
        if not p:
            return False
        if q == p:
            return True
        return p in q and len(q) - len(p) <= 3

    for intent_name, data in intents.items():
        mapped_commands = data.get("mapped_commands") or []
        phrases = [
            intent_name,
            data.get("display_name", ""),
            data.get("display_name_en", ""),
            data.get("description", ""),
            *data.get("keywords", []),
            *mapped_commands,
        ]
        if not any(close_match(str(p)) for p in phrases):
            continue

        for tool_name in [intent_name, *mapped_commands]:
            tool = _tool_store.load(str(tool_name))
            if tool:
                return tool
    return None


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

    param_code, parameters = gen.parameterize(
        req.code, req.source_query,
        thinking=req.thinking, selections=req.selections,
    )
    return {"code": param_code, "parameters": parameters}


@bridge_router.post("/api-search")
async def api_search(req: ApiSearchRequest):
    """Search Revit API docs via RAG — returns reranked results."""
    from server.app.deps import get_retriever
    retriever = get_retriever()
    results = retriever.search(
        req.query,
        api_top_k=req.top_k,
        code_top_k=3,
        rewrite=not req.fast,
    )

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
        content = item.content or ""
        # Detect incomplete code: if it lacks a closing brace or ends mid-line,
        # it was likely stored truncated — mark it so frontends can decide.
        is_complete = (
            not content
            or content.rstrip().endswith("}")
            or content.rstrip().endswith(";")
            or content.rstrip().endswith("*/")
        )
        sdk_items.append({
            "project": item.project,
            "summary": item.summary,
            "content": content if is_complete else "",
            "mentioned_apis": item.mentioned_apis,
            "distance": round(item.distance, 4),
            "is_complete": is_complete,
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

    from server.app.prompts.api_explorer import get_api_codegen_prompt
    system = get_api_codegen_prompt(req.api_context)
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
async def generate_code(req: GenerateRequest, request: Request):
    """RAG + LLM -> generate C# code for Revit execution."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator
    from server.app.log_store import get_log_store, get_client_ip

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit, config=config)

    t0 = time.time()
    code, meta = gen.generate(req.query, req.api_top_k, req.code_top_k)

    # Security review
    safe, warnings = sandbox.review(code)

    # Log interaction
    get_log_store().log(
        module="mcp_bridge",
        client_ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        user_input=req.query,
        assistant_output=code,
        duration_ms=int((time.time() - t0) * 1000),
        status="ok" if safe else "warning",
    )

    return {"code": code, "rag_context": meta, "safe": safe, "warnings": warnings}


@bridge_router.post("/generate-stream")
async def generate_code_stream(req: GenerateWithSelectionsRequest, request: Request):
    """RAG + LLM -> stream C# code generation via SSE. Supports optional selections."""
    from server.app.deps import get_retriever, get_config
    from pipeline.llm_client import create_llm_client
    from mcp_bridge.code_generator import CodeGenerator
    from server.app.log_store import get_log_store, get_client_ip

    retriever = get_retriever()
    config = get_config()
    llm = create_llm_client(config)
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit, config=config)
    selections = req.selections if req.selections else None
    _req_ip = get_client_ip(request)
    _req_ua = request.headers.get("user-agent", "")

    async def event_stream():
        def _p(msg: str):
            return f"event: progress\ndata: {json.dumps(msg)}\n\n"

        # ── Phase 0: Skills Extraction (Gemini Flash) ──
        yield _p("Skills extraction — scanning BIM standards with Gemini Flash...")
        skills_ctx = gen._build_skills_context(req.query)
        if skills_ctx:
            yield _p(f"Skills extracted — {len(skills_ctx)} chars of relevant BIM standards")
        else:
            yield _p("No relevant BIM standards found")

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

        # ── Phase 2.5: Tool context (reference from matched tool) ──
        tool_ref_ctx = ""
        if req.tool_context:
            tc = req.tool_context
            tool_ref_ctx = (
                f"\n## Reference Tool — \"{tc.get('name', '')}\" (user skipped direct execution)\n"
                f"The following code template was previously solidified for a similar task.\n"
                f"Use it as a REFERENCE for structure and API usage patterns, "
                f"but generate fresh code that fulfills the user's actual request.\n"
                f"```csharp\n{tc.get('code_template', '')}\n```\n"
            )
            if tc.get("parameters"):
                tool_ref_ctx += "Tool parameters:\n"
                for p in tc["parameters"]:
                    tool_ref_ctx += f"  - {p.get('name')}: {p.get('type', 'string')} — {p.get('description', '')}\n"
            yield _p(f"Tool reference loaded — {tc.get('name', '?')} ({len(tool_ref_ctx)} chars)")

        yield _p("Assembling system prompt (rules + context + unit config + skills)...")
        from mcp_bridge.code_generator import SYSTEM_EXECUTE, CodeGenerator as CG
        selections_ctx = CG._build_selections_context(selections) if selections else ""
        system = SYSTEM_EXECUTE.format(
            revit_version=gen.revit_version,
            api_context=ctx.get("api_context", "(none)"),
            code_context=ctx.get("code_context", "(none)") + tool_ref_ctx,
            selections_context=selections_ctx,
            unit_context=CG.UNIT_CONTEXTS.get(gen.user_unit, CG.UNIT_CONTEXTS["mm"]),
            skills_context=skills_ctx,
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

        # Log the interaction
        get_log_store().log(
            module="mcp_bridge_stream",
            client_ip=_req_ip,
            user_agent=_req_ua,
            user_input=req.query,
            assistant_output=code,
            status="ok" if safe else "warning",
        )

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
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit, config=config)

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


@bridge_router.post("/review-code")
async def review_code(req: ReviewCodeRequest):
    """Run the same static security review used before Revit execution."""
    safe, warnings = sandbox.review(req.code)
    return {"safe": safe, "warnings": warnings}


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
    gen = CodeGenerator(retriever, llm, user_unit=_user_unit, config=config)
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


@bridge_router.put("/tools/{name}")
async def update_tool(name: str, req: UpdateToolRequest):
    """Update editable metadata and code for an existing solidified tool."""
    updates = req.model_dump(exclude_unset=True)
    if "code_template" in updates:
        safe, warnings = sandbox.review(updates["code_template"] or "")
        if not safe:
            raise HTTPException(
                422,
                f"Code review failed: {'; '.join(warnings)}",
            )

    tool = _tool_store.update(name, updates)
    if not tool:
        raise HTTPException(404, f"Tool '{name}' not found")

    # Best-effort sync; the saved YAML remains the source of truth.
    revit_synced = False
    try:
        client = await _get_revit_client()
        resp = await client.send_command("manage_solidified_tools", {
            "action": "register",
            "name": tool.name,
            "code_template": tool.code_template,
            "description": tool.description,
            "parameters": tool.parameters,
            "source_query": tool.source_query,
        })
        revit_synced = resp.success
    except Exception:
        pass

    return {
        "status": "updated",
        "name": tool.name,
        "display_name": tool.display_name,
        "description": tool.description,
        "code_template": tool.code_template,
        "parameters": tool.parameters,
        "tags": tool.tags,
        "source_query": tool.source_query,
        "execution_count": tool.execution_count,
        "revit_synced": revit_synced,
    }


@bridge_router.post("/tools/{name}/run")
async def run_tool(name: str, req: RunToolRequest):
    """Execute a solidified tool with parameters."""
    tool = _tool_store.load(name)
    if not tool:
        raise HTTPException(404, f"Tool '{name}' not found")

    valid, errors, filled = _tool_store.validate_params(name, req.params)
    if not valid:
        raise HTTPException(
            422, f"Parameter validation failed: {'; '.join(errors)}"
        )

    code = tool.code_template
    for k, v in filled.items():
        code = code.replace(f"{{{k}}}", str(v))

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

    def _extract_type_name(item: dict) -> str:
        """Extract type name from Revit response — handles all known key formats."""
        return (item.get("TypeName")
                or item.get("typeName")
                or item.get("name")
                or item.get("Name")
                or str(item))

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
                    {"label": str(lv.get("Name", "?")),
                     "value": lv.get("Name", "")}
                    for lv in levels
                ]
            elif source.startswith("family_types:"):
                category = source.split(":", 1)[1]
                _logger.info(f"[choices] fetching family_types for category={category!r}")
                types = await executor.get_family_types([category])
                _logger.info(f"[choices] family_types returned: {len(types)} items")
                items = [
                    {"label": _extract_type_name(t),
                     "value": _extract_type_name(t)}
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
                        {"label": _extract_type_name(ft),
                         "value": _extract_type_name(ft)}
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


@bridge_router.post("/match-tool")
async def match_tool(req: ClassifyRequest):
    """Check if an existing solidified tool matches the user query.

    Returns matched tool info with parameter validation, or null if no match.
    """
    tool = _match_tool_from_intent_registry(req.query) or _tool_store.match_tool(req.query)
    if not tool:
        return {"matched": False}
    # Check if tool has usable parameters
    has_params = bool(tool.parameters)
    has_dynamic = any("choices_from" in p for p in tool.parameters)
    return {
        "matched": True,
        "name": tool.name,
        "display_name": tool.display_name,
        "description": tool.description,
        "parameters": tool.parameters,
        "has_params": has_params,
        "has_dynamic_params": has_dynamic,
        "execution_count": tool.execution_count,
    }


# -- Interactive Selection Routes ----------------------------------------------

@bridge_router.post("/classify-intent")
async def classify_intent(req: ClassifyRequest):
    """Classify user query to determine if interactive selection is needed."""
    result = _classifier.classify(req.query)
    return result


# -- Orchestrator (Intent Bridge integration) ---------------------------------

import logging as _logging
import uuid as _uuid

_orch_log = _logging.getLogger("mcp_bridge.orchestrate")


@dataclass
class _OrchSessionEntry:
    """Mutable orchestrator session guarded by one FIFO asyncio lock."""
    orch: Any
    session: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    queue_depth: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


_orch_sessions: dict[str, _OrchSessionEntry] = {}


def _get_orch_queue_timeout() -> float:
    return float(_get_bridge_config().get("orchestrate_queue_timeout", 20))


def _get_orch_turn_timeout() -> float:
    return float(_get_bridge_config().get("orchestrate_turn_timeout", 90))


def _get_orch_session_ttl() -> float:
    return float(_get_bridge_config().get("orchestrate_session_ttl", 900))


def _cleanup_orch_sessions(now: float | None = None) -> None:
    """Drop stale, idle sessions so abandoned queues cannot linger forever."""
    ts = now or time.time()
    ttl = _get_orch_session_ttl()
    stale = [
        sid for sid, entry in _orch_sessions.items()
        if not entry.lock.locked() and ts - entry.updated_at > ttl
    ]
    for sid in stale:
        _orch_sessions.pop(sid, None)


async def _acquire_orch_lock(
    sid: str,
    entry: _OrchSessionEntry,
    timeout: float | None = None,
) -> float:
    """Queue behind an in-flight session turn and fail fast if the queue stalls."""
    entry.queue_depth += 1
    start = time.monotonic()
    try:
        await asyncio.wait_for(
            entry.lock.acquire(),
            timeout=timeout if timeout is not None else _get_orch_queue_timeout(),
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            409,
            f"Agent session '{sid}' is busy; queued request timed out.",
        )
    finally:
        entry.queue_depth = max(0, entry.queue_depth - 1)
    return (time.monotonic() - start) * 1000


async def _process_orch_with_rollback(
    sid: str,
    entry: _OrchSessionEntry,
    query: str,
    *,
    is_new: bool,
    timeout: float | None = None,
):
    """Run one orchestrator turn with state rollback on error/cancellation."""
    snapshot = entry.session.model_copy(deep=True)
    try:
        resp = await asyncio.wait_for(
            entry.orch.process_turn(query, entry.session),
            timeout=timeout if timeout is not None else _get_orch_turn_timeout(),
        )
        entry.updated_at = time.time()
        return resp
    except asyncio.TimeoutError:
        if is_new:
            _orch_sessions.pop(sid, None)
        else:
            entry.session = snapshot
        _orch_log.warning("[orchestrate] sid=%s rolled back after turn timeout", sid)
        raise HTTPException(504, "Agent turn timed out and session state was rolled back.")
    except asyncio.CancelledError:
        if is_new:
            _orch_sessions.pop(sid, None)
        else:
            entry.session = snapshot
        _orch_log.warning("[orchestrate] sid=%s rolled back after cancellation", sid)
        raise
    except Exception as e:
        if is_new:
            _orch_sessions.pop(sid, None)
        else:
            entry.session = snapshot
        _orch_log.error("[orchestrate] sid=%s rolled back after failure: %s", sid, e)
        raise HTTPException(500, str(e))

# ── Enrichment config (loaded from YAML) ──
def _load_enrichment_config() -> dict:
    """Load enrichment rules from intent_bridge/schemas/enrichment_rules.yaml."""
    import yaml
    from pathlib import Path
    config_path = (Path(__file__).resolve().parent.parent
                   / "intent_bridge" / "schemas" / "enrichment_rules.yaml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

_enrichment_config: dict | None = None

def _get_enrichment_config() -> dict:
    global _enrichment_config
    if _enrichment_config is None:
        _enrichment_config = _load_enrichment_config()
    return _enrichment_config


def _infer_enrich(slot: str, q_text: str, category_aliases: dict) -> str:
    """Fallback: infer enrichment type from slot name + question text.

    Uses lightweight rules from enrichment_rules.yaml inference section.
    Returns enrich string like 'level', 'host_pick', 'family_type:window', or 'none'.
    """
    sl = slot.lower()
    qt = q_text.lower()
    combined = f"{sl} {qt}"

    # Host/element pick: slot name contains host, pick, 宿主
    # (do NOT match on question text alone — "选择" is too common)
    if any(kw in sl for kw in ("host", "pick", "宿主")):
        return "host_pick"
    # Question explicitly asks for ElementId
    if "elementid" in qt:
        return "host_pick"

    # Level: slot contains level, 标高, 楼层
    if any(kw in sl for kw in ("level", "标高", "楼层")):
        return "level"

    # Family type: slot contains type, family, symbol, 族, 类型
    # BUT exclude boolean-like slots (structural_type where question asks yes/no)
    is_type_slot = any(kw in sl for kw in ("type", "family", "symbol", "族", "类型"))
    if is_type_slot:
        # Check if question looks boolean (yes/no, 是/否, structural/non-structural)
        bool_indicators = ("是否", "yes/no", "true/false", "是/否",
                           "structural/", "non-structural", "/否")
        if any(bi in qt for bi in bool_indicators):
            return "none"

        # Infer category from slot name + question text
        for alias in category_aliases:
            if alias in combined:
                return f"family_type:{alias}"

    return "none"


async def _enrich_orch_questions(
    questions: list[dict], intent: dict | None
) -> list[dict]:
    """Enrich orchestrator questions with real Revit data.

    Uses the LLM-tagged `enrich` field on each question to decide:
    - "family_type:<category>" → query Revit family types
    - "level"                  → query Revit levels
    - "host_pick"              → mark for Revit interactive pick
    - "none" / missing         → skip
    """
    try:
        client = await _get_revit_client()
    except (ConnectionError, OSError):
        _orch_log.info("[enrich] Revit not connected, skipping enrichment")
        return questions

    config = _get_enrichment_config()
    category_aliases = config.get("category_aliases", {})
    fmt = config.get("family_type_format", "{FamilyName}: {TypeName}")
    executor = RevitQueryExecutor(client)

    valid_categories_by_lower = {
        str(category).lower(): category
        for category in category_aliases.values()
    }

    for q in questions:
        raw_enrich = str(q.get("enrich", "")).strip()
        enrich_key = raw_enrich.lower()
        slot = q.get("slot", "")
        q_text = q.get("text", "").lower()

        # ── Fallback: if LLM didn't tag enrich, infer from slot name + question text ──
        if not enrich_key or enrich_key == "none":
            raw_enrich = _infer_enrich(slot, q_text, category_aliases)
            enrich_key = raw_enrich.lower()
            if enrich_key != "none":
                _orch_log.info(f"[enrich] slot={slot} → inferred enrich='{raw_enrich}' (LLM didn't tag)")

        if enrich_key == "none":
            _orch_log.info(f"[enrich] slot={slot} → skipped (no enrichment needed)")
            continue

        # ── host_pick: mark for Revit interactive selection ──
        if enrich_key == "host_pick":
            q["_pick_mode"] = True
            q["allow_custom"] = True
            q["options"] = []
            q["values"] = []
            _orch_log.info(f"[enrich] slot={slot} → Revit pick mode")
            continue

        # ── level: query real Revit levels ──
        if enrich_key == "level":
            try:
                levels = await executor.get_levels()
                if levels:
                    q["options"] = [str(lv.get("Name", "?")) for lv in levels]
                    q["values"] = [lv.get("Name", "") for lv in levels]
                    q["allow_custom"] = True
                    _orch_log.info(f"[enrich] slot={slot} → {len(levels)} levels from Revit")
            except Exception as e:
                _orch_log.warning(f"[enrich] level query failed for slot={slot}: {e}")
            continue

        # ── family_type:<category>: query real Revit family types ──
        if enrich_key.startswith("family_type:"):
            alias = raw_enrich.split(":", 1)[1].strip()
            alias_key = alias.lower()
            category = category_aliases.get(alias_key) or category_aliases.get(alias)
            if not category:
                # Try direct OST_ value
                if alias.startswith("OST_"):
                    category = alias
                else:
                    category = valid_categories_by_lower.get(alias_key)
            if not category:
                _orch_log.warning(
                    f"[enrich] slot={slot} unknown category alias '{alias}', "
                    f"known: {list(category_aliases.keys())}")
                continue

            try:
                types = await executor.get_family_types([category])
                if types:
                    options = []
                    values = []
                    for t in types:
                        fname = t.get("FamilyName", t.get("familyName", ""))
                        tname = (t.get("TypeName") or t.get("typeName")
                                 or t.get("Name") or t.get("name", ""))
                        label = fmt.format(FamilyName=fname, TypeName=tname)
                        options.append(label)
                        values.append(label)
                    q["options"] = options
                    q["values"] = values
                    q["allow_custom"] = True
                    _orch_log.info(
                        f"[enrich] slot={slot} category={category} "
                        f"→ {len(types)} types from Revit"
                    )
            except Exception as e:
                _orch_log.warning(f"[enrich] family_types query failed for slot={slot}: {e}")
            continue

        _orch_log.warning(f"[enrich] slot={slot} unknown enrich type: '{raw_enrich}'")

    return questions


@bridge_router.post("/orchestrate")
async def orchestrate(req: OrchestrateRequest):
    """Full LLM intent analysis via Intent Bridge orchestrator.

    First call: provide query, get back questions for user.
    Follow-up calls: provide session_id + user answer, get next question or completion.
    """
    _cleanup_orch_sessions()

    try:
        from intent_bridge.llm_adapter import LLMAdapter
        from intent_bridge.slot_engine import ConversationOrchestrator
        from intent_bridge.models import SessionState
    except ImportError as e:
        raise HTTPException(500, f"Intent Bridge not available: {e}")

    is_new = False
    if req.session_id and req.session_id in _orch_sessions:
        # Continue existing session
        entry = _orch_sessions[req.session_id]
        sid = req.session_id
    else:
        # New session
        llm = LLMAdapter()
        orch = ConversationOrchestrator(llm=llm)
        session = SessionState()
        sid = str(_uuid.uuid4())[:8]
        entry = _OrchSessionEntry(orch=orch, session=session)
        _orch_sessions[sid] = entry
        is_new = True

    queued_ms = await _acquire_orch_lock(sid, entry)
    try:
        if _orch_sessions.get(sid) is not entry:
            raise HTTPException(
                409,
                f"Agent session '{sid}' is no longer active; queued request was discarded.",
            )

        resp = await _process_orch_with_rollback(
            sid,
            entry,
            req.query,
            is_new=is_new,
        )
        session = entry.session

        # Collect ALL questions (current + remaining pending)
        # NOTE: current_question comes from peek_question() which does NOT pop,
        # so pending_questions[0] IS current_question — skip it to avoid duplicates.
        all_questions = []
        if resp.current_question:
            all_questions.append(resp.current_question.model_dump())
            # pending_questions[1:] = remaining (skip [0] which is current_question)
            for q in session.pending_questions[1:]:
                all_questions.append(q.model_dump())
        else:
            for q in session.pending_questions:
                all_questions.append(q.model_dump())

        # Include action_plan from session for composite intents
        action_plan_data = []
        for step in session.action_plan:
            action_plan_data.append(step.model_dump())

        # ── Enrich questions with real Revit data ──
        # Replace LLM-fabricated options with actual Revit family types / levels
        if all_questions:
            _orch_log.info(f"[orchestrate] enriching {len(all_questions)} questions, "
                           f"slots: {[q.get('slot') for q in all_questions]}")
            try:
                all_questions = await _enrich_orch_questions(all_questions, resp.intent)
            except Exception as e:
                _orch_log.warning(f"[orchestrate] enrich failed (non-fatal): {e}")

        result = {
            "session_id": sid,
            "status": resp.status.value,
            "intent": resp.intent,
            "slots": resp.slots,
            "questions": all_questions,
            "action_plan": action_plan_data,
            "summary": resp.summary,
            "queue": {
                "wait_ms": int(queued_ms),
                "pending": entry.queue_depth,
                "rollback": "enabled",
            },
        }
        _orch_log.info(f"[orchestrate] sid={sid} status={resp.status.value} "
                       f"questions={len(all_questions)} slots={list(resp.slots.keys())} "
                       f"queued_ms={queued_ms:.0f}")

        # Cleanup completed sessions
        if resp.status.value in ("complete", "cancelled", "constraint_error"):
            _orch_sessions.pop(sid, None)

        return result
    finally:
        if entry.lock.locked():
            entry.lock.release()


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
            "bridge_version": "v0.3",
            "protocol": "JSON-RPC 2.0 / TCP",
            "endpoint": f"{host}:{port}",
            "timestamp": now_str,
            "mode": "tcp",
        }
    except Exception as e:
        # Check if any WebSocket slots are connected
        from mcp_bridge.ws_relay import get_slot_manager
        slot_status = get_slot_manager().get_status()
        return {
            "revit_connected": slot_status["connected"] > 0,
            "latency_ms": None,
            "detail": f"TCP: {e}" if slot_status["connected"] == 0 else f"TCP offline, {slot_status['connected']} WebSocket slot(s) connected",
            "bridge_version": "v0.3",
            "timestamp": now_str,
            "mode": "websocket" if slot_status["connected"] > 0 else "disconnected",
            "ws_slots": slot_status,
        }


# -- WebSocket Slot Routes (multi-user Revit connections) ----------------------

@bridge_router.websocket("/ws/{slot_id}")
async def revit_ws_endpoint(ws: WebSocket, slot_id: str):
    """WebSocket endpoint for Revit plugin connections.

    Revit plugin connects here as a client, registering on a slot.
    Server sends JSON-RPC requests, plugin sends JSON-RPC responses.
    """
    from mcp_bridge.ws_relay import get_slot_manager
    import logging
    _ws_log = logging.getLogger("mcp_bridge.ws")

    mgr = get_slot_manager()

    # Validate slot_id
    if not slot_id.isdigit() or int(slot_id) < 1 or int(slot_id) > mgr.max_slots:
        await ws.close(code=4001, reason=f"Invalid slot_id. Use 1-{mgr.max_slots}")
        return

    await ws.accept()

    if not mgr.register(slot_id, ws):
        await ws.send_text(json.dumps({"error": f"Slot {slot_id} already occupied"}))
        await ws.close(code=4002, reason="Slot occupied")
        return

    _ws_log.info(f"Revit plugin connected on slot {slot_id}")
    try:
        while True:
            data = await ws.receive_text()
            # All incoming messages are responses from Revit
            mgr.resolve_response(slot_id, data)
    except WebSocketDisconnect:
        _ws_log.info(f"Revit plugin disconnected from slot {slot_id}")
    except Exception as e:
        _ws_log.warning(f"Slot {slot_id} WebSocket error: {e}")
    finally:
        mgr.unregister(slot_id)


@bridge_router.get("/slots")
async def get_slots():
    """Return status of all connection slots."""
    from mcp_bridge.ws_relay import get_slot_manager
    return get_slot_manager().get_status()
