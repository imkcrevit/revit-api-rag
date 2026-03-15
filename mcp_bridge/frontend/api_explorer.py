"""
Gradio Tab — API Explorer: Search Revit API docs → Rerank → Generate code examples.

Replaces the Intent Bridge tab. Flow:
  Step 1: User types keyword (e.g. "Part", "Wall.Create")
  Step 2: RAG search + rerank → display ranked API results
  Step 3: User selects an API → LLM generates code example
"""
from __future__ import annotations

import json
import logging
import traceback
import httpx
import gradio as gr

logger = logging.getLogger("mcp_bridge.api_explorer")


def _bridge_url(path: str) -> str:
    return f"http://127.0.0.1:7860/api/v1/bridge{path}"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _search_api(query: str, top_k: int = 15) -> dict:
    try:
        resp = httpx.post(_bridge_url("/api-search"),
                          json={"query": query, "top_k": top_k}, timeout=30)
        return resp.json()
    except Exception as e:
        return {"error": str(e), "api_items": [], "sdk_items": []}


def _generate_example(api_name: str, api_context: str, hint: str = "") -> dict:
    try:
        resp = httpx.post(_bridge_url("/api-codegen"),
                          json={"api_name": api_name,
                                "api_context": api_context,
                                "user_hint": hint}, timeout=120)
        return resp.json()
    except Exception as e:
        return {"code": "", "error": str(e)}


# ---------------------------------------------------------------------------
# Tab builder
# ---------------------------------------------------------------------------

def create_api_explorer_tab():
    """Create API Explorer tab contents."""

    gr.Markdown("### Revit API Explorer — Search → Rerank → Generate")

    # Step 1: Search
    with gr.Row():
        search_input = gr.Textbox(
            placeholder="Type API keyword: Part, Wall.Create, FamilyInstance, FilteredElementCollector...",
            show_label=False, scale=5, lines=1, max_lines=1,
        )
        search_btn = gr.Button("Search API", variant="primary", scale=1)

    search_status = gr.Textbox(
        label="Step 1: Rerank Results", interactive=False, visible=False, max_lines=2,
    )

    # Step 2: Results table
    results_table = gr.Dataframe(
        headers=["#", "API Name", "Summary", "Distance"],
        label="Step 2: Ranked API Results (click row to select)",
        interactive=False, visible=False,
    )

    # SDK examples accordion
    with gr.Accordion("SDK Code Examples", open=False, visible=False) as sdk_accordion:
        sdk_display = gr.Markdown("No SDK examples found.")

    # Step 3: Selected API detail + code generation
    selected_api_name = gr.Textbox(
        label="Selected API", interactive=False, visible=False,
    )
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

    # State for search results
    search_results_state = gr.State([])  # list of api_items dicts

    # =====================================================================
    # Handlers
    # =====================================================================

    def on_search(query):
        """Search API docs, return reranked results."""
        logger.info(f"[api_explorer] search: {query!r}")
        if not query or not query.strip():
            return (gr.Textbox(visible=False),
                    gr.Dataframe(visible=False),
                    [],
                    gr.Textbox(visible=False),
                    gr.Code(visible=False),
                    gr.Textbox(visible=False),
                    gr.Button(visible=False),
                    gr.Code(visible=False),
                    gr.Markdown(""))

        result = _search_api(query.strip())

        if "error" in result and not result.get("api_items"):
            return (gr.Textbox(visible=True, value=f"Error: {result['error']}"),
                    gr.Dataframe(visible=False),
                    [],
                    gr.Textbox(visible=False),
                    gr.Code(visible=False),
                    gr.Textbox(visible=False),
                    gr.Button(visible=False),
                    gr.Code(visible=False),
                    gr.Markdown(""))

        api_items = result.get("api_items", [])
        sdk_items = result.get("sdk_items", [])
        rewritten = result.get("rewritten_query", query)

        # Build table rows
        table_data = []
        for i, item in enumerate(api_items):
            summary = (item.get("summary") or "")[:100]
            table_data.append([
                i + 1,
                item.get("name", "?"),
                summary,
                item.get("distance", 0),
            ])

        # Build SDK display
        sdk_md = ""
        if sdk_items:
            for si in sdk_items:
                proj = si.get("project", "?")
                content = si.get("content", "")[:400]
                apis = ", ".join(si.get("mentioned_apis", [])[:5])
                sdk_md += f"**{proj}** (APIs: {apis})\n```csharp\n{content}\n```\n\n"
        else:
            sdk_md = "No SDK examples found."

        status = f"Found {len(api_items)} API docs, {len(sdk_items)} SDK examples"
        if rewritten != query:
            status += f"\nRewritten query: {rewritten}"

        return (gr.Textbox(visible=True, value=status),
                gr.Dataframe(visible=True, value=table_data),
                api_items,
                gr.Textbox(visible=False),
                gr.Code(visible=False),
                gr.Textbox(visible=False),
                gr.Button(visible=False),
                gr.Code(visible=False),
                gr.Markdown(sdk_md))

    def on_select_result(evt: gr.SelectData, api_items):
        """User clicks a row in results table → show API detail."""
        row_idx = evt.index[0]
        if row_idx >= len(api_items):
            return (gr.Textbox(visible=False),
                    gr.Code(visible=False),
                    gr.Textbox(visible=False),
                    gr.Button(visible=False),
                    gr.Code(visible=False))

        item = api_items[row_idx]
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

        return (gr.Textbox(visible=True, value=f"{name} ({full_id})"),
                gr.Code(visible=True, value=detail_text),
                gr.Textbox(visible=True),
                gr.Button(visible=True),
                gr.Code(visible=False))

    def on_generate(api_name, api_detail_code, hint):
        """Generate code example for selected API."""
        logger.info(f"[api_explorer] generate: {api_name!r}")
        if not api_name:
            return gr.Code(visible=True, value="// No API selected")

        result = _generate_example(
            api_name=api_name.split(" (")[0],  # strip full_id
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

    search_btn.click(
        on_search, inputs=[search_input],
        outputs=[search_status, results_table, search_results_state,
                 selected_api_name, api_detail, user_hint, gen_btn,
                 generated_code, sdk_display],
    )
    search_input.submit(
        on_search, inputs=[search_input],
        outputs=[search_status, results_table, search_results_state,
                 selected_api_name, api_detail, user_hint, gen_btn,
                 generated_code, sdk_display],
    )

    results_table.select(
        on_select_result, inputs=[search_results_state],
        outputs=[selected_api_name, api_detail, user_hint, gen_btn,
                 generated_code],
    )

    gen_btn.click(
        on_generate, inputs=[selected_api_name, api_detail, user_hint],
        outputs=[generated_code],
    )
