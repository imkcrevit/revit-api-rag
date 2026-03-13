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
import sys
from pathlib import Path

# Ensure project root is importable
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from mcp.server.fastmcp import FastMCP

from mcp_bridge.tool_store import ToolStore
from mcp_bridge.revit_client import RevitClient

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

mcp = FastMCP(
    "Revit-RAG-Bridge",
    version="1.0.0",
    description="RAG-powered Revit code generation, execution, and tool solidification",
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
    """Send C# code to Revit for execution via TCP socket (port 8080).
    Returns execution result or error message."""
    client = RevitClient()
    try:
        await client.connect()
        resp = await client.send_code(code, parameters)
        return json.dumps({
            "success": resp.success,
            "result": resp.result,
            "error": resp.error,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    finally:
        await client.disconnect()


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
async def run_tool(name: str, params: str = "{}") -> str:
    """Execute a solidified tool by name with given parameters.
    params: JSON object of parameter values, e.g. {"height": 3000}"""
    import json as _json
    try:
        param_dict = _json.loads(params) if params else {}
    except _json.JSONDecodeError:
        return json.dumps({"success": False, "error": f"Invalid params JSON: {params}"})

    code = _tool_store.render_code(name, param_dict)
    if code is None:
        return json.dumps({"success": False, "error": f"Tool '{name}' not found."})

    # Execute
    client = RevitClient()
    try:
        await client.connect()
        resp = await client.send_code(code)
        if resp.success:
            _tool_store.record_usage(name)
        return json.dumps({
            "success": resp.success,
            "tool": name,
            "result": resp.result,
            "error": resp.error,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    finally:
        await client.disconnect()


# ── Resource: API Stats ──────────────────────────────────────────────────────

@mcp.resource("revit://stats")
def api_stats() -> str:
    """Available knowledge base and tool statistics."""
    tools = _tool_store.list_tools()
    return (
        f"API docs: 27,596 entries | SDK examples: 153 | Revit 2026\n"
        f"Solidified tools: {len(tools)}"
    )


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
