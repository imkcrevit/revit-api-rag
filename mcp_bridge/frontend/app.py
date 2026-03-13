"""
Gradio Tab D — MCP Bridge: Code Generation + Revit Execution + Tool Solidification.

Workflow:
1. User inputs natural language query
2. System classifies intent → direct or interactive selection
3. If interactive: query Revit for options → user selects → generate code
4. If direct: RAG + LLM → generate C# code
5. User reviews code → execute in Revit
6. On success → option to solidify as reusable tool
"""
from __future__ import annotations

import json
import httpx
import gradio as gr


def _api_base() -> str:
    return "http://127.0.0.1:7860"


def _bridge_url(path: str) -> str:
    return f"{_api_base()}/api/v1/bridge{path}"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _check_revit_health() -> str:
    try:
        resp = httpx.get(_bridge_url("/revit-health"), timeout=5)
        data = resp.json()
        if data.get("revit_connected"):
            return f"🟢 Revit Connected ({data.get('latency_ms', '?')}ms)"
        return "🔴 Revit Disconnected"
    except Exception:
        return "🔴 Revit Disconnected"


def _classify_intent(query: str) -> dict:
    try:
        resp = httpx.post(_bridge_url("/classify-intent"),
                          json={"query": query}, timeout=10)
        return resp.json()
    except Exception:
        return {"interaction_type": "direct", "queries": [], "need_level": False}


def _query_revit(command: str, params: dict) -> list:
    try:
        resp = httpx.post(_bridge_url("/query-revit"),
                          json={"command": command, "params": params}, timeout=30)
        data = resp.json()
        return data.get("result", [])
    except Exception:
        return []


def _generate_code(query: str, selections: dict | None = None,
                   api_top_k: int = 15, code_top_k: int = 5) -> dict:
    try:
        if selections:
            resp = httpx.post(_bridge_url("/generate-with-selections"),
                              json={"query": query, "selections": selections,
                                    "api_top_k": api_top_k, "code_top_k": code_top_k},
                              timeout=120)
        else:
            resp = httpx.post(_bridge_url("/generate"),
                              json={"query": query, "api_top_k": api_top_k,
                                    "code_top_k": code_top_k},
                              timeout=120)
        return resp.json()
    except Exception as e:
        return {"code": "", "error": str(e)}


