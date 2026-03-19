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
import re
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
        ver = data.get("bridge_version", "")
        ts = data.get("timestamp", "")
        if data.get("revit_connected"):
            latency = data.get("latency_ms", "?")
            proto = data.get("protocol", "TCP")
            endpoint = data.get("endpoint", "")
            return (
                f"Revit Connected | revit-mcp {ver} | "
                f"{proto} @ {endpoint} | {latency}ms | {ts}"
            )
        detail = data.get("detail", "")
        return f"Revit Disconnected: {detail} | revit-mcp {ver} | {ts}"
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
        # Step 1: Use LLM to parameterize hardcoded values into {placeholders}
        logger.info(f"[_solidify_tool] parameterizing code ({len(code)} chars)")
        param_resp = httpx.post(_bridge_url("/parameterize"),
                                json={"code": code, "source_query": source_query},
                                timeout=60)
        param_data = param_resp.json()
        param_code = param_data.get("code", code)
        parameters = param_data.get("parameters", [])
        logger.info(f"[_solidify_tool] parameterized: {len(parameters)} params extracted")

        # Step 2: Solidify with the parameterized code
        resp = httpx.post(_bridge_url("/solidify"),
                          json={"name": name, "code": param_code,
                                "description": description,
                                "parameters": parameters,
                                "source_query": source_query},
                          timeout=10)
        return resp.json()
    except Exception as e:
        logger.error(f"[_solidify_tool] error: {e}")
        return {"error": str(e)}


def _trigger_host_selection() -> list[dict]:
    """Trigger Revit selection mode and return selected elements."""
    try:
        resp = httpx.post(_bridge_url("/trigger-selection"), timeout=60)
        data = resp.json()
        return data.get("elements", [])
    except Exception as e:
        logger.error(f"[_trigger_host_selection] error: {e}")
        return []


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


def _match_tool(query: str) -> dict | None:
    """Check if a solidified tool matches the user query."""
    try:
        resp = httpx.post(_bridge_url("/match-tool"),
                          json={"query": query}, timeout=10)
        data = resp.json()
        if data.get("matched"):
            return data
        return None
    except Exception:
        return None


def _orchestrate(query: str, session_id: str | None = None) -> dict:
    """Call Intent Bridge orchestrator for full LLM intent analysis."""
    try:
        payload = {"query": query}
        if session_id:
            payload["session_id"] = session_id
        resp = httpx.post(_bridge_url("/orchestrate"),
                          json=payload, timeout=60)
        return resp.json()
    except Exception as e:
        logger.error(f"[_orchestrate] error: {e}")
        return {"error": str(e), "questions": []}


# ---------------------------------------------------------------------------
# Step indicator
# ---------------------------------------------------------------------------

def _step_md(current: int, labels: list[str], status: str = "",
             pipeline_log: list[str] | None = None) -> str:
    """Build an HTML progress bar with colored step indicators and pipeline log.

    Args:
        current: 1-based index of current step
        labels: step labels
        status: short status text for the active step
        pipeline_log: accumulated list of pipeline stage messages (shown as scrollable log)
    """
    parts = []
    for i, label in enumerate(labels, 1):
        if i < current:
            parts.append(
                f'<span style="color:#22c55e;font-weight:600">'
                f'&#10003; {i}.{label}</span>'
            )
        elif i == current:
            parts.append(
                f'<span style="color:#2563eb;font-weight:700;font-size:1.15em;'
                f'text-decoration:underline;text-underline-offset:4px">'
                f'&#9654; {i}.{label}</span>'
            )
        else:
            parts.append(
                f'<span style="color:#9ca3af">'
                f'{i}.{label}</span>'
            )
    bar = ' <span style="color:#d1d5db;font-size:1.1em"> &rarr; </span> '.join(parts)
    html = f'<div style="font-size:15px;line-height:1.8;padding:4px 0">{bar}</div>'

    # Pipeline log — accumulated stages with scroll
    if pipeline_log:
        log_lines = []
        for idx, msg in enumerate(pipeline_log):
            is_last = (idx == len(pipeline_log) - 1)
            if is_last:
                log_lines.append(f'<div class="active">&#9654; {msg}</div>')
            else:
                log_lines.append(f'<div class="done">&#10003; {msg}</div>')
        html += f'<div class="pipeline-log">{"".join(log_lines)}</div>'
    elif status:
        html += (
            f'<div style="margin-top:6px;padding:8px 12px;'
            f'background:#eff6ff;border-left:3px solid #2563eb;'
            f'border-radius:4px;color:#1e40af;font-size:14px">'
            f'{status}</div>'
        )
    return html


MAIN_STEPS = ["Input", "Select", "Review Code", "Execute", "Solidify"]
TOOL_STEPS = ["Select Tool", "Load Choices", "Set Params", "Run"]


# ---------------------------------------------------------------------------
# Tab D builder
# ---------------------------------------------------------------------------

