"""
MCP Server — exposes RAG search + code execution + solidified tools.

Usage:
    Standalone:  python -m mcp_bridge.mcp_server
    Dev mode:    mcp dev mcp_bridge/mcp_server.py

Provides tools:
    - search_revit_api    : RAG semantic search on 27,596 API docs
    - get_code_examples   : retrieve SDK golden code samples
    - generate_code       : RAG + LLM → C# code for Revit execution
    - execute_code        : send C# code to Revit via TCP socket
    - solidify_tool       : save successful code as reusable named tool
    - list_tools          : list all solidified tools
    - run_tool            : execute a solidified tool with parameters
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

# Ensure project root is importable
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from mcp.server.fastmcp import FastMCP

from mcp_bridge.tool_store import ToolStore
from mcp_bridge.client_pool import RevitClientPool

# Lazy-loaded singletons
_retriever = None
_llm = None
_tool_store = ToolStore()


def _get_retriever():
    global _retriever
    if _retriever is None:
        from server.app.deps import get_retriever
        _retriever = get_retriever()
    return _retriever


def _get_llm():
    global _llm
    if _llm is None:
        from config import load_config
        from pipeline.llm_client import create_llm_client
        _llm = create_llm_client(load_config())
    return _llm


# ── MCP Server ───────────────────────────────────────────────────────────────

SERVER_INSTRUCTIONS = """\
You are a Revit API assistant powered by the Revit-RAG-Bridge MCP server.
You have access to the following capabilities:

## Available Tools

1. **search_revit_api** — Semantic search across 27,596 Revit 2026 API documentation entries.
   Returns method signatures, parameters, return types, and usage notes.

2. **get_code_examples** — Retrieve real C# code samples from the official Revit SDK.
   These are golden examples that demonstrate correct API usage patterns.

3. **generate_code** — Generate executable C# code for Revit using RAG context.
   Combines API docs + SDK examples + LLM to produce ready-to-run code.

4. **execute_code** — Send C# code to a running Revit instance for execution via TCP (port 18080).
   The code runs inside Revit's ExternalEvent handler with full API access.

5. **solidify_tool** — Save a successfully executed code snippet as a reusable named tool.
   Tools are stored as YAML with parameterized code templates.

6. **list_tools** — List all solidified (saved) tools available for execution.

7. **run_tool** — Execute a previously solidified tool by name, filling in parameter values.

8. **get_tool_choices** — Query Revit for dynamic parameter values (levels, family types, etc.).
   MUST be called BEFORE run_tool for parameters with `source: query:*`.

## Recommended Workflow

1. **Search** — Use `search_revit_api` to find relevant API classes and methods.
2. **Examples** — Use `get_code_examples` to see how the SDK uses those APIs.
3. **Generate** — Use `generate_code` to create C# code for the user's request.
4. **Execute** — Use `execute_code` to run the code in Revit and verify it works.
5. **Solidify** — If execution succeeds, use `solidify_tool` to save it for reuse.
6. **Reuse** — Next time, use `get_tool_choices` → `run_tool` to execute saved tools.

## Parameter Source Protocol (CRITICAL — prevents silent failures)

Every solidified tool parameter has a `source` field declaring where its value comes from.
You MUST respect these sources:

- **`source: query:levels`** — Call `get_tool_choices` first. NEVER guess level names.
- **`source: query:*_types`** — Call `get_tool_choices` first. NEVER assume type names.
- **`source: interactive:pick_object`** — User must select in Revit. NEVER fabricate IDs.
- **`source: ask_user`** — You MUST ask the user. Do not guess or assume.
- **`source: default`** — Use the declared default value.

### Recognize Your Own Rationalizations
You will feel the urge to skip querying and just fill in a value. Resist it:
- "Level 1 is standard" — the project may use "1F", "B1", or custom names. **Query.**
- "Generic - 200mm is common" — it may not be loaded in this project. **Query.**
- "I'll use (0,0,0)" — the user never said that. **Ask.**
- "This parameter is probably optional" — if the API requires it, it's not. **Ask.**

## Execution Verification (FAITHFUL REPORTING)

After `execute_code` or `run_tool`:
- Report faithfully. If it failed, say so with the actual error message.
- Do NOT claim success when the response shows an error.
- Do NOT paraphrase errors — include the actual error text.
- If a tool fails 2+ times, fall back to `generate_code` with fresh RAG — the tool is stale.

## Resources

- `revit://stats` — Knowledge base and tool statistics.
- `revit://tools/{name}` — View the YAML definition of a solidified tool.
- `revit://connection-status` — Check whether Revit is connected and reachable.

