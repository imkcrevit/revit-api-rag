"""
Gradio Tab D — MCP Bridge: Code Generation + Revit Execution + Tool Solidification.

Workflow (with step indicators):
  Step 1: Input → Step 2: Select → Step 3: Review Code → Step 4: Execute → Step 5: Solidify

Tool Library workflow:
  Select Tool → Load Choices (auto) → Set Params → Run
"""
from __future__ import annotations

import json
import logging
import traceback
import httpx
import gradio as gr

logger = logging.getLogger("mcp_bridge.frontend")


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
            logger.info(f"[_generate_code] POST /generate-with-selections query={query!r} selections={selections}")
            resp = httpx.post(_bridge_url("/generate-with-selections"),
                              json={"query": query, "selections": selections,
                                    "api_top_k": api_top_k, "code_top_k": code_top_k},
                              timeout=120)
        else:
            logger.info(f"[_generate_code] POST /generate query={query!r}")
            resp = httpx.post(_bridge_url("/generate"),
                              json={"query": query, "api_top_k": api_top_k,
                                    "code_top_k": code_top_k},
                              timeout=120)
        logger.info(f"[_generate_code] response status={resp.status_code}")
        return resp.json()
    except Exception as e:
        logger.error(f"[_generate_code] EXCEPTION: {e}")
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
    try:
        # Quick check: get tool detail first to see if it has dynamic params
        detail = _get_tool_detail(name)
        has_dynamic = any("choices_from" in p for p in detail.get("parameters", []))
        if not has_dynamic:
            return {}
        resp = httpx.get(_bridge_url(f"/tools/{name}/choices"), timeout=15)
        return resp.json()
    except Exception:
        return {}


