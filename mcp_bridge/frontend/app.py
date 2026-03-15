"""
Gradio Tab D — MCP Bridge: Code Generation + Revit Execution + Tool Solidification.

Workflow (with step indicators):
  Step 1: Input → Step 2: Select → Step 3: Review Code → Step 4: Execute → Step 5: Solidify

Tool Library workflow:
  Select Tool → Load Choices (auto) → Set Params → Run
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
        resp = httpx.get(_bridge_url("/revit-health"), timeout=10)
        data = resp.json()
        detail = data.get("detail", "")
        if data.get("revit_connected"):
            return f"Revit Connected ({data.get('latency_ms', '?')}ms)"
        return f"Revit Disconnected: {detail}"
    except Exception as e:
        return f"Revit Disconnected: {e}"


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


def _get_tool_detail(name: str) -> dict:
    """Fetch full tool definition including parameters."""
    try:
        resp = httpx.get(_bridge_url(f"/tools/{name}"), timeout=10)
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
# Step indicator helper
# ---------------------------------------------------------------------------

def _step_indicator(current: int, labels: list[str]) -> str:
    parts = []
    for i, label in enumerate(labels, 1):
        if i == current:
            parts.append(f">> [{i}. {label}] <<")
        elif i < current:
            parts.append(f"[{i}. {label}]")
        else:
            parts.append(f"  {i}. {label}  ")
    return "  >  ".join(parts)


MAIN_STEPS = ["Input", "Select", "Review Code", "Execute", "Solidify"]
TOOL_STEPS = ["Select Tool", "Load Choices", "Set Params", "Run"]


# ---------------------------------------------------------------------------
# Tab D builder
# ---------------------------------------------------------------------------

def create_bridge_tab():
    """Create MCP Bridge tab contents (called inside gr.Tab)."""

    # === Header: Revit Status ===
    with gr.Row():
        revit_status = gr.Textbox(
            value="Revit Disconnected",
            label="Revit Status",
            interactive=False,
            max_lines=1,
            scale=4,
        )
        refresh_btn = gr.Button("Refresh", size="sm", scale=0)

    # === State ===
    current_query = gr.State("")
    current_selections = gr.State({})
    last_code = gr.State("")

    # ─────────────────────────────────────────────────────────────
    # SECTION A: Code Generation Pipeline
    # ─────────────────────────────────────────────────────────────
    gr.Markdown("### Code Generation Pipeline")

    step_display = gr.Textbox(
        value=_step_indicator(1, MAIN_STEPS),
        label="Workflow",
        interactive=False,
        max_lines=1,
    )

    # --- Step 1: Input ---
    with gr.Row():
        query_input = gr.Textbox(
            placeholder="Describe what to do in Revit... (e.g. create structural column at 3000,3000)",
            show_label=False,
            scale=5,
            lines=1,
            max_lines=2,
        )
        generate_btn = gr.Button("Generate Code", variant="primary", scale=1)

    # --- Step 2: Interactive Selection (all components individually visible-controlled) ---
    selection_status = gr.Textbox(
        label="Step 2: Select Options (from Revit)",
        interactive=False,
        visible=False,
    )
    family_dropdown = gr.Dropdown(
        label="Family Type", choices=[], interactive=True, visible=False,
    )
    level_dropdown = gr.Dropdown(
        label="Level", choices=[], interactive=True, visible=False,
    )
    x_input = gr.Number(label="X (mm)", value=0, visible=False)
    y_input = gr.Number(label="Y (mm)", value=0, visible=False)
    confirm_selection_btn = gr.Button(
        "Confirm & Generate Code", variant="primary", visible=False,
    )

    # --- Step 3: Review Code ---
    code_display = gr.Code(
        language="cpp",
        label="Step 3: Review Generated Code",
        interactive=True,
        lines=15,
    )
    security_status = gr.Textbox(
        label="Security Review", interactive=False, visible=False,
    )

    # --- Step 4: Execute ---
    execute_btn = gr.Button("Execute in Revit", variant="primary")
    exec_result = gr.Textbox(
        label="Step 4: Execution Result", interactive=False, lines=3,
    )

    # --- Step 5: Solidify ---
    with gr.Accordion("Step 5: Save as Reusable Tool", open=False):
        with gr.Row():
            tool_name = gr.Textbox(label="Tool Name", placeholder="e.g. create_wall")
            tool_desc = gr.Textbox(label="Description", placeholder="What this tool does")
        solidify_btn = gr.Button("Solidify", variant="primary")
        solidify_result = gr.Textbox(label="", interactive=False, visible=False)

    # --- RAG Context ---
    with gr.Accordion("RAG Context", open=False):
        rag_info = gr.JSON(label="Retrieval Details")

    # ─────────────────────────────────────────────────────────────
    # SECTION B: Tool Library
    # ─────────────────────────────────────────────────────────────
    gr.Markdown("### Solidified Tool Library")

    tool_step_display = gr.Textbox(
        value=_step_indicator(1, TOOL_STEPS),
        label="Tool Workflow",
        interactive=False,
        max_lines=1,
    )

    # Step 1: Select tool
    tools_refresh_btn = gr.Button("Refresh Tools", size="sm")
    tools_table = gr.Dataframe(
        headers=["Name", "Description", "Uses", "Tags"],
        label="Available Tools (click name to select)",
        interactive=False,
    )

    # Step 2: Load choices
    with gr.Row():
        run_tool_name = gr.Textbox(
            label="Selected Tool", placeholder="click a tool above or type name",
            scale=3,
        )
        load_choices_btn = gr.Button("Load Choices from Revit", scale=1)

    tool_choices_info = gr.Textbox(
        label="Step 2: Available Choices (queried from Revit)",
        interactive=False,
        visible=False,
        lines=5,
    )
    tool_choices_state = gr.State({})

    # Step 3: Parameters
    run_tool_params = gr.Textbox(
        label="Step 3: Parameters (auto-filled from choices, edit as needed)",
        placeholder='{"level_name": "L1", "type_name": "...", "x": 0}',
        lines=4,
    )

    # Step 4: Run
    run_tool_btn = gr.Button("Run Tool", variant="primary")
    run_tool_result = gr.Textbox(
        label="Step 4: Tool Result", interactive=False, lines=3,
    )

    # =====================================================================
    # Event handlers
    # =====================================================================

    def on_refresh_health():
        return _check_revit_health()

    # --- Code Generation Pipeline ---

    def on_generate(query):
        """Classify intent → direct generate or show selection dropdowns."""
        if not query.strip():
            return (query, {}, "", "",
                    # selection components (6)
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    # security + rag + step
                    gr.update(visible=False), None, _step_indicator(1, MAIN_STEPS))

        intent = _classify_intent(query)
        itype = intent.get("interaction_type", "direct")

        if itype == "direct":
            result = _generate_code(query)
            code = result.get("code", "")
            safe = result.get("safe", True)
            warnings = result.get("warnings", [])
            rag = result.get("rag_context", {})
            sec_text = "Safe" if safe else "Warning: " + "; ".join(warnings)

            return (query, {}, code, code,
                    # hide all selection components
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(visible=False), gr.update(visible=False),
                    # show security + rag
                    gr.update(visible=True, value=sec_text), rag,
                    _step_indicator(3, MAIN_STEPS))

        # Interactive: query Revit for options
        family_choices = []
        level_choices = []
        levels_raw = []  # keep raw level data for elevation matching
        status_parts = []
        parsed_coords = intent.get("parsed_coords")

        for q in intent.get("queries", []):
            cmd = q.get("command")
            params = q.get("params", {})
            label = q.get("label", cmd)
            data = _query_revit(cmd, params)

            if cmd == "get_available_family_types" and isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        name = item.get("name", item.get("Name", str(item)))
                        family_choices.append(name)
                    else:
                        family_choices.append(str(item))
                status_parts.append(f"{label}: {len(family_choices)}")

        if intent.get("need_level"):
            levels_raw = _query_revit("get_levels", {})
            if isinstance(levels_raw, list):
                for item in levels_raw:
                    if isinstance(item, dict):
                        name = item.get("Name", item.get("name", str(item)))
                        elev = item.get("ElevationMm", item.get("elevation", ""))
                        level_choices.append(f"{name} ({elev}mm)" if elev else name)
                    else:
                        level_choices.append(str(item))
            status_parts.append(f"Levels: {len(level_choices)}")

        has_family = len(family_choices) > 0
        has_level = len(level_choices) > 0

        # Auto-fill coordinates from parsed query
        x_val = parsed_coords["x"] if parsed_coords else 0
        y_val = parsed_coords["y"] if parsed_coords else 0

        # Auto-select level by elevation (z coordinate)
        level_default = level_choices[0] if level_choices else None
        if parsed_coords and parsed_coords.get("z") is not None and levels_raw:
            from mcp_bridge.interactive import IntentClassifier
            matched = IntentClassifier.match_level_by_elevation(
                levels_raw, parsed_coords["z"]
            )
            if matched:
                # Find the display string that starts with matched name
                for lc in level_choices:
                    if lc.startswith(matched):
                        level_default = lc
                        break

        status_msg = "Queried from Revit: " + " | ".join(status_parts)
        if parsed_coords:
            z_info = f", Z={parsed_coords['z']}mm" if parsed_coords.get("z") is not None else ""
            status_msg += f"\nParsed from input: X={x_val}mm, Y={y_val}mm{z_info}"
            if level_default:
                status_msg += f" → Level: {level_default}"

        return (query, {}, "", "",
                # selection_status
                gr.update(visible=True, value=status_msg),
                # family_dropdown
                gr.update(visible=has_family, choices=family_choices,
                          value=family_choices[0] if family_choices else None),
                # level_dropdown
                gr.update(visible=has_level, choices=level_choices,
                          value=level_default),
                # x_input, y_input — pre-filled from parsed coords
                gr.update(visible=True, value=x_val),
                gr.update(visible=True, value=y_val),
                # confirm_btn
                gr.update(visible=True),
                # security (hide)
                gr.update(visible=False),
                # rag
                None,
                _step_indicator(2, MAIN_STEPS))

    def on_confirm_selection(query, family, level, x, y):
        selections = {}
        if family:
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
        sec_text = "Safe" if safe else "Warning: " + "; ".join(warnings)

        return (selections, code, code,
                gr.update(visible=True, value=sec_text),
                rag,
                _step_indicator(3, MAIN_STEPS))

    def on_execute(code):
        if not code.strip():
            return "No code to execute.", _step_indicator(3, MAIN_STEPS)
        result = _execute_code(code)
        if result.get("success"):
            res = result.get("result", "")
            msg = f"Success\n{json.dumps(res, indent=2, ensure_ascii=False) if res else '(no result)'}"
        else:
            msg = f"Failed\n{result.get('error', 'Unknown error')}"
        return msg, _step_indicator(4, MAIN_STEPS)

    def on_solidify(name, description, code, query):
        if not name.strip():
            return (gr.update(visible=True, value="Please enter a tool name."),
                    _step_indicator(4, MAIN_STEPS))
        result = _solidify_tool(name, code, description, query)
        if "error" in result:
            return (gr.update(visible=True, value=f"Error: {result['error']}"),
                    _step_indicator(4, MAIN_STEPS))
        return (gr.update(visible=True, value=f"Solidified as '{result.get('name', name)}'"),
                _step_indicator(5, MAIN_STEPS))

    # --- Tool Library ---

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

    def on_select_tool(evt: gr.SelectData):
        if evt.value and evt.index[1] == 0:
            return evt.value
        return gr.update()

    def on_load_choices(name):
        if not name.strip():
            return {}, gr.update(visible=False), "", _step_indicator(1, TOOL_STEPS)

        tool_detail = _get_tool_detail(name)
        all_params = tool_detail.get("parameters", [])
        choices = _get_tool_choices(name)

        prefill = {}
        display_lines = []

        for p in all_params:
            pname = p.get("name", "")
            if pname in choices and choices[pname]:
                items = choices[pname]
                labels = [it["label"] for it in items]
                display_lines.append(
                    f"  {pname} ({p.get('description', '')}):\n"
                    + "\n".join(f"    - {lb}" for lb in labels)
                )
                prefill[pname] = items[0]["value"]
            elif "default" in p:
                prefill[pname] = p["default"]
            else:
                prefill[pname] = ""

        if display_lines:
            display_text = "Available choices:\n\n" + "\n\n".join(display_lines)
        else:
            display_text = "No dynamic parameters."

        return (
            choices,
            gr.update(visible=True, value=display_text),
            json.dumps(prefill, indent=2, ensure_ascii=False),
            _step_indicator(3, TOOL_STEPS),
        )

    def on_run_tool(name, params_json):
        if not name.strip():
            return "Please enter a tool name.", _step_indicator(1, TOOL_STEPS)
        params = {}
        if params_json.strip():
            try:
                params = json.loads(params_json)
            except json.JSONDecodeError:
                return "Invalid JSON parameters.", _step_indicator(3, TOOL_STEPS)
        result = _run_tool(name, params)
        if result.get("success"):
            msg = f"Success\n{json.dumps(result.get('result', ''), indent=2, ensure_ascii=False)}"
        else:
            msg = f"Failed: {result.get('error', 'Unknown')}"
        return msg, _step_indicator(4, TOOL_STEPS)

    # === Wire events ===

    refresh_btn.click(on_refresh_health, outputs=[revit_status])

    # Code Generation — outputs are all individual Components (no layout containers)
    generate_btn.click(
        on_generate,
        inputs=[query_input],
        outputs=[
            current_query, current_selections, code_display, last_code,
            # 6 selection components (all gr.Component, not layout)
            selection_status, family_dropdown, level_dropdown,
            x_input, y_input, confirm_selection_btn,
            # rest
            security_status, rag_info, step_display,
        ],
    )

    confirm_selection_btn.click(
        on_confirm_selection,
        inputs=[current_query, family_dropdown, level_dropdown, x_input, y_input],
        outputs=[current_selections, code_display, last_code, security_status,
                 rag_info, step_display],
    )

    execute_btn.click(
        on_execute,
        inputs=[code_display],
        outputs=[exec_result, step_display],
    )

    solidify_btn.click(
        on_solidify,
        inputs=[tool_name, tool_desc, last_code, current_query],
        outputs=[solidify_result, step_display],
    )

    # Tool Library
    tools_refresh_btn.click(on_refresh_tools, outputs=[tools_table])
    tools_table.select(on_select_tool, outputs=[run_tool_name])

    load_choices_btn.click(
        on_load_choices,
        inputs=[run_tool_name],
        outputs=[tool_choices_state, tool_choices_info, run_tool_params,
                 tool_step_display],
    )

    run_tool_btn.click(
        on_run_tool,
        inputs=[run_tool_name, run_tool_params],
        outputs=[run_tool_result, tool_step_display],
    )
