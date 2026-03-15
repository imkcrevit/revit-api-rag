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


# ---------------------------------------------------------------------------
# Step indicator
# ---------------------------------------------------------------------------

def _step_md(current: int, labels: list[str], status: str = "",
             pipeline_log: list[str] | None = None,
             elapsed: str = "") -> str:
    """Build an HTML progress bar with colored step indicators and pipeline log.

    Args:
        current: 1-based index of current step
        labels: step labels
        status: short status text for the active step
        pipeline_log: accumulated list of pipeline stage messages (shown as scrollable log)
        elapsed: elapsed time string (e.g. "12s")
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
    # Timer badge — JS auto-incrementing when running, static when done
    timer_html = ""
    if elapsed:
        try:
            elapsed_sec = float(elapsed.rstrip("s"))
        except (ValueError, AttributeError):
            elapsed_sec = 0.0
        # Unique ID per render to avoid conflicts
        tid = f"timer_{id(elapsed) % 100000}"
        timer_html = (
            f'<span id="{tid}" style="float:right;background:#2563eb;color:white;'
            f'padding:2px 10px;border-radius:12px;font-size:13px;'
            f'font-weight:600;letter-spacing:0.5px">'
            f'&#9201; {elapsed}</span>'
            f'<script>'
            f'(function(){{'
            f'var el=document.getElementById("{tid}");'
            f'if(!el)return;'
            f'var t={elapsed_sec:.1f};'
            f'if(el._iv)clearInterval(el._iv);'
            f'el._iv=setInterval(function(){{'
            f't+=0.1;el.textContent="⏱ "+t.toFixed(1)+"s";'
            f'}},100);'
            f'}})()'
            f'</script>'
        )
    html = f'<div style="font-size:15px;line-height:1.8;padding:4px 0">{timer_html}{bar}</div>'

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

    # ── SECTION A: Code Generation Pipeline ──
    gr.Markdown("### Code Generation Pipeline")

    step_display = gr.HTML(
        value=_step_md(1, MAIN_STEPS),
        elem_classes=["step-bar"],
    )

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
                       elem_classes=["bridge-section"]):
        selection_status = gr.Textbox(
            label="Query Result (from Revit)",
            interactive=False, value="(waiting for intent classification)",
        )
        family_radio = gr.Dropdown(
            label="Family Type — select one (type to filter)",
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

        with gr.Row():
            x_input = gr.Number(label="X (mm)", value=0)
            y_input = gr.Number(label="Y (mm)", value=0)
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

        Yields 16 values: query, selections, code, last_code, thinking,
                          selection controls (6), host_row, host_element_id,
                          security, rag, step_md.
        """
        import time as _time
        logger.info(f"[on_generate] query={query!r}")
        t_start = _time.monotonic()

        def _elapsed():
            return f"{_time.monotonic() - t_start:.1f}s"

        # 16-value tuple helper — resets all controls
        # Selection controls are inside Accordion (always visible),
        # so we just clear their values instead of toggling visible.
        def _reset(step=1, status="", plog=None):
            return (query, {}, "", "",
                    "",  # thinking — clear
                    gr.Textbox(value=""),             # selection_status
                    gr.Dropdown(choices=[]),           # family_radio
                    gr.Radio(choices=[]),              # level_radio
                    gr.Number(value=0),                # x_input
                    gr.Number(value=0),                # y_input
                    gr.Button(interactive=True),       # confirm_btn
                    gr.Row(visible=False), None,       # host_row, host_id
                    gr.Textbox(visible=False), None,   # security, rag
                    _step_md(step, MAIN_STEPS, status,
                             pipeline_log=plog, elapsed=_elapsed()))

        try:
            if not query.strip():
                yield _reset()
                return

            # --- Step 1: Intent Classification ---
            yield _reset(1, "Classifying intent...")

            intent = _classify_intent(query)
            itype = intent.get("interaction_type", "direct")
            logger.info(f"[on_generate] intent type={itype}")

            if itype == "direct":
                # --- Step 2: RAG + Streaming generation ---
                yield _reset(2, "Initializing pipeline...")

                try:
                    # Helper to build the 16-value tuple for streaming yields
                    def _stream_yield(code, thinking_md,
                                      sec_text=None, rag=None, step=2,
                                      plog=None, status=""):
                        return (query, {}, code, code,
                                thinking_md,
                                gr.Textbox(value=""),        # selection_status
                                gr.Dropdown(choices=[]),      # family
                                gr.Radio(choices=[]),         # level
                                gr.Number(value=0),           # x
                                gr.Number(value=0),           # y
                                gr.Button(interactive=True),  # confirm
                                gr.Row(visible=False), None,
                                gr.Textbox(visible=bool(sec_text), value=sec_text or ""),
                                rag,
                                _step_md(step, MAIN_STEPS, status=status,
                                         pipeline_log=plog, elapsed=_elapsed()))

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
                            elapsed_str = f"{elapsed:.0f}s"

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
                                # First token yields immediately, then throttle 0.25s
                                if token_count > 1 and now - last_yield < 0.25:
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
                    elapsed_str = f"{elapsed:.0f}s"
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

            for q in intent.get("queries", []):
                cmd = q.get("command")
                params = q.get("params", {})
                label = q.get("label", cmd)
                data = _query_revit(cmd, params)

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

            status_msg = "Queried from Revit: " + " | ".join(status_parts)
            if need_host and select_prompt:
                status_msg += f"\n{select_prompt}"
            if parsed_coords:
                z_info = f", Z={parsed_coords['z']}mm" if parsed_coords.get("z") is not None else ""
                status_msg += f"\nParsed: X={x_val}, Y={y_val}{z_info}"
                if level_default:
                    status_msg += f" -> Level: {level_default}"

            yield (query, {}, "", "",
                   "",  # thinking — clear
                   gr.Textbox(value=status_msg),
                   gr.Dropdown(choices=family_choices,
                               value=family_choices[0] if family_choices else None),
                   gr.Radio(choices=level_choices, value=level_default),
                   gr.Number(value=x_val),
                   gr.Number(value=y_val),
                   gr.Button(interactive=True),
                   gr.Row(visible=need_host), None,
                   gr.Textbox(visible=False),
                   None,
                   _step_md(2, MAIN_STEPS, elapsed=_elapsed()))
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

    def on_confirm_selection(query, family, level, x, y, host_id):
        """Generator — uses SSE streaming for real-time thinking + timer.

        Yields 7 values: selections, code, last_code, thinking,
                         security_status, rag_info, step_display.
        """
        import time as _time
        t0 = _time.monotonic()
        logger.info(f"[on_confirm] query={query!r} family={family!r} "
                    f"level={level!r} x={x} y={y} host_id={host_id}")

        def _el():
            return f"{_time.monotonic() - t0:.1f}s"

        def _out(sels, code, thinking="", sec_text="", rag=None,
                 step=2, plog=None, status=""):
            return (sels, code, code, thinking,
                    gr.Textbox(visible=bool(sec_text), value=sec_text),
                    rag,
                    _step_md(step, MAIN_STEPS, status=status,
                             pipeline_log=plog, elapsed=_el()))

        try:
            selections = {}
            if family:
                selections["family_type"] = family
            if level:
                level_name = level.split(" (")[0] if " (" in level else level
                selections["level"] = level_name
            if x or y:
                selections["position"] = {"x": x, "y": y}
            if host_id:
                selections["host_element_id"] = host_id

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
               _step_md(4, MAIN_STEPS, status="Executing in Revit...",
                        elapsed=f"{_time.monotonic() - t0:.1f}s"))
        result = _execute_code(code)
        el = f"{_time.monotonic() - t0:.1f}s"
        if result.get("success"):
            res = result.get("result", "")
            msg = f"✓ 执行成功 ({el})\n{json.dumps(res, indent=2, ensure_ascii=False) if res else '(no result)'}"
        else:
            error = result.get("error", "Unknown error")
            logger.error(f"[on_execute] FAILED: {error}")
            msg = f"✗ 执行失败 ({el}): {error}"
        yield msg, _step_md(4, MAIN_STEPS, elapsed=el)

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

        if not params:
            return "No parameters provided. Please fill in the fields above.", _step_md(3, TOOL_STEPS)

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
        return msg, _step_md(4, TOOL_STEPS)

    # === Wire events ===
    refresh_btn.click(on_page_load, outputs=[revit_status, unit_selector])
    unit_selector.change(on_change_unit, inputs=[unit_selector], outputs=[unit_selector])
    detect_unit_btn.click(on_detect_unit, outputs=[unit_selector])

    generate_btn.click(
        on_generate, inputs=[query_input],
        outputs=[current_query, current_selections, code_display, last_code,
                 thinking_display,
                 selection_status, family_radio, level_radio,
                 x_input, y_input, confirm_selection_btn,
                 host_row, host_element_id,
                 security_status, rag_info, step_display],
    )

    select_host_btn.click(
        on_select_host,
        outputs=[host_display, host_element_id],
    )

    confirm_selection_btn.click(
        on_confirm_selection,
        inputs=[current_query, family_radio, level_radio, x_input, y_input,
                host_element_id],
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
