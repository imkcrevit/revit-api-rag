"""
Intent Bridge — Gradio UI with Question Wizard

Layout:
  LEFT (scale=3): Chat history + input box
  RIGHT (scale=2): Intent card + question buttons + JSON output

Custom input flow: when user clicks "其他", the question text updates to
prompt for custom input and the RIGHT-SIDE custom textbox + submit button appear.
"""
from __future__ import annotations

import json

import httpx
import gradio as gr

_API_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=5.0, pool=5.0)
_ANSWER_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)
_MAX_OPTIONS = 8


def _api_base() -> str:
    return "http://127.0.0.1:7860"


# ---------------------------------------------------------------------------
# HTML renderers
# ---------------------------------------------------------------------------

def _render_card_header(card_data: dict) -> str:
    if not card_data or not card_data.get("intent"):
        return ""
    intent = card_data.get("intent", {})
    name = intent.get("display_name", intent.get("name", ""))
    confidence = intent.get("confidence", 0)
    pct = f"{confidence * 100:.0f}%"
    c = "#22c55e" if confidence >= 0.8 else "#eab308" if confidence >= 0.5 else "#ef4444"
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;'
        f'background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">'
        f'<span style="font-weight:600;">🎯 {name}</span>'
        f'<span style="background:{c};color:white;padding:2px 8px;border-radius:10px;font-size:11px;">{pct}</span>'
        f'</div>'
    )


def _render_slots_status(card_data: dict) -> str:
    if not card_data or not card_data.get("slots"):
        return ""
    slots = card_data.get("slots", {})
    icons = {"filled": "✅", "defaulted": "⚙️", "inferred": "💡", "empty": "❓"}
    parts = []
    for name, info in slots.items():
        icon = icons.get(info.get("status", "empty"), "❓")
        val = info.get("display") or info.get("value") or "—"
        parts.append(
            f'<span style="font-size:12px;display:inline-flex;align-items:center;'
            f'background:white;padding:2px 6px;border-radius:4px;border:1px solid #d1fae5;">'
            f'{icon} <b>{name}</b>: {val}</span>'
        )
    return (
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;padding:8px;margin:6px 0;'
        f'background:#f0fdf4;border-radius:6px;border:1px solid #bbf7d0;">{"".join(parts)}</div>'
    )