## Notes

- All generated code targets **Revit 2026** APIs.
- The Revit plugin listens on **localhost:18080** (TCP, JSON-RPC 2.0).
- Code executes inside a Revit `ExternalEvent` handler — it has full access to
  `UIApplication`, `Document`, and all Revit API namespaces.
- Always prefer searching the API docs before generating code to ensure correct
  class names, method signatures, and parameter types.
"""

mcp = FastMCP(
    "Revit-RAG-Bridge",
    version="1.0.0",
    description="RAG-powered Revit code generation, execution, and tool solidification",
    instructions=SERVER_INSTRUCTIONS,
)


# ── RAG Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def search_revit_api(query: str, top_k: int = 10) -> str:
    """Search Revit API documentation (27,596 entries).
    Returns method signatures, parameters, and usage notes."""
    retriever = _get_retriever()
    results = retriever.search(query, api_top_k=top_k, code_top_k=0)
    ctx = retriever.build_context(results)
    return ctx.get("api_context", "No results found.")


@mcp.tool()
def get_code_examples(query: str, top_k: int = 3) -> str:
    """Get Revit SDK golden code examples matching the query.
    Returns real C# code from official SDK samples."""
    retriever = _get_retriever()
    results = retriever.search(query, api_top_k=0, code_top_k=top_k)
    ctx = retriever.build_context(results)
    return ctx.get("code_context", "No examples found.")


@mcp.tool()
def generate_code(user_request: str) -> str:
    """Generate executable C# code for Revit using RAG context.
    The code is designed to run inside send_code_to_revit."""
    from mcp_bridge.code_generator import CodeGenerator
    gen = CodeGenerator(_get_retriever(), _get_llm())
    code, meta = gen.generate(user_request)
    return json.dumps({
        "code": code,
        "rag_context": meta,
    }, ensure_ascii=False, indent=2)


# ── Execution Tools ──────────────────────────────────────────────────────────

