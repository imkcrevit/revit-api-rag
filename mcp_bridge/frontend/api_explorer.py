"""
Gradio Tab — API Explorer: Search Revit API docs → Rerank → Generate code examples.

Flow:
  Step 1: User types keyword (e.g. "Part", "Wall.Create")
  Step 2: RAG search + rerank → display ranked API results
  Step 3: User selects an API → LLM generates code example
"""
from __future__ import annotations

import logging
import httpx
import gradio as gr

logger = logging.getLogger("mcp_bridge.api_explorer")


# ---------------------------------------------------------------------------
# Direct retriever access (avoids self-HTTP deadlock on same port)
# ---------------------------------------------------------------------------

def _search_direct(query: str, top_k: int = 15, fast: bool = False) -> dict:
    """Search via retriever directly — no HTTP round-trip."""
    try:
        from server.app.deps import get_retriever
        retriever = get_retriever()
        results = retriever.search(
            query,
            api_top_k=top_k,
            code_top_k=3,
            rewrite=not fast,
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
    except Exception as e:
        logger.error(f"[_search_direct] {e}", exc_info=True)
        return {"error": str(e), "api_items": [], "sdk_items": []}


def _generate_example(api_name: str, api_context: str, hint: str = "") -> dict:
    """Code generation uses HTTP (long-running LLM call, won't deadlock)."""
    try:
        resp = httpx.post(
            "http://127.0.0.1:7860/api/v1/bridge/api-codegen",
            json={"api_name": api_name,
                  "api_context": api_context,
                  "user_hint": hint},
            timeout=120,
        )
        return resp.json()
    except Exception as e:
        return {"code": "", "error": str(e)}


# ---------------------------------------------------------------------------
# HTML table builder
# ---------------------------------------------------------------------------

def _build_results_dataframe(api_items: list[dict]) -> list[list]:
    """Build dataframe rows for search results."""
    rows = []
    for i, item in enumerate(api_items):
        name = item.get("name", "?")
        summary = (item.get("summary") or "")[:120]
        dist = item.get("distance", 0)
        rows.append([i + 1, name, summary, f"{dist:.4f}"])
    return rows


def _build_sdk_md(sdk_items: list[dict]) -> str:
    """Build markdown for SDK examples."""
    if not sdk_items:
        return "No SDK examples found."
    parts = []
    for si in sdk_items:
        proj = si.get("project", "?")
        content = si.get("content", "")[:400]
        raw_apis = si.get("mentioned_apis", "")
        apis = raw_apis[:200] if isinstance(raw_apis, str) else ", ".join(raw_apis[:5])
        parts.append(f"**{proj}** (APIs: {apis})\n```csharp\n{content}\n```\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tab builder
# ---------------------------------------------------------------------------

def create_api_explorer_tab():
    """Create API Explorer tab contents."""

    gr.Markdown("### Revit API Explorer — Search → Rerank → Generate")

    # Step 1: Search
    with gr.Row():
        search_input = gr.Textbox(
            placeholder="Type API keyword or describe what you need (中文/English): "
                        "Part, Wall.Create, 创建墙体, 获取房间面积...",
            show_label=False, scale=5, lines=1, max_lines=1,
        )
        search_btn = gr.Button("Search API", variant="primary", scale=1)
    with gr.Row():
        search_mode = gr.Radio(
            choices=["Fast (embedding only)", "Full (rewrite + rerank)"],
            value="Full (rewrite + rerank)",
            label="Search Mode", scale=3,
        )
        top_k_slider = gr.Slider(
            minimum=5, maximum=30, value=15, step=5,
            label="Results", scale=1,
        )

    search_status = gr.Textbox(
        label="Search Status", interactive=False, visible=False, max_lines=3,
    )

    # Step 2: Results — clickable dataframe
    results_table = gr.Dataframe(
        headers=["#", "API Name", "Summary", "Distance"],
        datatype=["number", "str", "str", "str"],
        label="Search Results (click a row to view details)",
        visible=False, interactive=False,
    )

    # SDK examples
    with gr.Accordion("SDK Code Examples", open=False, visible=False) as sdk_accordion:
        sdk_display = gr.Markdown("No SDK examples found.")

    # Step 3: Selected API detail + code generation
    api_detail = gr.Code(
        language="cpp", label="API Detail (Syntax + Parameters)",
        visible=False, lines=8, interactive=False,
    )

    with gr.Row():
        user_hint = gr.Textbox(
            label="Hint (optional)", visible=False,
            placeholder="e.g. create a wall between two points",
            scale=4,
        )
        gen_btn = gr.Button("Generate Code Example", variant="primary",
                            visible=False, scale=1)

    generated_code = gr.Code(
        language="cpp", label="Step 3: Generated Code Example",
        visible=False, lines=15, interactive=True,
    )

    # Hidden state
    search_results_state = gr.State([])  # list of api_items dicts

    # =====================================================================
    # Handlers
    # =====================================================================

    def on_search(query, mode, top_k):
        """Search API docs, return results."""
        import time as _time
        t0 = _time.monotonic()
        logger.info(f"[api_explorer] search: {query!r} mode={mode!r} top_k={top_k}")

        if not query or not query.strip():
            return ("", gr.Dataframe(visible=False),
                    [], gr.Code(visible=False, value=""),
                    gr.Textbox(visible=False, value=""),
                    gr.Button(visible=False),
                    gr.Code(visible=False, value=""), "")

        try:
            fast = mode.startswith("Fast")
            result = _search_direct(query.strip(), top_k=int(top_k), fast=fast)

            if "error" in result and not result.get("api_items"):
                err = result["error"]
                return (f"Error: {err}",
                        gr.Dataframe(visible=False),
                        [], gr.Code(visible=False, value=""),
                        gr.Textbox(visible=False, value=""),
                        gr.Button(visible=False),
                        gr.Code(visible=False, value=""), "")

            api_items = result.get("api_items", [])
            sdk_items = result.get("sdk_items", [])
            rewritten = result.get("rewritten_query", query)

            # Build dataframe rows
            df_rows = _build_results_dataframe(api_items)

            # SDK markdown
            sdk_md = _build_sdk_md(sdk_items)

            elapsed = _time.monotonic() - t0
            status = f"Found {len(api_items)} API docs, {len(sdk_items)} SDK examples ({elapsed:.1f}s)"
            if rewritten != query:
                status += f"\nRewritten query: {rewritten}"
            if fast:
                status += "\nMode: Fast (embedding only, no rewrite/rerank)"

            logger.info(f"[api_explorer] done: {len(api_items)} results in {elapsed:.2f}s")

            return (status,
                    gr.Dataframe(value=df_rows, visible=bool(df_rows)),
                    api_items,
                    gr.Code(visible=False, value=""),
                    gr.Textbox(visible=False, value=""),
                    gr.Button(visible=False),
                    gr.Code(visible=False, value=""),
                    sdk_md)

        except Exception as e:
            logger.error(f"[api_explorer] on_search error: {e}", exc_info=True)
            return (f"Error: {e}",
                    gr.Dataframe(visible=False),
                    [], gr.Code(visible=False, value=""),
                    gr.Textbox(visible=False, value=""),
                    gr.Button(visible=False),
                    gr.Code(visible=False, value=""), "")

    def on_select_api(api_items, evt: gr.SelectData):
        """User clicks a row in results table → show detail."""
        if not api_items:
            return (gr.Code(visible=False, value=""),
                    gr.Textbox(visible=False, value=""),
                    gr.Button(visible=False),
                    gr.Code(visible=False, value=""))

        idx = evt.index[0]  # row index
        if idx < 0 or idx >= len(api_items):
            return (gr.Code(visible=False, value=""),
                    gr.Textbox(visible=False, value=""),
                    gr.Button(visible=False),
                    gr.Code(visible=False, value=""))

        item = api_items[idx]
        name = item.get("name", "?")
        full_id = item.get("full_id", "")
        syntax = item.get("syntax", "")
        params = item.get("parameters", "")
        remark = item.get("remark", "")

        detail_parts = []
        if full_id:
            detail_parts.append(f"// {full_id}")
        if syntax:
            detail_parts.append(syntax)
        if params:
            detail_parts.append(f"\n// Parameters:\n// {params[:500]}")
        if remark:
            detail_parts.append(f"\n// Remark:\n// {remark[:300]}")

        detail_text = "\n".join(detail_parts) if detail_parts else f"// {name}"

        return (gr.Code(visible=True, value=detail_text),
                gr.Textbox(visible=True, value=""),
                gr.Button(visible=True),
                gr.Code(visible=False, value=""))

    def on_generate(api_detail_code, hint):
        """Generate code example for selected API."""
        if not api_detail_code:
            return gr.Code(visible=True, value="// No API selected")

        # Extract API name from detail (first line: "// Namespace.Class.Method")
        first_line = api_detail_code.strip().split("\n")[0]
        api_name = first_line.lstrip("/ ").strip() if first_line.startswith("//") else "Unknown"
        logger.info(f"[api_explorer] generate: {api_name!r}")

        result = _generate_example(
            api_name=api_name,
            api_context=api_detail_code or "",
            hint=hint or "",
        )
        code = result.get("code", "")
        if not code and "error" in result:
            code = f"// Error: {result['error']}"

        return gr.Code(visible=True, value=code)

    # =====================================================================
    # Wire events
    # =====================================================================

    _search_outputs = [search_status, results_table,
                       search_results_state, api_detail, user_hint, gen_btn,
                       generated_code, sdk_display]

    search_btn.click(on_search,
                     inputs=[search_input, search_mode, top_k_slider],
                     outputs=_search_outputs)
    search_input.submit(on_search,
                        inputs=[search_input, search_mode, top_k_slider],
                        outputs=_search_outputs)

    results_table.select(
        on_select_api,
        inputs=[search_results_state],
        outputs=[api_detail, user_hint, gen_btn, generated_code],
    )

    gen_btn.click(
        on_generate,
        inputs=[api_detail, user_hint],
        outputs=[generated_code],
    )