def create_bridge_tab():
    """Create MCP Bridge tab contents (called inside gr.Tab)."""

    # --- Injected CSS ---
    gr.HTML("""<style>
/* Collapsible sections — use Gradio default accordion style */
.bridge-section {
    margin-bottom: 6px !important;
}

/* Thinking panel — fixed height, scrollable, collapses when empty */
.thinking-scroll {
    max-height: 200px;
    overflow-y: auto !important;
    padding: 10px 14px;
    background: #f8fafc;
    font-size: 13px;
    line-height: 1.6;
}
.thinking-scroll:empty,
.thinking-scroll > :first-child:empty {
    display: none;
}

/* Pipeline log — fixed height, scrollable, monospace */
.pipeline-log {
    max-height: 140px;
    overflow-y: auto !important;
    font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
    font-size: 12px;
    line-height: 1.7;
    padding: 8px 12px;
    background: #f0f4f8;
    border-radius: 4px;
    margin-top: 6px;
}
.pipeline-log .done { color: #16a34a; }
.pipeline-log .active { color: #2563eb; font-weight: 600; }

/* Step bar — always sticky at top */
.step-bar {
    position: sticky;
    top: 0;
    z-index: 50;
    background: white;
    padding: 6px 0;
    border-bottom: 1px solid #e5e7eb;
}

/* Elapsed timer — plain text like Gradio native "processing | 3.9s" */
.elapsed-timer {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-width: 0 !important;
    padding: 0 !important;
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
}
.elapsed-timer textarea, .elapsed-timer input {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #6b7280 !important;
    font-size: 12px !important;
    font-style: italic !important;
    text-align: right !important;
    padding: 0 !important;
    min-height: 0 !important;
    height: auto !important;
}
.elapsed-timer .wrap, .elapsed-timer .container {
    padding: 0 !important;
    min-height: 0 !important;
    gap: 0 !important;
}
</style>""")

    # === Header ===
    with gr.Row():
        revit_status = gr.Textbox(
            value="Revit Disconnected", label="Revit Status",
            interactive=False, max_lines=1, scale=4,
        )
        refresh_btn = gr.Button("Refresh", size="sm", scale=0)

    # === Settings ===
    with gr.Accordion("Settings", open=False):
        with gr.Row():
            unit_selector = gr.Radio(
                choices=["mm", "m", "feet"], value="mm",
                label="User Unit (auto-detected from project, can override)",
                interactive=True, scale=3,
            )
            detect_unit_btn = gr.Button("Re-detect from Revit", size="sm", scale=1)

    # === State ===
    current_query = gr.State("")
    current_selections = gr.State({})
    last_code = gr.State("")
    timer_start = gr.State(0.0)    # monotonic timestamp when pipeline started
    timer_active = gr.State(False) # whether timer is running

    # ── SECTION A: Code Generation Pipeline ──
    gr.Markdown("### Code Generation Pipeline")

    with gr.Row():
        step_display = gr.HTML(
            value=_step_md(1, MAIN_STEPS),
            elem_classes=["step-bar"],
            scale=5,
        )
        elapsed_display = gr.Textbox(
            value="", label="", show_label=False,
            interactive=False, max_lines=1, scale=0,
            elem_classes=["elapsed-timer"],
        )

    # Real-time timer driven by gr.Timer
    pipeline_timer = gr.Timer(value=0.5, active=False)

    # Step 1: Input
    with gr.Row():
        query_input = gr.Textbox(
            placeholder="e.g. 创建结构柱在100,0,0",
            show_label=False, scale=5, lines=1, max_lines=2,
        )
        generate_btn = gr.Button("Generate Code", variant="primary", scale=1)

    # Thinking Chain — always visible, fixed height, scrollable
    # NOTE: Do NOT wrap in Accordion or use visible=False — Gradio 6 cannot
    # toggle visibility inside Accordion during generator streaming.
    thinking_display = gr.Markdown(
        value="", elem_classes=["thinking-scroll"],
    )

    # Step 2: Selection controls — in Accordion for collapse/expand
    # Children stay visible=True; Accordion handles collapse.
    with gr.Accordion("Step 2: Select Options", open=False,
                       elem_classes=["bridge-section"]) as step2_accordion:
        selection_status = gr.Textbox(
            label="意图分类 / Classification",
            interactive=False, value="(waiting for intent classification)",
        )
        family_radio = gr.Dropdown(
            label="族类型 — select one (type to filter)",
            choices=[], interactive=True, filterable=True,
        )
        level_radio = gr.Radio(
            label="Level — select one", choices=[], interactive=True,
        )
        # Host element selection (for windows/doors on walls)
        with gr.Row(visible=False) as host_row:
            host_display = gr.Textbox(
                label="Host Element", interactive=False, scale=3,
                placeholder="Click 'Select Host' to pick a wall/floor in Revit",
            )
            select_host_btn = gr.Button("Select Host in Revit", variant="secondary", scale=1)
        host_element_id = gr.State(None)

        with gr.Row() as single_coord_row:
            x_input = gr.Number(label="X (mm)", value=0)
            y_input = gr.Number(label="Y (mm)", value=0)
        with gr.Row(visible=False) as multi_coord_row:
            coords_text = gr.Textbox(
                label="坐标 Coordinates (x,y; x,y; ...)",
                placeholder="例: 1000,0; 5000,0; 9000,0",
                lines=2,
            )
        quantity_state = gr.State(1)
        intent_meta = gr.State({})  # stores classification metadata for code generation

        # Orchestrator questions — pre-allocated Dropdowns for LLM-generated questions
        gr.Markdown("#### LLM 参数分析 / Orchestrator Questions",
                     elem_classes=["bridge-section"])
        orch_q1 = gr.Dropdown(label="(waiting for analysis)", choices=[],
                               interactive=False, allow_custom_value=True)
        orch_q2 = gr.Dropdown(label="(waiting for analysis)", choices=[],
                               interactive=False, allow_custom_value=True)
        orch_q3 = gr.Dropdown(label="(waiting for analysis)", choices=[],
                               interactive=False, allow_custom_value=True)
        orch_q4 = gr.Dropdown(label="(waiting for analysis)", choices=[],
                               interactive=False, allow_custom_value=True)
        orch_q5 = gr.Dropdown(label="(waiting for analysis)", choices=[],
                               interactive=False, allow_custom_value=True)
        orch_q6 = gr.Dropdown(label="(waiting for analysis)", choices=[],
                               interactive=False, allow_custom_value=True)
        orch_state = gr.State({})  # full orchestrator response

        confirm_selection_btn = gr.Button(
            "Confirm & Generate Code", variant="primary",
        )

    # Step 3: Review Code — collapsible
    with gr.Accordion("Step 3: Review Generated Code", open=True,
                       elem_classes=["bridge-section"]):
        code_display = gr.Code(
            language="cpp", label="Generated Code",
            interactive=True, lines=15,
        )
        security_status = gr.Textbox(
            label="Security Review", interactive=False, visible=False,
        )

    # Step 4: Execute — collapsible
    with gr.Accordion("Step 4: Execute", open=True,
                       elem_classes=["bridge-section"]):
        execute_btn = gr.Button("Execute in Revit", variant="primary")
        exec_result = gr.Textbox(
            label="Execution Result", interactive=False, lines=6,
        )

    # Step 5: Solidify — collapsible, default closed
    with gr.Accordion("Step 5: Save as Reusable Tool", open=False,
                       elem_classes=["bridge-section"]):
        with gr.Row():
            tool_name = gr.Textbox(label="Tool Name", placeholder="e.g. create_wall")
            tool_desc = gr.Textbox(label="Description", placeholder="What this tool does")
        solidify_btn = gr.Button("Solidify", variant="primary")
        solidify_result = gr.Textbox(label="Solidify Result", interactive=False)

    # RAG Context — collapsible, default closed
    with gr.Accordion("RAG Context", open=False,
                       elem_classes=["bridge-section"]):
        rag_info = gr.JSON(label="Retrieval Details")

    # ── SECTION B: Tool Library ──
    gr.Markdown("### Solidified Tool Library")

    tool_step_display = gr.Markdown(
        value=_step_md(1, TOOL_STEPS),
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

    def on_page_load():
        """Auto-detect Revit connection and project units on page load."""
        health = _check_revit_health()
        unit = "mm"  # default
        try:
            resp = httpx.get(_bridge_url("/project-units"), timeout=10)
            data = resp.json()
            if "detected" in data and "error" not in data:
                unit = data["detected"]
                httpx.post(_bridge_url("/unit"), json={"unit": unit}, timeout=5)
                logger.info(f"[on_page_load] auto-detected unit={unit}")
        except Exception as e:
            logger.warning(f"[on_page_load] unit detection failed: {e}")
        return health, gr.Radio(value=unit)

    def on_change_unit(unit):
        """Update backend unit preference."""
        try:
            resp = httpx.post(_bridge_url("/unit"), json={"unit": unit}, timeout=5)
            data = resp.json()
            logger.info(f"[on_change_unit] set unit={unit}, response={data}")
        except Exception as e:
            logger.error(f"[on_change_unit] error: {e}")
        return unit

    def on_detect_unit():
        """Query Revit project units and update selector."""
        try:
            resp = httpx.get(_bridge_url("/project-units"), timeout=10)
            data = resp.json()
            if "error" in data:
                return gr.Radio(value=data.get("current_setting", "mm"))
            detected = data.get("detected", "mm")
            display = data.get("display_name", "")
            logger.info(f"[on_detect_unit] detected={detected} display={display}")
            # Also set the backend unit
            httpx.post(_bridge_url("/unit"), json={"unit": detected}, timeout=5)
            return gr.Radio(value=detected)
        except Exception as e:
            logger.error(f"[on_detect_unit] error: {e}")
            return gr.Radio(value="mm")

    def _parse_sse_stream(resp):
        """Parse SSE stream, separate thinking from code progressively.

        Yields: ("progress", msg, None) | ("token", thinking, code) | ("done", dict, None)
        """
        thinking_buf = ""
        code_buf = ""
        full_buf = ""
        token_n = 0

        current_event = None
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                current_event = line[7:]
                continue
            if not line.startswith("data: "):
                continue
            data_str = line[6:]

            if current_event == "progress":
                try:
                    yield "progress", json.loads(data_str), None
                except (json.JSONDecodeError, TypeError):
                    pass
            elif current_event == "rag":
                pass  # info is in done event
            elif current_event == "token":
                try:
                    token = json.loads(data_str)
                    full_buf += token
                    token_n += 1

                    # --- Thinking: try closed tag first, then open ---
                    closed = re.search(
                        r'<thinking>(.*?)</thinking>', full_buf, re.DOTALL)
                    if closed:
                        thinking_buf = closed.group(1).strip()
                    else:
                        open_m = re.search(r'<thinking>(.*)', full_buf, re.DOTALL)
                        if open_m:
                            thinking_buf = open_m.group(1).strip()

                    # --- Code: after thinking block ---
                    after = re.sub(
                        r'<thinking>.*?</thinking>', '', full_buf, flags=re.DOTALL)
                    if '<thinking>' in after and '</thinking>' not in after:
                        after = after[:after.index('<thinking>')]
                    cm = re.search(
                        r'```(?:csharp|cs)?\s*\n(.*?)(?:```|$)', after, re.DOTALL)
                    if cm:
                        code_buf = cm.group(1).strip()

                    yield "token", thinking_buf, code_buf
                except (json.JSONDecodeError, TypeError):
                    pass
            elif current_event == "done":
                try:
                    yield "done", json.loads(data_str), None
                except (json.JSONDecodeError, TypeError):
                    pass

    def on_generate(query):
        """Streaming generator — yields progressive thinking + code updates.

        Yields 20 values: query, selections, code, last_code, thinking,
                          step2_accordion, selection controls (6),
                          host_row, host_element_id,
                          security, rag, exec_result, step_md,
                          run_tool_name, tool_choices_info.
        """
        import time as _time
        logger.info(f"[on_generate] query={query!r}")
        t_start = _time.monotonic()

        def _elapsed():
            return f"{_time.monotonic() - t_start:.1f}s"

        # 32-value tuple helper — resets all controls including exec_result + tool library
        # Selection controls are inside Accordion (always visible),
        # so we just clear their values instead of toggling visible.
        _ORCH_EMPTY = gr.Dropdown(label="(waiting for analysis)", choices=[],
                                   value=None, interactive=False)

        def _reset(step=1, status="", plog=None,
                   tool_name_val="", tool_info_val="",
                   open_select=False):
            return (query, {}, "", "",
                    "",  # thinking — clear
                    gr.Accordion(open=open_select),    # step2_accordion
                    gr.Textbox(value=""),             # selection_status
                    gr.Dropdown(choices=[]),           # family_radio
                    gr.Radio(choices=[]),              # level_radio
                    gr.Number(value=0),                # x_input
                    gr.Number(value=0),                # y_input
                    gr.Row(visible=True),              # single_coord_row
                    gr.Row(visible=False),             # multi_coord_row
                    gr.Textbox(value=""),              # coords_text
                    1,                                 # quantity_state
                    {},                                # intent_meta
                    gr.Button(interactive=True),       # confirm_btn
                    gr.Row(visible=False), None,       # host_row, host_id
                    gr.Textbox(visible=False), None,   # security, rag
                    "",                                # exec_result — clear
                    _step_md(step, MAIN_STEPS, status,
                             pipeline_log=plog),
                    tool_name_val,                     # run_tool_name
                    tool_info_val,                     # tool_choices_info
                    # Orchestrator questions (6 Dropdowns + 1 State)
                    _ORCH_EMPTY, _ORCH_EMPTY, _ORCH_EMPTY,
                    _ORCH_EMPTY, _ORCH_EMPTY, _ORCH_EMPTY,
                    {})                                # orch_state

        try:
            if not query.strip():
                yield _reset()
                return

            # --- Step 0: Check for matching solidified tool ---
            yield _reset(1, "Checking tool library...")

            matched = _match_tool(query)
            if matched and matched.get("has_params"):
                tool_name = matched["name"]
                display = matched.get("display_name", tool_name)
                params = matched.get("parameters", [])
                param_names = [p.get("name", "") for p in params]
                logger.info(f"[on_generate] TOOL MATCHED: {tool_name} "
                            f"params={param_names}")

                thinking_md = (
                    f"**Found existing tool: `{display}`**\n\n"
                    f"Description: {matched.get('description', '')}\n\n"
                    f"Parameters: {', '.join(param_names)}\n\n"
                    f"This tool has been used {matched.get('execution_count', 0)} "
                    f"time(s) before. Please review/modify the parameters below "
                    f"in the **Tool Library** section, then click **Run Tool**."
                )
                tool_info = (
                    f"Matched from query: '{query}' — "
                    f"click 'Load Parameters' to configure"
                )
                yield (query, {}, "", "",
                       thinking_md,
                       gr.Accordion(open=False),       # step2 — not needed
                       gr.Textbox(value=f"Existing tool found: {display}"),
                       gr.Dropdown(choices=[]),
                       gr.Radio(choices=[]),
                       gr.Number(value=0),
                       gr.Number(value=0),
                       gr.Row(visible=True),           # single_coord_row
                       gr.Row(visible=False),          # multi_coord_row
                       gr.Textbox(value=""),            # coords_text
                       1,                               # quantity_state
                       {},                              # intent_meta
                       gr.Button(interactive=True),
                       gr.Row(visible=False), None,
                       gr.Textbox(visible=False), None,
                       "",
                       _step_md(1, MAIN_STEPS,
                                status=f"Matched tool: {display}"),
                       tool_name,
                       tool_info,
                       _ORCH_EMPTY, _ORCH_EMPTY, _ORCH_EMPTY,
                       _ORCH_EMPTY, _ORCH_EMPTY, _ORCH_EMPTY,
                       {})
                return

            # --- Step 1: Intent Classification ---
            yield _reset(1, "Classifying intent...")

            intent = _classify_intent(query)
            itype = intent.get("interaction_type", "direct")
            logger.info(f"[on_generate] intent type={itype}")

            # --- Call orchestrator for ALL queries (not just interactive) ---
            # If orchestrator returns questions, override to interactive mode
            yield _reset(2, "LLM 深度分析中 / Orchestrator analyzing...")
            orch_data = _orchestrate(query)
            orch_questions = orch_data.get("questions", [])
            orch_intent = orch_data.get("intent", {})
            orch_error = orch_data.get("error")
            logger.info(f"[on_generate] orchestrator: {len(orch_questions)} questions, "
                       f"intent={orch_intent}, error={orch_error}")

            # Override: if orchestrator found questions, force interactive
            if itype == "direct" and orch_questions:
                logger.info(f"[on_generate] overriding direct -> select_family "
                           f"(orchestrator has {len(orch_questions)} questions)")
                itype = "select_family"
                # Also run Revit family query based on orchestrator intent
                orch_intent_name = orch_intent.get("name", "")
                if not intent.get("queries"):
                    # Build a default query from orchestrator data
                    action_plan = orch_data.get("action_plan", [])
                    cats = set()
                    for step in action_plan:
                        step_intent = step.get("intent", "").lower()
                        if "wall" in step_intent:
                            cats.add("OST_Walls")
                        if "room" in step_intent:
                            cats.add("OST_Rooms")
                        if "furniture" in step_intent or "bed" in step_intent:
                            cats.add("OST_Furniture")
                        if "door" in step_intent:
                            cats.add("OST_Doors")
                        if "window" in step_intent:
                            cats.add("OST_Windows")
                        if "column" in step_intent or "structural" in step_intent:
                            cats.add("OST_StructuralColumns")
                    if not cats:
                        cats = {"OST_Walls", "OST_Rooms"}  # sensible default
                    intent["queries"] = [{
                        "command": "get_available_family_types",
                        "params": {"categoryList": list(cats)},
                        "label": orch_intent.get("display_name", "族类型"),
                    }]
                    intent["need_level"] = True

            if itype == "direct":
                # --- Step 2: RAG + Streaming generation ---
                yield _reset(2, "Initializing pipeline...")

                try:
                    # Helper to build the 32-value tuple for streaming yields
                    def _stream_yield(code, thinking_md,
                                      sec_text=None, rag=None, step=2,
                                      plog=None, status=""):
                        return (query, {}, code, code,
                                thinking_md,
                                gr.Accordion(open=False),     # step2_accordion
                                gr.Textbox(value=""),        # selection_status
                                gr.Dropdown(choices=[]),      # family
                                gr.Radio(choices=[]),         # level
                                gr.Number(value=0),           # x
                                gr.Number(value=0),           # y
                                gr.Row(visible=True),         # single_coord_row
                                gr.Row(visible=False),        # multi_coord_row
                                gr.Textbox(value=""),         # coords_text
                                1,                            # quantity_state
                                {},                           # intent_meta
                                gr.Button(interactive=True),  # confirm
                                gr.Row(visible=False), None,
                                gr.Textbox(visible=bool(sec_text), value=sec_text or ""),
                                rag,
                                "",                           # exec_result
                                _step_md(step, MAIN_STEPS, status=status,
                                         pipeline_log=plog),
                                "", "",                       # tool_name, tool_info
                                _ORCH_EMPTY, _ORCH_EMPTY, _ORCH_EMPTY,
                                _ORCH_EMPTY, _ORCH_EMPTY, _ORCH_EMPTY,
                                {})

                    with httpx.stream(
                        "POST", _bridge_url("/generate-stream"),
                        json={"query": query, "api_top_k": 15, "code_top_k": 5},
                        timeout=120,
                    ) as resp:
                        resp.raise_for_status()
                        final_code = ""
                        final_thinking = ""
                        done_info = None
                        last_yield = 0.0
                        token_count = 0
                        pipeline_log: list[str] = []  # accumulated stages

                        for event_type, data1, data2 in _parse_sse_stream(resp):
                            elapsed = _time.monotonic() - t_start
                            elapsed_str = f"{elapsed:.1f}s"

                            if event_type == "progress":
                                # Append to log (replace last if it was active)
                                msg = f"{data1} ({elapsed_str})"
                                pipeline_log.append(msg)
                                thinking_md = (f"**Thinking:**\n\n{final_thinking}"
                                               if final_thinking else "")
                                yield _stream_yield(
                                    final_code, thinking_md,
                                    plog=pipeline_log)
                                continue

                            if event_type == "token":
                                final_thinking = data1
                                final_code = data2
                                token_count += 1
                                now = _time.monotonic()
                                # First token yields immediately, then throttle 0.15s
                                if token_count > 1 and now - last_yield < 0.15:
                                    continue
                                last_yield = now

                                thinking_md = (f"**Thinking:**\n\n{final_thinking}"
                                               if final_thinking
                                               else "*Waiting for LLM response...*")
                                code_lines = final_code.count('\n') + 1 if final_code else 0
                                # Update the last log entry with generation stats
                                gen_msg = (f"LLM generating... "
                                           f"{code_lines} lines, {token_count} tokens ({elapsed_str})")
                                # Replace ongoing generation entry
                                gen_log = [l for l in pipeline_log
                                           if not l.startswith("LLM generating")]
                                gen_log.append(gen_msg)
                                yield _stream_yield(
                                    final_code, thinking_md,
                                    plog=gen_log)

                            elif event_type == "done":
                                done_info = data1

                    # Final yield — Step 3
                    elapsed = _time.monotonic() - t_start
                    elapsed_str = f"{elapsed:.1f}s"
                    if done_info:
                        final_code = done_info.get("code", final_code)
                        safe = done_info.get("safe", True)
                        warnings = done_info.get("warnings", [])
                        rag = done_info.get("rag_context", {})
                        sec_text = "Safe" if safe else "Warning: " + "; ".join(warnings)
                    else:
                        sec_text = "Safe"
                        rag = {}

                    # Build final log
                    final_log = [l for l in pipeline_log
                                 if not l.startswith("LLM generating")]
                    code_lines = final_code.count('\n') + 1 if final_code else 0
                    final_log.append(f"LLM generation complete — {code_lines} lines ({elapsed_str})")
                    final_log.append(f"Code extracted & security reviewed — {sec_text}")

                    thinking_md = f"**Thinking:**\n\n{final_thinking}" if final_thinking else ""
                    yield _stream_yield(
                        final_code, thinking_md,
                        sec_text=sec_text, rag=rag, step=3,
                        plog=final_log)
                except Exception:
                    err = traceback.format_exc()
                    logger.error(f"[on_generate] stream error:\n{err}")
                    yield _reset(2, f"Error: {err[:200]}")
                return

            # === Interactive: query Revit ===
            need_host = (itype == "select_both")
            select_prompt = intent.get("select_prompt", "")

            # --- Progress: Querying Revit ---
            yield _reset(2, "Querying Revit for family types...")

            family_choices = []
            level_choices = []
            levels_raw = []
            status_parts = []
            parsed_coords = intent.get("parsed_coords")

            # Extract label & categories from LLM classification
            queries = intent.get("queries", [])
            family_label = "族类型"
            queried_categories = []
            for q in queries:
                cmd = q.get("command")
                params = q.get("params", {})
                label = q.get("label", cmd)
                family_label = label  # use LLM's label
                cats = params.get("categoryList", [])
                queried_categories.extend(cats)
                data = _query_revit(cmd, params)
                logger.info(f"[on_generate] query_revit cmd={cmd} "
                            f"categories={cats} "
                            f"result_type={type(data).__name__} "
                            f"len={len(data) if isinstance(data, list) else 'N/A'} "
                            f"sample={str(data[:2]) if isinstance(data, list) and data else data}")

                if cmd == "get_available_family_types" and isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            type_name = (item.get("TypeName")
                                         or item.get("typeName")
                                         or item.get("name")
                                         or item.get("Name")
                                         or str(item))
                            family_choices.append(type_name)
                        else:
                            family_choices.append(str(item))
                    status_parts.append(f"{label}: {len(family_choices)}")

            if intent.get("need_level"):
                yield _reset(2, "Querying levels...")
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

            x_val = parsed_coords["x"] if parsed_coords else 0
            y_val = parsed_coords["y"] if parsed_coords else 0

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

            # Build status message with classification details
            cat_display = ", ".join(c.replace("OST_", "") for c in queried_categories)
            status_msg = f"分类: {family_label}"
            if cat_display:
                status_msg += f" ({cat_display})"
            if status_parts:
                status_msg += f"\nRevit 查询结果: {' | '.join(status_parts)}"
            if not family_choices:
                status_msg += "\n⚠ 未找到匹配的族类型，请在 Revit 中加载对应族文件"
            if need_host and select_prompt:
                status_msg += f"\n{select_prompt}"
            if parsed_coords:
                z_info = f", Z={parsed_coords['z']}mm" if parsed_coords.get("z") is not None else ""
                status_msg += f"\nParsed: X={x_val}, Y={y_val}{z_info}"
                if level_default:
                    status_msg += f" -> Level: {level_default}"

            quantity = intent.get("quantity", 1)
            is_multi = quantity > 1
            if is_multi:
                status_msg += f"\nQuantity: {quantity} — please enter {quantity} coordinate pairs"
            logger.info(f"[on_generate] interactive: family_choices={len(family_choices)} "
                       f"level_choices={len(level_choices)} quantity={quantity}")

            # Build thinking markdown from orchestrator analysis (orch_data already fetched above)
            thinking_parts = []
            if orch_intent and not orch_error:
                intent_name = orch_intent.get("name", orch_intent.get("intent", ""))
                display_name = orch_intent.get("display_name", "")
                confidence = orch_intent.get("confidence", 0)
                thinking_parts.append(
                    f"**Intent**: `{intent_name}`"
                    + (f" — {display_name}" if display_name else "")
                    + f" (confidence: {confidence})"
                )
                # Show action plan for composite intents (from top-level response)
                action_plan = orch_data.get("action_plan", [])
                if action_plan:
                    thinking_parts.append(f"**Composite**: {len(action_plan)} steps")
                    for ap in action_plan:
                        step_n = ap.get("step", "?")
                        disp = ap.get("display_name", "")
                        api = ap.get("api_method", "")
                        desc = ap.get("description", "")
                        thinking_parts.append(f"  - Step {step_n}: **{disp}** (`{api}`)\n    {desc}")
                summary = orch_data.get("summary", "")
                if summary:
                    thinking_parts.append(f"\n**Summary**: {summary}")
            elif orch_error:
                thinking_parts.append(f"Orchestrator error: {orch_error}")
            thinking_md = "\n".join(thinking_parts) if thinking_parts else ""

            if orch_questions:
                status_msg += f"\nLLM 分析: {len(orch_questions)} 个参数需要确认"

            # Build orchestrator question Dropdowns (up to 6)
            orch_dd = []
            for i in range(6):
                if i < len(orch_questions):
                    q = orch_questions[i]
                    q_text = q.get("text", f"Question {i+1}")
                    q_opts = q.get("options", [])
                    orch_dd.append(gr.Dropdown(
                        label=q_text, choices=q_opts,
                        value=None, interactive=True,
                        allow_custom_value=q.get("allow_custom", True),
                    ))
                else:
                    orch_dd.append(gr.Dropdown(
                        label="(waiting for analysis)", choices=[],
                        value=None, interactive=False,
                    ))

            # When orchestrator has questions, hide redundant old controls
            has_orch = bool(orch_questions)
            yield (query, {}, "", "",
                   thinking_md,
                   gr.Accordion(open=True),          # auto-open Step 2
                   gr.Textbox(value=status_msg),
                   gr.Dropdown(choices=[] if has_orch else family_choices,
                               value=None if has_orch else (family_choices[0] if family_choices else None),
                               label="(由 LLM 分析控制)" if has_orch
                                     else f"{family_label} — select one (type to filter)",
                               interactive=not has_orch),
                   gr.Radio(choices=[] if has_orch else level_choices,
                            value=None if has_orch else level_default,
                            interactive=not has_orch),
                   gr.Number(value=x_val),
                   gr.Number(value=y_val),
                   gr.Row(visible=False if has_orch else not is_multi),  # single_coord_row
                   gr.Row(visible=False if has_orch else is_multi),      # multi_coord_row
                   gr.Textbox(                       # coords_text
                       value="",
                       placeholder=f"请输入 {quantity} 组坐标，例: 1000,0; 5000,0"
                           if is_multi else "",
                   ),
                   quantity,                          # quantity_state
                   {"label": family_label,            # intent_meta
                    "categories": queried_categories,
                    "interaction_type": itype},
                   gr.Button(interactive=True),
                   gr.Row(visible=need_host), None,
                   gr.Textbox(visible=False),
                   None,
                   "",  # exec_result — clear
                   _step_md(2, MAIN_STEPS),
                   "", "",  # tool_name, tool_info
                   # Orchestrator questions + state
                   *orch_dd,
                   orch_data)
        except Exception:
            err = traceback.format_exc()
            logger.error(f"[on_generate] EXCEPTION:\n{err}")
            yield _reset(1, f"Error: {err[:200]}")

    def on_select_host():
        """Trigger Revit selection mode — user clicks a wall/floor in Revit."""
        logger.info("[on_select_host] triggering Revit selection")
        elements = _trigger_host_selection()
        if elements and len(elements) > 0:
            el = elements[0]  # take first selected element
            el_id = el.get("Id", el.get("id", ""))
            el_name = el.get("Name", el.get("name", "?"))
            el_cat = el.get("Category", el.get("category", ""))
            display = f"{el_name} (ID: {el_id}, {el_cat})" if el_cat else f"{el_name} (ID: {el_id})"
            logger.info(f"[on_select_host] selected: {display}")
            return display, el_id
        logger.warning("[on_select_host] no element selected")
        return "No element selected — try again", None

    def on_confirm_selection(query, family, level, x, y,
                             coords_text_val, quantity, intent_meta_val, host_id,
                             oq1, oq2, oq3, oq4, oq5, oq6, orch_data):
        """Generator — uses SSE streaming for real-time thinking + timer.

        Yields 7 values: selections, code, last_code, thinking,
                         security_status, rag_info, step_display.
        """
        import time as _time
        t0 = _time.monotonic()
        logger.info(f"[on_confirm] query={query!r} family={family!r} "
                    f"level={level!r} x={x} y={y} "
                    f"coords_text={coords_text_val!r} quantity={quantity} "
                    f"intent_meta={intent_meta_val} host_id={host_id}")

        def _el():
            return f"{_time.monotonic() - t0:.1f}s"

        def _out(sels, code, thinking="", sec_text="", rag=None,
                 step=2, plog=None, status=""):
            return (sels, code, code, thinking,
                    gr.Textbox(visible=bool(sec_text), value=sec_text),
                    rag,
                    _step_md(step, MAIN_STEPS, status=status,
                             pipeline_log=plog))

        try:
            selections = {}
            if family:
                selections["family_type"] = family
            if level:
                level_name = level.split(" (")[0] if " (" in level else level
                selections["level"] = level_name

            qty = quantity if isinstance(quantity, int) else 1
            if qty > 1 and coords_text_val and coords_text_val.strip():
                # Parse semicolon-separated coordinates: "x,y; x,y; ..."
                positions = []
                for pair in coords_text_val.split(";"):
                    pair = pair.strip()
                    if not pair:
                        continue
                    parts = pair.split(",")
                    if len(parts) >= 2:
                        try:
                            positions.append({
                                "x": float(parts[0].strip()),
                                "y": float(parts[1].strip()),
                            })
                        except ValueError:
                            continue
                if positions:
                    selections["positions"] = positions
                    selections["quantity"] = len(positions)
            elif x or y:
                selections["position"] = {"x": x, "y": y}

            if host_id:
                selections["host_element_id"] = host_id

            # Pass classification metadata to code generator
            meta = intent_meta_val if isinstance(intent_meta_val, dict) else {}
            if meta.get("categories"):
                selections["_revit_categories"] = meta["categories"]
            if meta.get("label"):
                selections["_classification_label"] = meta["label"]

            # Collect orchestrator question answers
            orch_answers = {}
            orch_questions = (orch_data or {}).get("questions", [])
            oq_values = [oq1, oq2, oq3, oq4, oq5, oq6]
            for i, q in enumerate(orch_questions):
                if i >= 6:
                    break
                selected = oq_values[i]
                if selected is None or selected == "":
                    continue
                slot = q.get("slot", f"q{i}")
                # Map display option back to value
                options = q.get("options", [])
                values = q.get("values", [])
                if selected in options and values:
                    idx = options.index(selected)
                    if idx < len(values):
                        orch_answers[slot] = values[idx]
                    else:
                        orch_answers[slot] = selected
                else:
                    orch_answers[slot] = selected  # custom input
            if orch_answers:
                selections["_orchestrator_answers"] = orch_answers
                logger.info(f"[on_confirm] orch_answers={orch_answers}")

            # Pass orchestrator intent/action_plan for richer code generation
            orch_intent = (orch_data or {}).get("intent", {})
            if orch_intent:
                selections["_orchestrator_intent"] = orch_intent

            logger.info(f"[on_confirm] selections={selections}")

            # Use SSE streaming — same as direct path
            pipeline_log: list[str] = ["Building selections..."]
            yield _out(selections, "", step=2, plog=pipeline_log)

            final_code = ""
            final_thinking = ""
            done_info = None
            last_yield = 0.0
            token_count = 0

            with httpx.stream(
                "POST", _bridge_url("/generate-stream"),
                json={"query": query, "selections": selections,
                      "api_top_k": 15, "code_top_k": 5},
                timeout=120,
            ) as resp:
                resp.raise_for_status()
                for event_type, data1, data2 in _parse_sse_stream(resp):
                    if event_type == "progress":
                        msg = f"{data1} ({_el()})"
                        pipeline_log.append(msg)
                        thinking_md = (f"**Thinking:**\n\n{final_thinking}"
                                       if final_thinking else "")
                        yield _out(selections, final_code, thinking_md,
                                   step=2, plog=pipeline_log)

                    elif event_type == "token":
                        final_thinking = data1
                        final_code = data2
                        token_count += 1
                        now = _time.monotonic()
                        if token_count > 1 and now - last_yield < 0.25:
                            continue
                        last_yield = now
                        thinking_md = (f"**Thinking:**\n\n{final_thinking}"
                                       if final_thinking
                                       else "*Waiting for LLM response...*")
                        code_lines = final_code.count('\n') + 1 if final_code else 0
                        gen_msg = (f"LLM generating... {code_lines} lines, "
                                   f"{token_count} tokens ({_el()})")
                        gen_log = [l for l in pipeline_log
                                   if not l.startswith("LLM generating")]
                        gen_log.append(gen_msg)
                        yield _out(selections, final_code, thinking_md,
                                   step=2, plog=gen_log)

                    elif event_type == "done":
                        done_info = data1

            # Final
            if done_info:
                final_code = done_info.get("code", final_code)
                safe = done_info.get("safe", True)
                warnings = done_info.get("warnings", [])
                rag = done_info.get("rag_context", {})
                sec_text = "Safe" if safe else "Warning: " + "; ".join(warnings)
            else:
                sec_text = "Safe"
                rag = {}

            final_log = [l for l in pipeline_log
                         if not l.startswith("LLM generating")]
            code_lines = final_code.count('\n') + 1 if final_code else 0
            final_log.append(f"LLM generation complete — {code_lines} lines ({_el()})")
            final_log.append(f"Code extracted & security reviewed — {sec_text}")

            thinking_md = f"**Thinking:**\n\n{final_thinking}" if final_thinking else ""
            yield _out(selections, final_code, thinking_md,
                       sec_text=sec_text, rag=rag, step=3, plog=final_log)
        except Exception:
            err = traceback.format_exc()
            logger.error(f"[on_confirm] EXCEPTION:\n{err}")
            yield _out({}, "", sec_text=f"Error:\n{err[:200]}",
                       step=2, status=f"Error: {err[:100]}")

    def on_execute(code):
        """Generator — yields progress to avoid 'processing' overlay."""
        import time as _time
        t0 = _time.monotonic()
        logger.info(f"[on_execute] code_len={len(code) if code else 0}")
        if not code.strip():
            yield "No code to execute.", _step_md(3, MAIN_STEPS)
            return
        yield ("Sending code to Revit...",
               _step_md(4, MAIN_STEPS, status="Executing in Revit..."))
        result = _execute_code(code)
        el = f"{_time.monotonic() - t0:.1f}s"
        if result.get("success"):
            res = result.get("result", "")
            msg = f"✓ 执行成功 ({el})\n{json.dumps(res, indent=2, ensure_ascii=False) if res else '(no result)'}"
        else:
            error = result.get("error", "Unknown error")
            logger.error(f"[on_execute] FAILED: {error}")
            msg = f"✗ 执行失败 ({el}): {error}"
        yield msg, _step_md(4, MAIN_STEPS)

    def on_solidify(name, description, code, query):
        """Solidify tool and auto-refresh tool list."""
        logger.info(f"[on_solidify] name={name!r} code_len={len(code) if code else 0}")
        try:
            if not name.strip():
                return ("Please enter a tool name.", _step_md(4, MAIN_STEPS),
                        on_refresh_tools())
            result = _solidify_tool(name, code, description, query)
            if "error" in result:
                return (f"Error: {result['error']}", _step_md(4, MAIN_STEPS),
                        on_refresh_tools())
            return (f"Solidified as '{result.get('name', name)}'",
                    _step_md(5, MAIN_STEPS),
                    on_refresh_tools())
        except Exception:
            return (f"Error:\n{traceback.format_exc()}", _step_md(4, MAIN_STEPS),
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
        config = (choices_data or {}).get("__config__", {"radio_configs": [], "text_configs": []})

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
        return (*radios, *inputs, _step_md(3, TOOL_STEPS))

    def on_run_tool(name, choices_data, radio_names, input_names,
                    radio1, radio2,
                    inp1, inp2, inp3, inp4, inp5, inp6):
        """Run tool by merging radio selections + text inputs."""
        logger.info(f"[on_run_tool] name={name!r} radio_names={radio_names} input_names={input_names}")
        logger.info(f"[on_run_tool] radio1={radio1!r} radio2={radio2!r}")
        logger.info(f"[on_run_tool] inputs: inp1={inp1!r} inp2={inp2!r} inp3={inp3!r} inp4={inp4!r}")
        logger.info(f"[on_run_tool] choices_data keys={list((choices_data or {}).keys())}")

        if not name or not name.strip():
            return "Please select a tool.", _step_md(1, TOOL_STEPS)

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
            error = result.get("error") or result.get("detail") or "Unknown"
            msg = f"Failed: {error}"
        return msg, _step_md(4, TOOL_STEPS)

    # === Timer handlers ===
    def on_timer_start():
        """Start the pipeline timer."""
        import time as _time
        return _time.monotonic(), gr.Timer(active=True), "processing | 0.0s"

    def on_timer_tick(start_ts):
        """Update elapsed display every 0.5s."""
        import time as _time
        if not start_ts:
            return ""
        elapsed = _time.monotonic() - start_ts
        return f"processing | {elapsed:.1f}s"

    def on_timer_stop(start_ts):
        """Stop the timer and show final elapsed."""
        import time as _time
        elapsed = _time.monotonic() - start_ts if start_ts else 0.0
        return gr.Timer(active=False), f"completed | {elapsed:.1f}s"

    # === Wire events ===
    refresh_btn.click(on_page_load, outputs=[revit_status, unit_selector])
    unit_selector.change(on_change_unit, inputs=[unit_selector], outputs=[unit_selector])
    detect_unit_btn.click(on_detect_unit, outputs=[unit_selector])

    # Timer tick — updates elapsed display while active
    pipeline_timer.tick(
        on_timer_tick, inputs=[timer_start],
        outputs=[elapsed_display],
    )

    generate_btn.click(
        # Phase 0: start timer
        on_timer_start, outputs=[timer_start, pipeline_timer, elapsed_display],
    ).then(
        on_generate, inputs=[query_input],
        outputs=[current_query, current_selections, code_display, last_code,
                 thinking_display,
                 step2_accordion,
                 selection_status, family_radio, level_radio,
                 x_input, y_input,
                 single_coord_row, multi_coord_row, coords_text, quantity_state,
                 intent_meta,
                 confirm_selection_btn,
                 host_row, host_element_id,
                 security_status, rag_info,
                 exec_result, step_display,
                 run_tool_name, tool_choices_info,
                 orch_q1, orch_q2, orch_q3, orch_q4, orch_q5, orch_q6,
                 orch_state],
    ).then(
        # Stop timer after generation completes
        on_timer_stop, inputs=[timer_start],
        outputs=[pipeline_timer, elapsed_display],
    ).then(
        # Auto-load tool parameters when a tool was matched
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

    select_host_btn.click(
        on_select_host,
        outputs=[host_display, host_element_id],
    )

    confirm_selection_btn.click(
        on_confirm_selection,
        inputs=[current_query, family_radio, level_radio, x_input, y_input,
                coords_text, quantity_state, intent_meta, host_element_id,
                orch_q1, orch_q2, orch_q3, orch_q4, orch_q5, orch_q6,
                orch_state],
        outputs=[current_selections, code_display, last_code, thinking_display,
                 security_status, rag_info, step_display],
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