def _is_other(text: str) -> bool:
    return any(kw in text for kw in ("其他", "Other", "other", "自定义", "custom"))


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def create_intent_bridge_tab():
    session_id = gr.State(value=None)
    card_data = gr.State(value={})
    current_q = gr.State(value=None)
    # Track whether we are in custom-input mode
    custom_mode = gr.State(value=False)

    with gr.Row():
        # === LEFT: Chat ===
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                height=420, show_label=False, render_markdown=True,
                placeholder="描述你想在 Revit 中执行的操作...",
            )
            with gr.Row(equal_height=True, elem_classes=["input-row"]):
                msg_input = gr.Textbox(
                    placeholder="例如：在大厅背面添加一个窗户",
                    show_label=False, scale=6, lines=1, max_lines=1,
                    elem_classes=["input-textbox"],
                )
                send_btn = gr.Button("发送 Send", scale=1, variant="primary", min_width=80, elem_classes=["input-btn"])
                clear_btn = gr.Button("清除 Clear", scale=1, min_width=80, elem_classes=["input-btn"])

        # === RIGHT: Card ===
        with gr.Column(scale=2):
            card_header_html = gr.HTML(value="")
            slots_html = gr.HTML(value="")
            question_text = gr.Markdown(value="", visible=False)
            # Option buttons
            option_btns = []
            for i in range(_MAX_OPTIONS):
                option_btns.append(gr.Button(f"Option {i+1}", visible=False, size="sm"))
            # Custom input — ALWAYS visible, toggled via interactive
            # (Gradio has a bug where visible=False -> visible=True doesn't render)
            custom_input = gr.Textbox(
                placeholder="",
                show_label=False, lines=1, visible=True, interactive=False,
                value="", elem_id="custom-input",
            )
            custom_submit = gr.Button(
                "确定 OK", variant="primary", size="sm", visible=True, interactive=False,
            )
            # Bottom
            confirm_btn = gr.Button(
                "✅ 确认执行 Confirm", variant="primary", interactive=False, visible=False,
            )
            json_accordion = gr.Accordion("结构化 JSON 输出 / Structured JSON", open=False)
            with json_accordion:
                json_output = gr.Code(language="json", label="Output JSON", lines=10)

    # -----------------------------------------------------------------------
    # Output helpers
    # -----------------------------------------------------------------------

    def _hide_btns():
        return [gr.Button(visible=False) for _ in range(_MAX_OPTIONS)]

    def _show_btns(options: list[str]):
        out = []
        for i in range(_MAX_OPTIONS):
            if i < len(options):
                out.append(gr.Button(value=options[i], visible=True, variant="secondary", size="sm"))
            else:
                out.append(gr.Button(value="", visible=False))
        return out

    def _pack_send(history, sid, card, q_obj,
                   q_text="", q_vis=False, btn_list=None,
                   custom_active=False, custom_placeholder="",
                   confirm=None, json_str="", acc_open=False,
                   is_custom=False):
        if btn_list is None:
            btn_list = _hide_btns()
        if confirm is None:
            confirm = gr.Button(interactive=False, visible=False)
        ph = custom_placeholder if custom_active else ""
        return (
            history, "", sid,
            _render_card_header(card),
            _render_slots_status(card),
            gr.update(value=q_text, visible=q_vis),
            *btn_list,
            gr.update(value="", interactive=custom_active, placeholder=ph),
            gr.update(interactive=custom_active),
            confirm, json_str, gr.Accordion(open=acc_open),
            card, q_obj, is_custom,
        )

    def _pack_answer(history, sid, card, q_obj,
                     q_text="", q_vis=False, btn_list=None,
                     custom_active=False, custom_placeholder="",
                     confirm=None, json_str="", acc_open=False,
                     is_custom=False):
        if btn_list is None:
            btn_list = _hide_btns()
        if confirm is None:
            confirm = gr.Button(interactive=False, visible=False)
        ph = custom_placeholder if custom_active else ""
        return (
            history, sid,
            _render_card_header(card),
            _render_slots_status(card),
            gr.update(value=q_text, visible=q_vis),
            *btn_list,
            gr.update(value="", interactive=custom_active, placeholder=ph),
            gr.update(interactive=custom_active),
            confirm, json_str, gr.Accordion(open=acc_open),
            card, q_obj, is_custom,
        )

    def _card_from(data: dict) -> dict:
        return {
            "intent": data.get("intent", {}),
            "slots": data.get("slots", {}),
            "status": data.get("status", ""),
            "output": data.get("structured_output"),
        }

    # -----------------------------------------------------------------------
    # Build outputs from API response
    # -----------------------------------------------------------------------

    def _from_response(data: dict, history: list, sid: str, is_send: bool):
        status = data.get("status", "")
        q = data.get("current_question")
        remaining = data.get("questions_remaining", 0)
        summary = data.get("summary", "")
        structured = data.get("structured_output")
        card = _card_from(data)
        pack = _pack_send if is_send else _pack_answer

        if status == "complete" and summary:
            history = history + [{"role": "assistant", "content": summary}]
            return pack(history, sid, card, None,
                        json_str=json.dumps(structured, ensure_ascii=False, indent=2) if structured else "",
                        confirm=gr.Button(interactive=True, visible=True))

        if q:
            options = q.get("options", [])
            q_text = f"### 💬 {q.get('text', '')}  \n*剩余 Remaining: {remaining}*"
            return pack(history, sid, card, q,
                        q_text=q_text, q_vis=True,
                        btn_list=_show_btns(options))

        followup = data.get("followup_question", "处理中...")
        history = history + [{"role": "assistant", "content": followup}]
        return pack(history, sid, card, None)

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------

    async def handle_send(message: str, history: list, sid: str | None, cur_card: dict, is_custom: bool, cur_q):
        """Handle both normal send AND custom value submit (via left input box)."""
        if not message.strip():
            return _pack_send(history, sid, cur_card or {}, None)

        # If in custom mode, treat as answering current question
        if is_custom and sid and cur_q:
            return await _do_answer(message.strip(), -1, sid, history, cur_card)

        # Normal: new user message
        history = history + [{"role": "user", "content": message}]

        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            if not sid:
                try:
                    r = await client.post(f"{_api_base()}/api/v1/intent/session", json={})
                    r.raise_for_status()
                    sid = r.json()["session_id"]
                except Exception as e:
                    history = history + [{"role": "assistant", "content": f"会话创建失败: {e}"}]
                    return _pack_send(history, sid, cur_card or {}, None)
            try:
                r = await client.post(
                    f"{_api_base()}/api/v1/intent/session/{sid}/turn",
                    json={"user_input": message},
                )
                r.raise_for_status()
                return _from_response(r.json(), history, sid, is_send=True)
            except Exception as e:
                history = history + [{"role": "assistant", "content": f"错误: {e}"}]
                return _pack_send(history, sid, cur_card or {}, None)

    async def _do_answer(value: str, idx: int, sid: str, history: list, cur_card: dict):
        async with httpx.AsyncClient(timeout=_ANSWER_TIMEOUT) as client:
            try:
                r = await client.post(
                    f"{_api_base()}/api/v1/intent/session/{sid}/answer",
                    json={"value": value, "option_index": idx},
                )
                r.raise_for_status()
                return _from_response(r.json(), history, sid, is_send=False)
            except Exception as e:
                history = history + [{"role": "assistant", "content": f"错误: {e}"}]
                return _pack_answer(history, sid, cur_card or {}, None)

    async def handle_custom_submit(val: str, sid, cur_q, history, cur_card):
        if not sid or not cur_q or not val.strip():
            return _pack_answer(history, sid, cur_card or {}, cur_q)
        return await _do_answer(val.strip(), -1, sid, history, cur_card)

    def handle_clear():
        return _pack_send([], None, {}, None)

    def handle_confirm(cur_card: dict):
        output = cur_card.get("output")
        if output:
            return (json.dumps(output, ensure_ascii=False, indent=2),
                    gr.Accordion(open=True),
                    gr.Button(interactive=False, value="✅ 已确认 Confirmed", visible=True))
        return ("", gr.Accordion(open=True), gr.Button(interactive=False, visible=True))

    # -----------------------------------------------------------------------
    # Option button click
    # -----------------------------------------------------------------------

    async def option_handler(btn_text: str, sid, cur_q, history, cur_card):
        if not sid or not cur_q or not btn_text:
            return _pack_answer(history, sid, cur_card or {}, cur_q)

        options = cur_q.get("options", [])
        values = cur_q.get("values", [])

        # "Other" → show custom input textbox on right panel
        if _is_other(btn_text):
            slot_name = cur_q.get("slot", "")
            q_original = cur_q.get("text", "")

            # Build contextual placeholder based on parameter type
            if any(kw in slot_name.lower() for kw in ("host", "element", "wall", "id")):
                placeholder = f"输入 ElementId (例: 12345) / Enter ElementId for {slot_name}"
            elif any(kw in slot_name.lower() for kw in ("xyz", "location", "point", "position")):
                placeholder = f"输入坐标 (例: 1000,500,0) / Enter XYZ coordinates"
            elif any(kw in slot_name.lower() for kw in ("height", "width", "offset", "length")):
                placeholder = f"输入数值 (毫米) / Enter value in mm"
            else:
                placeholder = f"输入 {slot_name} 的自定义值 / Enter custom value for {slot_name}"

            q_text = f"### 💬 {q_original}  \n*请在下方输入框输入自定义值:*"
            return _pack_answer(history, sid, cur_card or {}, cur_q,
                                q_text=q_text, q_vis=True,
                                custom_active=True, custom_placeholder=placeholder,
                                is_custom=True)

        # Find index by matching button text to options
        idx = -1
        value = btn_text
        for i, opt in enumerate(options):
            if opt == btn_text:
                idx = i
                value = values[i] if i < len(values) else btn_text
                break

        return await _do_answer(value, idx, sid, history, cur_card)

    # -----------------------------------------------------------------------
    # Wire events
    # -----------------------------------------------------------------------

    send_outputs = [
        chatbot, msg_input, session_id,
        card_header_html, slots_html, question_text,
        *option_btns,
        custom_input, custom_submit,
        confirm_btn, json_output, json_accordion,
        card_data, current_q, custom_mode,
    ]
    answer_outputs = [
        chatbot, session_id,
        card_header_html, slots_html, question_text,
        *option_btns,
        custom_input, custom_submit,
        confirm_btn, json_output, json_accordion,
        card_data, current_q, custom_mode,
    ]

    send_btn.click(handle_send,
                   [msg_input, chatbot, session_id, card_data, custom_mode, current_q],
                   send_outputs)
    msg_input.submit(handle_send,
                     [msg_input, chatbot, session_id, card_data, custom_mode, current_q],
                     send_outputs)

    for btn in option_btns:
        btn.click(option_handler,
                  [btn, session_id, current_q, chatbot, card_data],
                  answer_outputs)

    custom_submit.click(handle_custom_submit,
                        [custom_input, session_id, current_q, chatbot, card_data],
                        answer_outputs)
    custom_input.submit(handle_custom_submit,
                        [custom_input, session_id, current_q, chatbot, card_data],
                        answer_outputs)

    clear_btn.click(handle_clear, outputs=send_outputs)
    confirm_btn.click(handle_confirm, [card_data],
                      [json_output, json_accordion, confirm_btn])

    return chatbot