def _execute_code(code: str, params: list | None = None) -> dict:
    try:
        resp = httpx.post(_bridge_url("/execute"),
                          json={"code": code, "parameters": params or []},
                          timeout=120)
        return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def _solidify_tool(name: str, code: str, description: str,
                   source_query: str) -> dict:
    try:
        from mcp_bridge.code_generator import CodeGenerator
        parameters = CodeGenerator.extract_parameters(code)
        resp = httpx.post(_bridge_url("/solidify"),
                          json={"name": name, "code": code,
                                "description": description,
                                "parameters": parameters,
                                "source_query": source_query},
                          timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def _list_tools() -> list[dict]:
    try:
        resp = httpx.get(_bridge_url("/tools"), timeout=10)
        return resp.json()
    except Exception:
        return []


def _get_tool_choices(name: str) -> dict:
    """Fetch dynamic parameter choices from Revit for a tool."""
    try:
        resp = httpx.get(_bridge_url(f"/tools/{name}/choices"), timeout=30)
        return resp.json()
    except Exception:
        return {}


def _run_tool(name: str, params: dict) -> dict:
    try:
        resp = httpx.post(_bridge_url(f"/tools/{name}/run"),
                          json={"name": name, "params": params}, timeout=120)
        return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Tab D builder
# ---------------------------------------------------------------------------

def create_bridge_tab():
    """Create MCP Bridge tab contents (called inside gr.Tab)."""

    # --- Revit Status ---
    revit_status = gr.Textbox(
        value="🔴 Revit Disconnected",
        label="Revit Status",
        interactive=False,
        max_lines=1,
    )
    refresh_btn = gr.Button("Refresh", size="sm", scale=0)

    # --- State ---
    current_query = gr.State("")
    current_selections = gr.State({})
    last_code = gr.State("")

    # --- Input ---
    with gr.Row():
        query_input = gr.Textbox(
            placeholder="Describe what to do in Revit... (e.g. 创建结构柱, create a wall)",
            show_label=False,
            scale=5,
            lines=1,
            max_lines=2,
        )
        generate_btn = gr.Button("Generate Code", variant="primary", scale=1)
        execute_btn = gr.Button("Execute", variant="secondary", scale=1)

    # --- Interactive Selection Panel ---
    with gr.Accordion("Interactive Selection", open=False, visible=False) as selection_panel:
        selection_status = gr.Textbox(label="Status", interactive=False)
        family_dropdown = gr.Dropdown(label="Family Type", choices=[], interactive=True, visible=False)
        level_dropdown = gr.Dropdown(label="Level", choices=[], interactive=True, visible=False)
        with gr.Row(visible=False) as coord_row:
            x_input = gr.Number(label="X (mm)", value=0)
            y_input = gr.Number(label="Y (mm)", value=0)
        confirm_selection_btn = gr.Button("Confirm & Generate", variant="primary", visible=False)

    # --- Code Display ---
    code_display = gr.Code(
        language="csharp",
        label="Generated Code",
        interactive=True,
        lines=15,
    )

    # --- Security Review ---
    security_status = gr.Textbox(label="Security Review", interactive=False, visible=False)

    # --- Execution Result ---
    exec_result = gr.Textbox(label="Execution Result", interactive=False, lines=3)

    # --- Solidify Panel ---
    with gr.Accordion("Save as Tool", open=False) as solidify_panel:
        with gr.Row():
            tool_name = gr.Textbox(label="Tool Name", placeholder="e.g. create_wall")
            tool_desc = gr.Textbox(label="Description", placeholder="What this tool does")
        solidify_btn = gr.Button("Solidify", variant="primary")
        solidify_result = gr.Textbox(label="", interactive=False, visible=False)

    # --- RAG Context ---
    with gr.Accordion("RAG Context", open=False):
        rag_info = gr.JSON(label="Retrieval Details")

    # --- Tool Library ---
    with gr.Accordion("Solidified Tools", open=False):
        tools_refresh_btn = gr.Button("Refresh Tools", size="sm")
        tools_table = gr.Dataframe(
            headers=["Name", "Description", "Uses", "Tags"],
            label="Available Tools",
            interactive=False,
        )
        with gr.Row():
            run_tool_name = gr.Textbox(label="Tool Name", placeholder="e.g. create_wall")
            load_choices_btn = gr.Button("Load Choices", size="sm", scale=0)
        tool_choices_state = gr.State({})
        tool_choices_display = gr.JSON(label="Dynamic Choices (from Revit)", visible=False)
        run_tool_params = gr.Textbox(
            label="Params (JSON)",
            placeholder='{"level_name": "L1", "height": 3000}',
            lines=3,
        )
        with gr.Row():
            run_tool_btn = gr.Button("Run Tool", variant="primary")
        run_tool_result = gr.Textbox(label="Tool Result", interactive=False)

    # =====================================================================
    # Event handlers
    # =====================================================================

    def on_refresh_health():
        return _check_revit_health()

    def on_generate(query):
        if not query.strip():
            return (query, {}, "", "", gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    None)

        # Step 1: Classify intent
        intent = _classify_intent(query)
        itype = intent.get("interaction_type", "direct")

        if itype == "direct":
            # Direct generation
            result = _generate_code(query)
            code = result.get("code", "")
            safe = result.get("safe", True)
            warnings = result.get("warnings", [])
            rag = result.get("rag_context", {})

            sec_text = "✅ Safe" if safe else "⚠️ " + "; ".join(warnings)

            return (query, {}, code, code,
                    gr.update(visible=False),  # selection_panel
                    gr.update(visible=True, value=sec_text),  # security_status
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    rag)

        # Interactive: need selections
        family_choices = []
        level_choices = []
        status_msg = ""

        for q in intent.get("queries", []):
            cmd = q.get("command")
            params = q.get("params", {})
            label = q.get("label", cmd)
            data = _query_revit(cmd, params)

            if cmd == "get_available_family_types":
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            name = item.get("name", item.get("Name", str(item)))
                            family_choices.append(name)
                        else:
                            family_choices.append(str(item))
                status_msg += f"Found {len(family_choices)} {label}\n"

        if intent.get("need_level"):
            levels_data = _query_revit("get_levels", {})
            if isinstance(levels_data, list):
                for item in levels_data:
                    if isinstance(item, dict):
                        name = item.get("Name", item.get("name", str(item)))
                        elev = item.get("ElevationMm", item.get("elevation", ""))
                        level_choices.append(f"{name} ({elev}mm)" if elev else name)
                    else:
                        level_choices.append(str(item))
            status_msg += f"Found {len(level_choices)} levels\n"

        has_family = len(family_choices) > 0
        has_level = len(level_choices) > 0

        return (query, {}, "", "",
                gr.update(visible=True),  # selection_panel
                gr.update(visible=False),  # security_status
                gr.update(visible=has_family, choices=family_choices,
                          value=family_choices[0] if family_choices else None),
                gr.update(visible=has_level, choices=level_choices,
                          value=level_choices[0] if level_choices else None),
                gr.update(visible=True),  # coord_row
                gr.update(visible=True),  # confirm_btn
                None)

    def on_confirm_selection(query, family, level, x, y):
        selections = {}
        if family:
            # Strip elevation info from level display
            selections["family_type"] = family
        if level:
            level_name = level.split(" (")[0] if " (" in level else level
            selections["level"] = level_name
        if x or y:
            selections["position"] = {"x": x, "y": y}

        result = _generate_code(query, selections=selections)
        code = result.get("code", "")
        safe = result.get("safe", True)
        warnings = result.get("warnings", [])
        rag = result.get("rag_context", {})

        sec_text = "✅ Safe" if safe else "⚠️ " + "; ".join(warnings)

        return (selections, code, code,
                gr.update(visible=True, value=sec_text),
                rag)

    def on_execute(code):
        if not code.strip():
            return "No code to execute."
        result = _execute_code(code)
        if result.get("success"):
            res = result.get("result", "")
            return f"✅ Success\n{json.dumps(res, indent=2, ensure_ascii=False) if res else '(no result)'}"
        else:
            return f"❌ Failed\n{result.get('error', 'Unknown error')}"

    def on_solidify(name, description, code, query):
        if not name.strip():
            return gr.update(visible=True, value="Please enter a tool name.")
        result = _solidify_tool(name, code, description, query)
        if "error" in result:
            return gr.update(visible=True, value=f"Error: {result['error']}")
        return gr.update(visible=True,
                         value=f"✅ Solidified as '{result.get('name', name)}'")

    def on_refresh_tools():
        tools = _list_tools()
        rows = []
        for t in tools:
            rows.append([
                t.get("name", ""),
                t.get("description", ""),
                t.get("execution_count", 0),
                ", ".join(t.get("tags", [])),
            ])
        return rows

    def on_load_choices(name):
        if not name.strip():
            return {}, gr.update(visible=False), ""
        choices = _get_tool_choices(name)
        if not choices:
            return {}, gr.update(visible=False), ""
        # Pre-fill params JSON with first available choice for each dynamic param
        prefill = {}
        for param_name, items in choices.items():
            if items:
                prefill[param_name] = items[0]["value"]
        return (
            choices,
            gr.update(visible=True, value=choices),
            json.dumps(prefill, indent=2, ensure_ascii=False),
        )

    def on_run_tool(name, params_json):
        if not name.strip():
            return "Please enter a tool name."
        params = {}
        if params_json.strip():
            try:
                params = json.loads(params_json)
            except json.JSONDecodeError:
                return "Invalid JSON parameters."
        result = _run_tool(name, params)
        if result.get("success"):
            return f"✅ {json.dumps(result.get('result', ''), indent=2, ensure_ascii=False)}"
        return f"❌ {result.get('error', 'Failed')}"

    # --- Wire events ---
    refresh_btn.click(on_refresh_health, outputs=[revit_status])

    generate_btn.click(
        on_generate,
        inputs=[query_input],
        outputs=[current_query, current_selections, code_display, last_code,
                 selection_panel, security_status,
                 family_dropdown, level_dropdown, coord_row, confirm_selection_btn,
                 rag_info],
    )

    confirm_selection_btn.click(
        on_confirm_selection,
        inputs=[current_query, family_dropdown, level_dropdown, x_input, y_input],
        outputs=[current_selections, code_display, last_code, security_status, rag_info],
    )

    execute_btn.click(on_execute, inputs=[code_display], outputs=[exec_result])

    solidify_btn.click(
        on_solidify,
        inputs=[tool_name, tool_desc, last_code, current_query],
        outputs=[solidify_result],
    )

    tools_refresh_btn.click(on_refresh_tools, outputs=[tools_table])
    load_choices_btn.click(
        on_load_choices,
        inputs=[run_tool_name],
        outputs=[tool_choices_state, tool_choices_display, run_tool_params],
    )
    run_tool_btn.click(on_run_tool, inputs=[run_tool_name, run_tool_params],
                       outputs=[run_tool_result])