def _get_tool_detail(name: str) -> dict:
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
# Step indicator
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

    # === Header ===
    with gr.Row():
        revit_status = gr.Textbox(
            value="Revit Disconnected", label="Revit Status",
            interactive=False, max_lines=1, scale=4,
        )
        refresh_btn = gr.Button("Refresh", size="sm", scale=0)

    # === State ===
    current_query = gr.State("")
    current_selections = gr.State({})
    last_code = gr.State("")

    # ── SECTION A: Code Generation Pipeline ──
    gr.Markdown("### Code Generation Pipeline")

    step_display = gr.Textbox(
        value=_step_indicator(1, MAIN_STEPS),
        label="Workflow", interactive=False, max_lines=1,
    )

    # Step 1: Input
    with gr.Row():
        query_input = gr.Textbox(
            placeholder="e.g. 创建结构柱在100,0,0",
            show_label=False, scale=5, lines=1, max_lines=2,
        )
        generate_btn = gr.Button("Generate Code", variant="primary", scale=1)

    # Step 2: Selection — Dropdown for family (many items), Radio for level (few)
    selection_status = gr.Textbox(
        label="Step 2: Select Options (from Revit)",
        interactive=False, visible=False,
    )
    family_radio = gr.Dropdown(
        label="Family Type — select one (type to filter)",
        choices=[], interactive=True, visible=False, filterable=True,
    )
    level_radio = gr.Radio(
        label="Level — select one", choices=[], interactive=True, visible=False,
    )
    x_input = gr.Number(label="X (mm)", value=0, visible=False)
    y_input = gr.Number(label="Y (mm)", value=0, visible=False)
    confirm_selection_btn = gr.Button(
        "Confirm & Generate Code", variant="primary", visible=False,
    )

    # Step 3: Review Code
    code_display = gr.Code(
        language="cpp", label="Step 3: Review Generated Code",
        interactive=True, lines=15,
    )
    security_status = gr.Textbox(
        label="Security Review", interactive=False, visible=False,
    )

    # Step 4: Execute
    execute_btn = gr.Button("Execute in Revit", variant="primary")
    exec_result = gr.Textbox(
        label="Step 4: Execution Result", interactive=False, lines=3,
    )

    # Step 5: Solidify
    gr.Markdown("#### Save as Reusable Tool")
    with gr.Row():
        tool_name = gr.Textbox(label="Tool Name", placeholder="e.g. create_wall")
        tool_desc = gr.Textbox(label="Description", placeholder="What this tool does")
    solidify_btn = gr.Button("Solidify", variant="primary")
    solidify_result = gr.Textbox(label="Solidify Result", interactive=False)

    # RAG Context
    with gr.Accordion("RAG Context", open=False):
        rag_info = gr.JSON(label="Retrieval Details")

    # ── SECTION B: Tool Library ──
    gr.Markdown("### Solidified Tool Library")

    tool_step_display = gr.Textbox(
        value=_step_indicator(1, TOOL_STEPS),
        label="Tool Workflow", interactive=False, max_lines=1,
    )

    tools_refresh_btn = gr.Button("Refresh Tools", size="sm")
    tools_table = gr.Dataframe(
        headers=["Name", "Description", "Uses", "Tags"],
        label="Available Tools", interactive=False,
    )

    with gr.Row():
        run_tool_name = gr.Textbox(
            label="Selected Tool", placeholder="click table row to select", scale=3,
        )
        load_choices_btn = gr.Button("Load Parameters", variant="primary", scale=1)

    tool_choices_info = gr.Textbox(
        label="Step 2: Parameters", interactive=False, lines=1,
        value="Click 'Load Parameters' to load tool configuration",
    )

    # Dynamic selectors for choices_from params (up to 2)
    # NOTE: Gradio 6 bug — Dropdown hangs if toggled from visible=False to True.
    # Workaround: start visible=True with placeholder, never toggle visibility.
    tool_param_radio_1 = gr.Dropdown(
        label="(no parameter loaded)", choices=[], interactive=False, filterable=True,
    )
    tool_param_radio_2 = gr.Dropdown(
        label="(no parameter loaded)", choices=[], interactive=False, filterable=True,
    )
    # Individual text inputs for other params (up to 6)
    tool_param_input_1 = gr.Textbox(label="(no parameter loaded)", interactive=False)
    tool_param_input_2 = gr.Textbox(label="(no parameter loaded)", interactive=False)
    tool_param_input_3 = gr.Textbox(label="(no parameter loaded)", interactive=False)
    tool_param_input_4 = gr.Textbox(label="(no parameter loaded)", interactive=False)
    tool_param_input_5 = gr.Textbox(label="(no parameter loaded)", interactive=False)
    tool_param_input_6 = gr.Textbox(label="(no parameter loaded)", interactive=False)

    # State for param name mappings
    tool_choices_state = gr.State({})   # {param_name: [{label, value}]}
    tool_radio_names = gr.State([])     # param names for radio slots
    tool_input_names = gr.State([])     # param names for text input slots

    run_tool_btn = gr.Button("Run Tool", variant="primary")
    run_tool_result = gr.Textbox(
        label="Step 4: Tool Result", interactive=False, lines=3,
    )

    # =====================================================================
    # Event handlers — ALL return plain values, NO gr.update()
    # =====================================================================

    def on_refresh_health():
        return _check_revit_health()

    def on_generate(query):
        """Returns 13 values matching outputs list."""
        logger.info(f"[on_generate] query={query!r}")
        try:
            if not query.strip():
                return (query, {}, "", "",
                        # selection: status, family(Dropdown), level(Radio), x, y, confirm_btn
                        gr.Textbox(visible=False), gr.Dropdown(visible=False, choices=[]),
                        gr.Radio(visible=False, choices=[]),
                        gr.Number(visible=False, value=0), gr.Number(visible=False, value=0),
                        gr.Button(visible=False),
                        # security, rag, step
                        gr.Textbox(visible=False), None,
                        _step_indicator(1, MAIN_STEPS))

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
                        gr.Textbox(visible=False), gr.Dropdown(visible=False, choices=[]),
                        gr.Radio(visible=False, choices=[]),
                        gr.Number(visible=False, value=0), gr.Number(visible=False, value=0),
                        gr.Button(visible=False),
                        gr.Textbox(visible=True, value=sec_text), rag,
                        _step_indicator(3, MAIN_STEPS))

            # Interactive: query Revit
            family_choices = []
            level_choices = []
            levels_raw = []
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

            # Auto-fill from parsed coords
            x_val = parsed_coords["x"] if parsed_coords else 0
            y_val = parsed_coords["y"] if parsed_coords else 0

            # Auto-select level by elevation
            level_default = level_choices[0] if level_choices else None
            if parsed_coords and parsed_coords.get("z") is not None and levels_raw:
                from mcp_bridge.interactive import IntentClassifier
                matched = IntentClassifier.match_level_by_elevation(
                    levels_raw, parsed_coords["z"]
                )
                if matched:
                    for lc in level_choices:
                        if lc.startswith(matched):
                            level_default = lc
                            break

            status_msg = "Queried from Revit: " + " | ".join(status_parts)
            if parsed_coords:
                z_info = f", Z={parsed_coords['z']}mm" if parsed_coords.get("z") is not None else ""
                status_msg += f"\nParsed: X={x_val}, Y={y_val}{z_info}"
                if level_default:
                    status_msg += f" -> Level: {level_default}"

            logger.info(f"[on_generate] interactive: {len(family_choices)} families, {len(level_choices)} levels")
            return (query, {}, "", "",
                    gr.Textbox(visible=True, value=status_msg),
                    gr.Dropdown(visible=bool(family_choices), choices=family_choices,
                                value=family_choices[0] if family_choices else None),
                    gr.Radio(visible=bool(level_choices), choices=level_choices,
                                value=level_default),
                    gr.Number(visible=True, value=x_val),
                    gr.Number(visible=True, value=y_val),
                    gr.Button(visible=True),
                    gr.Textbox(visible=False),
                    None,
                    _step_indicator(2, MAIN_STEPS))
        except Exception:
            err = traceback.format_exc()
            logger.error(f"[on_generate] EXCEPTION:\n{err}")
            return (query, {}, "", "",
                    gr.Textbox(visible=True, value=f"Error:\n{err}"),
                    gr.Dropdown(visible=False, choices=[]),
                    gr.Radio(visible=False, choices=[]),
                    gr.Number(visible=False, value=0), gr.Number(visible=False, value=0),
                    gr.Button(visible=False),
                    gr.Textbox(visible=False), None,
                    _step_indicator(1, MAIN_STEPS))

    def on_confirm_selection(query, family, level, x, y):
        logger.info(f"[on_confirm] query={query!r} family={family!r} level={level!r} x={x} y={y}")
        try:
            selections = {}
            if family:
                selections["family_type"] = family
            if level:
                level_name = level.split(" (")[0] if " (" in level else level
                selections["level"] = level_name
            if x or y:
                selections["position"] = {"x": x, "y": y}

            logger.info(f"[on_confirm] selections={selections}")
            result = _generate_code(query, selections=selections)
            logger.info(f"[on_confirm] generate result keys={list(result.keys())}, code_len={len(result.get('code', ''))}")

            code = result.get("code", "")
            safe = result.get("safe", True)
            warnings = result.get("warnings", [])
            rag = result.get("rag_context", {})
            sec_text = "Safe" if safe else "Warning: " + "; ".join(warnings)

            return (selections, code, code,
                    gr.Textbox(visible=True, value=sec_text),
                    rag,
                    _step_indicator(3, MAIN_STEPS))
        except Exception:
            err = traceback.format_exc()
            logger.error(f"[on_confirm] EXCEPTION:\n{err}")
            return ({}, "", "",
                    gr.Textbox(visible=True, value=f"Error:\n{err}"),
                    None,
                    _step_indicator(2, MAIN_STEPS))

    def on_execute(code):
        logger.info(f"[on_execute] code_len={len(code) if code else 0}")
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
        """Solidify tool and auto-refresh tool list."""
        logger.info(f"[on_solidify] name={name!r} code_len={len(code) if code else 0}")
        try:
            if not name.strip():
                return ("Please enter a tool name.", _step_indicator(4, MAIN_STEPS),
                        on_refresh_tools())
            result = _solidify_tool(name, code, description, query)
            if "error" in result:
                return (f"Error: {result['error']}", _step_indicator(4, MAIN_STEPS),
                        on_refresh_tools())
            return (f"Solidified as '{result.get('name', name)}'",
                    _step_indicator(5, MAIN_STEPS),
                    on_refresh_tools())
        except Exception:
            return (f"Error:\n{traceback.format_exc()}", _step_indicator(4, MAIN_STEPS),
                    on_refresh_tools())

    # --- Tool Library ---

    def on_refresh_tools():
        tools = _list_tools()
        return [[t.get("name", ""), t.get("description", ""),
                 t.get("execution_count", 0), ", ".join(t.get("tags", []))]
                for t in tools]

    def on_select_tool(evt: gr.SelectData):
        if evt.value and evt.index[1] == 0:
            return evt.value
        return gr.Textbox()

    def on_load_choices_fetch(name):
        """Phase 1: Fetch tool params and choices from backend.

        Returns 4 values: choices_state, radio_names, input_names, choices_info.
        The .then() handler renders the actual components.
        """
        logger.info(f"[on_load_choices_fetch] name={name!r}")

        try:
            if not name or not name.strip():
                return {}, [], [], "Please select a tool first"

            tool_detail = _get_tool_detail(name)
            if not tool_detail:
                return {}, [], [], f"Tool '{name}' not found"

            all_params = tool_detail.get("parameters", [])
            logger.info(f"[on_load_choices_fetch] params={[p.get('name') for p in all_params]}")

            choices = _get_tool_choices(name)
            logger.info(f"[on_load_choices_fetch] choices keys={list(choices.keys())}")

            # Separate params into choice-based vs text input
            radio_names = []
            text_names = []
            # Build a config dict to pass via State
            config = {"radio_configs": [], "text_configs": []}

            for p in all_params:
                pname = p.get("name", "")
                pdesc = p.get("description", pname)

                if pname in choices and choices[pname]:
                    items = choices[pname]
                    labels = [it["label"] for it in items]
                    config["radio_configs"].append({
                        "label": f"{pname} — {pdesc}",
                        "choices": labels,
                        "default": labels[0] if labels else None,
                    })
                    radio_names.append(pname)
                elif "choices_from" in p:
                    config["text_configs"].append({"label": f"{pname} — {pdesc}", "default": ""})
                    text_names.append(pname)
                else:
                    default = str(p.get("default", "")) if "default" in p else ""
                    config["text_configs"].append({"label": f"{pname} — {pdesc}", "default": default})
                    text_names.append(pname)

            total = len(config["radio_configs"]) + len(config["text_configs"])
            if total > 0:
                parts = []
                for rn, rc in zip(radio_names, config["radio_configs"]):
                    parts.append(f"{rn}: {len(rc['choices'])} options")
                status = f"Loaded {total} parameter(s)"
                if parts:
                    status += f" — choices: {', '.join(parts)}"
                if text_names:
                    status += f" — text: {', '.join(text_names)}"
            else:
                status = "Tool has no parameters — click Run directly"

            # Store config in choices_state for the render phase
            choices["__config__"] = config
            logger.info(f"[on_load_choices_fetch] {len(radio_names)} radios + {len(text_names)} text inputs")
            return choices, radio_names, text_names, status
        except Exception:
            err = traceback.format_exc()
            logger.error(f"[on_load_choices_fetch] EXCEPTION:\n{err}")
            return {}, [], [], f"Error: {err[:200]}"

    def on_load_choices_render(choices_data, radio_names, input_names):
        """Phase 2: Render param components based on fetched data.

        Returns 9 values: radio1, radio2, input1-6, step_display.
        NOTE: Never set visible= on Dropdowns — Gradio 6 bug causes hang.
        Only update label, choices, value, interactive.
        """
        logger.info(f"[on_load_choices_render] radio_names={radio_names} input_names={input_names}")
        N_RADIOS, N_INPUTS = 2, 6
        config = (choices_data or {}).pop("__config__", {"radio_configs": [], "text_configs": []})

        radios = []
        for i in range(N_RADIOS):
            if i < len(config["radio_configs"]):
                rc = config["radio_configs"][i]
                radios.append(gr.Dropdown(
                    label=rc["label"], choices=rc["choices"],
                    value=rc["default"], interactive=True,
                ))
            else:
                radios.append(gr.Dropdown(
                    label="(no parameter)", choices=[], value=None, interactive=False,
                ))

        inputs = []
        for i in range(N_INPUTS):
            if i < len(config["text_configs"]):
                tc = config["text_configs"][i]
                inputs.append(gr.Textbox(
                    label=tc["label"], value=tc["default"], interactive=True,
                ))
            else:
                inputs.append(gr.Textbox(
                    label="(no parameter)", value="", interactive=False,
                ))

        logger.info(f"[on_load_choices_render] RETURNING {len(radios)} radios + {len(inputs)} inputs")
        return (*radios, *inputs, _step_indicator(3, TOOL_STEPS))

    def on_run_tool(name, choices_data, radio_names, input_names,
                    radio1, radio2,
                    inp1, inp2, inp3, inp4, inp5, inp6):
        """Run tool by merging radio selections + text inputs."""
        logger.info(f"[on_run_tool] name={name!r} radio_names={radio_names} input_names={input_names}")
        logger.info(f"[on_run_tool] radio1={radio1!r} radio2={radio2!r}")
        logger.info(f"[on_run_tool] inputs: inp1={inp1!r} inp2={inp2!r} inp3={inp3!r} inp4={inp4!r}")
        logger.info(f"[on_run_tool] choices_data keys={list((choices_data or {}).keys())}")

        if not name or not name.strip():
            return "Please select a tool.", _step_indicator(1, TOOL_STEPS)

        params = {}

        # Collect radio selections — map label back to value
        radio_values = [radio1, radio2]
        for i, pname in enumerate(radio_names or []):
            if i >= 2:
                break
            selected_label = radio_values[i]
            logger.info(f"[on_run_tool] radio[{i}] pname={pname!r} selected_label={selected_label!r}")
            if selected_label and pname in (choices_data or {}):
                for item in choices_data[pname]:
                    if item["label"] == selected_label:
                        params[pname] = item["value"]
                        logger.info(f"[on_run_tool] matched: {pname}={item['value']!r}")
                        break
                else:
                    params[pname] = selected_label
                    logger.info(f"[on_run_tool] no match, using label: {pname}={selected_label!r}")
            elif selected_label:
                # choices_data doesn't have this param — use raw value
                params[pname] = selected_label
                logger.info(f"[on_run_tool] no choices_data for {pname}, using raw: {selected_label!r}")
            else:
                logger.warning(f"[on_run_tool] radio[{i}] {pname} has NO selection!")

        # Collect text input values
        text_values = [inp1, inp2, inp3, inp4, inp5, inp6]
        for i, pname in enumerate(input_names or []):
            if i >= 6:
                break
            val = text_values[i]
            if val is not None and str(val).strip():
                params[pname] = str(val).strip()

        logger.info(f"[on_run_tool] final params={params}")

        if not params:
            return "No parameters provided. Please fill in the fields above.", _step_indicator(3, TOOL_STEPS)

        result = _run_tool(name, params)
        logger.info(f"[on_run_tool] result={result}")
        if result.get("success"):
            res_data = result.get("result", "")
            res_json = json.dumps(res_data, indent=2, ensure_ascii=False) if res_data else "(no result)"
            # Check if result contains an error status from the code template
            if isinstance(res_data, dict) and res_data.get("Status") == "Error":
                msg = f"Code executed but returned error:\n{res_json}"
            else:
                msg = f"Success\n{res_json}"
        else:
            msg = f"Failed: {result.get('error', 'Unknown')}"
        return msg, _step_indicator(4, TOOL_STEPS)

    # === Wire events ===
    refresh_btn.click(on_refresh_health, outputs=[revit_status])

    generate_btn.click(
        on_generate, inputs=[query_input],
        outputs=[current_query, current_selections, code_display, last_code,
                 selection_status, family_radio, level_radio,
                 x_input, y_input, confirm_selection_btn,
                 security_status, rag_info, step_display],
    )

    confirm_selection_btn.click(
        on_confirm_selection,
        inputs=[current_query, family_radio, level_radio, x_input, y_input],
        outputs=[current_selections, code_display, last_code, security_status,
                 rag_info, step_display],
    )

    execute_btn.click(on_execute, inputs=[code_display],
                      outputs=[exec_result, step_display])

    solidify_btn.click(
        on_solidify, inputs=[tool_name, tool_desc, last_code, current_query],
        outputs=[solidify_result, step_display, tools_table],
    )

    tools_refresh_btn.click(on_refresh_tools, outputs=[tools_table])
    tools_table.select(on_select_tool, outputs=[run_tool_name])

    load_choices_btn.click(
        on_load_choices_fetch, inputs=[run_tool_name],
        outputs=[tool_choices_state, tool_radio_names, tool_input_names,
                 tool_choices_info],
    ).then(
        on_load_choices_render,
        inputs=[tool_choices_state, tool_radio_names, tool_input_names],
        outputs=[tool_param_radio_1, tool_param_radio_2,
                 tool_param_input_1, tool_param_input_2, tool_param_input_3,
                 tool_param_input_4, tool_param_input_5, tool_param_input_6,
                 tool_step_display],
    )

    run_tool_btn.click(
        on_run_tool,
        inputs=[run_tool_name, tool_choices_state, tool_radio_names,
                tool_input_names,
                tool_param_radio_1, tool_param_radio_2,
                tool_param_input_1, tool_param_input_2, tool_param_input_3,
                tool_param_input_4, tool_param_input_5, tool_param_input_6],
        outputs=[run_tool_result, tool_step_display],
    )