@mcp.tool()
async def execute_code(code: str, parameters: list | None = None) -> str:
    """Send C# code to Revit for execution via TCP socket (port 18080).
    Returns execution result or error message."""
    try:
        client = await RevitClientPool.get_client()
        resp = await client.send_code(code, parameters)
        return json.dumps({
            "success": resp.success,
            "result": resp.result,
            "error": resp.error,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Solidification Tools ─────────────────────────────────────────────────────

@mcp.tool()
def solidify_tool(
    name: str,
    code: str,
    description: str = "",
    parameters: str = "[]",
    tags: str = "",
    source_query: str = "",
) -> str:
    """Save a successful code execution as a reusable named tool.
    parameters: JSON array of {name, type, description, default?}
    tags: comma-separated tags"""
    import json as _json
    try:
        params = _json.loads(parameters) if parameters else []
    except _json.JSONDecodeError:
        params = []

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    tool = _tool_store.solidify(
        name=name,
        code=code,
        description=description,
        parameters=params,
        tags=tag_list,
        source_query=source_query,
    )
    return f"Tool '{tool.name}' solidified successfully. Saved to {_tool_store._tool_path(name)}"


@mcp.tool()
def list_tools() -> str:
    """List all solidified tools available for execution."""
    tools = _tool_store.list_tools()
    if not tools:
        return "No solidified tools yet. Use solidify_tool to save successful code."
    lines = []
    for t in tools:
        params_str = ", ".join(p.get("name", "?") for p in t.parameters) if t.parameters else "none"
        lines.append(f"- {t.name}: {t.description} (params: {params_str}, used: {t.execution_count}x)")
    return "\n".join(lines)


@mcp.tool()
async def get_tool_choices(name: str) -> str:
    """Query Revit for dynamic parameter choices of a solidified tool.
    Call this BEFORE run_tool to discover available levels, family types, etc.
    Returns {param_name: [{label, value}, ...]} for parameters that need selection."""
    from mcp_bridge.interactive import RevitQueryExecutor
    dynamic_params = _tool_store.get_dynamic_params(name)
    if not dynamic_params:
        return json.dumps({"message": f"Tool '{name}' has no dynamic parameters"})

    try:
        client = await RevitClientPool.get_client()
        executor = RevitQueryExecutor(client)
        choices: dict[str, list[dict]] = {}

        for p in dynamic_params:
            source = p["choices_from"]
            items: list[dict] = []

            if source == "levels":
                levels = await executor.get_levels()
                items = [{"label": f"{lv.get('Name','?')} ({lv.get('ElevationMm',0)}mm)",
                          "value": lv.get("Name", "")} for lv in levels]
            elif source.startswith("family_types:"):
                category = source.split(":", 1)[1]
                types = await executor.get_family_types([category])
                items = [{"label": t.get("name", t.get("Name", str(t))),
                          "value": t.get("name", t.get("Name", str(t)))} for t in types]
            elif source == "floor_types":
                code = ('var types = new FilteredElementCollector(document)\n'
                        '    .OfClass(typeof(FloorType)).Cast<FloorType>()\n'
                        '    .Select(ft => new { Name = ft.Name, Id = ft.Id.Value }).ToList();\n'
                        'return types;')
                resp = await client.send_code(code)
                if resp.success and resp.result:
                    data = resp.result if isinstance(resp.result, list) else [resp.result]
                    items = [{"label": ft.get("Name", str(ft)),
                              "value": ft.get("Name", str(ft))} for ft in data]
            elif source.startswith("elements:"):
                category = source.split(":", 1)[1]
                code = (f'var elems = new FilteredElementCollector(document)\n'
                        f'    .OfCategory(BuiltInCategory.{category})\n'
                        f'    .WhereElementIsNotElementType()\n'
                        f'    .Select(e => new {{ Id = e.Id.Value, Name = e.Name }}).ToList();\n'
                        f'return elems;')
                resp = await client.send_code(code)
                if resp.success and resp.result:
                    data = resp.result if isinstance(resp.result, list) else [resp.result]
                    items = [{"label": f"{el.get('Name','?')} (ID: {el.get('Id','?')})",
                              "value": el.get("Id", "")} for el in data]

            choices[p["name"]] = items

        return json.dumps(choices, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
async def run_tool(name: str, params: str = "{}") -> str:
    """Execute a solidified tool by name with given parameters.
    IMPORTANT: Call get_tool_choices first for parameters with source: query:*.
    params: JSON object of parameter values, e.g. {"level_name": "L1", "height": 3000}"""
    import json as _json
    try:
        param_dict = _json.loads(params) if params else {}
    except _json.JSONDecodeError:
        return json.dumps({"success": False, "error": f"Invalid params JSON: {params}"})

    # Health check — warn if tool is stale or failing
    health = _tool_store.health_check(name)
    if health["status"] == "not_found":
        return json.dumps({"success": False, "error": f"Tool '{name}' not found."})
    if health["recommendation"] == "fallback_to_rag":
        return json.dumps({
            "success": False,
            "error": f"Tool '{name}' is unhealthy: {'; '.join(health['issues'])}. "
                     f"Use generate_code instead for fresh RAG-based code generation.",
            "health": health,
        }, ensure_ascii=False, indent=2)

    code = _tool_store.render_code(name, param_dict)
    if code is None:
        valid, errors, _ = _tool_store.validate_params(name, param_dict)
        return json.dumps({
            "success": False,
            "error": f"Parameter validation failed: {'; '.join(errors)}",
        }, ensure_ascii=False, indent=2)

    # Execute via client pool
    try:
        client = await RevitClientPool.get_client()
        resp = await client.send_code(code)
        _tool_store.record_usage(name, success=resp.success)
        result = {
            "success": resp.success,
            "tool": name,
            "result": resp.result,
            "error": resp.error,
        }
        if not resp.success:
            result["hint"] = (
                "If this tool fails repeatedly, use generate_code with fresh RAG "
                "context instead — the tool definition may be outdated."
            )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        _tool_store.record_usage(name, success=False)
        return json.dumps({"success": False, "error": str(e)})


# ── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("revit://stats")
def api_stats() -> str:
    """Available knowledge base and tool statistics."""
    tools = _tool_store.list_tools()
    return (
        f"API docs: 27,596 entries | SDK examples: 153 | Revit 2026\n"
        f"Solidified tools: {len(tools)}"
    )


@mcp.resource("revit://tools/{name}")
def tool_resource(name: str) -> str:
    """Returns the YAML definition of a solidified tool by name."""
    safe_name = re.sub(r"[^\w\-]", "_", name)
    tool_path = _tool_store._tool_path(safe_name)
    if not tool_path.exists():
        return json.dumps({"error": f"Tool '{name}' not found."})
    return tool_path.read_text(encoding="utf-8")


@mcp.resource("revit://connection-status")
async def connection_status() -> str:
    """Check whether the Revit plugin is reachable on localhost:18080."""
    reachable = await RevitClientPool.ping()
    status = {
        "host": "localhost",
        "port": 18080,
        "reachable": reachable,
        "status": "connected" if reachable else "disconnected",
    }
    return json.dumps(status, indent=2)


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
