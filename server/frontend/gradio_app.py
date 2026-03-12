"""
Gradio UI — Tab A: Code Generation + Tab B: Text2Revit (Legacy) + Tab C: Intent Bridge

Claude / AI Studio 风格：全宽聊天区、底部输入、设置折叠。
自动从 .env 读取 API Key。Model 使用 OpenAI 格式模型名。
"""
from __future__ import annotations

import json
import os
import uuid

import httpx
import gradio as gr
from dotenv import load_dotenv

# Load .env for local dev
load_dotenv()

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _api_base() -> str:
    return "http://127.0.0.1:7860"


def _get_model_choices() -> list[str]:
    """Read model names from config.yaml (OpenAI format)."""
    try:
        from server.app.deps import get_config
        config = get_config()
        models_cfg = config.get("llm", {}).get("models", {})
        choices = []
        for mcfg in models_cfg.values():
            model = mcfg.get("model", "")
            if model and model not in choices:
                choices.append(model)
        return choices or ["anthropic/claude-sonnet-4.6"]
    except Exception:
        return ["anthropic/claude-sonnet-4.6"]


def _get_default_model() -> str:
    try:
        from server.app.deps import get_config
        config = get_config()
        llm_cfg = config.get("llm", {})
        provider = llm_cfg.get("provider", "claude")
        return llm_cfg.get("models", {}).get(provider, {}).get("model", "anthropic/claude-sonnet-4.6")
    except Exception:
        return "anthropic/claude-sonnet-4.6"


def _has_system_key() -> bool:
    """Check if system API key is configured (without exposing it)."""
    return bool(os.getenv("OPENROUTER_API_KEY", ""))


# ---------------------------------------------------------------------------
# SSE parser
# ---------------------------------------------------------------------------

def _parse_sse_stream(resp):
    """Parse SSE stream, yield accumulated assistant content."""
    content = ""
    for line in resp.iter_lines():
        if not line:
            continue
        if line.startswith("event: "):
            continue
        if line.startswith("data: "):
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                token = json.loads(data_str)
                content += token
                yield content
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_gradio_app() -> gr.Blocks:
    model_choices = _get_model_choices()
    default_model = _get_default_model()
    has_sys_key = _has_system_key()

    with gr.Blocks(title="Revit API Assistant", fill_height=True) as app:
        # State
        session_id = gr.State(lambda: uuid.uuid4().hex)

        # --- Injected CSS (Gradio 6 doesn't support css= in Blocks) ---
        gr.HTML(
            '<style>'
            '.input-row { align-items: center !important; gap: 8px !important; }'
            '.input-row .input-textbox textarea { min-height: 40px !important; max-height: 40px !important; padding: 8px 12px !important; }'
            '.input-row .input-textbox { min-height: 40px !important; }'
            '.input-row .input-btn { height: 40px !important; min-height: 40px !important; max-height: 40px !important; }'
            '#custom-input textarea:disabled { background: #f9fafb !important; border-color: #e5e7eb !important; cursor: default !important; }'
            '#custom-input textarea:disabled::placeholder { color: transparent !important; }'
            '</style>'
        )

        # --- Header ---
        gr.HTML(
            '<div class="app-header">'
            '<h1>Revit API Assistant</h1>'
            '<p>Code Generation & Text2Revit</p>'
            '</div>'
        )

        # --- Settings (collapsible) ---
        with gr.Accordion("Settings", open=False, elem_classes=["settings-accordion"]):
            with gr.Row():
                key_placeholder = "Leave empty to use system key" if has_sys_key else "Required: enter your OpenRouter API Key"
                api_key_input = gr.Textbox(
                    label="API Key (optional)" if has_sys_key else "API Key (required)",
                    value="",
                    placeholder=key_placeholder,
                    type="password",
                    scale=3,
                )
                model_dropdown = gr.Dropdown(
                    label="Model",
                    choices=model_choices,
                    value=default_model,
                    scale=2,
                )
                show_full_cb = gr.Checkbox(
                    label="Full Code",
                    value=False,
                    scale=1,
                )
            key_status = gr.Markdown(
                value=("*Using system API key*" if has_sys_key else "*No API key configured — please enter one above*"),
            )

        # --- Tabs ---
        with gr.Tabs():
            # ==========================================================
            # Tab A: Code Generation
            # ==========================================================
            with gr.Tab("Code Generation"):
                chatbot_code = gr.Chatbot(
                    height=420,
                    show_label=False,
                    elem_classes=["main-chat-area"],
                    render_markdown=True,
                )
                with gr.Row(equal_height=True, elem_classes=["input-row"]):
                    msg_code = gr.Textbox(
                        placeholder="Ask about Revit API...",
                        show_label=False,
                        scale=6,
                        lines=1,
                        max_lines=1,
                        elem_classes=["input-textbox"],
                    )
                    send_code = gr.Button("发送 Send", scale=1, variant="primary", min_width=80, elem_classes=["input-btn"])
                    clear_code = gr.Button("清除 Clear", scale=1, min_width=80, elem_classes=["input-btn"])

            # ==========================================================
            # Tab B: Text2Revit (Legacy)
            # ==========================================================
            with gr.Tab("Text2Revit (Legacy)"):
                chatbot_t2r = gr.Chatbot(
                    height=420,
                    show_label=False,
                    elem_classes=["main-chat-area"],
                    render_markdown=True,
                )
                with gr.Row(equal_height=True, elem_classes=["input-row"]):
                    msg_t2r = gr.Textbox(
                        placeholder="Describe what to create in Revit... (wall, column, beam, floor, door, window)",
                        show_label=False,
                        scale=6,
                        lines=1,
                        max_lines=1,
                        elem_classes=["input-textbox"],
                    )
                    send_t2r = gr.Button("发送 Send", scale=1, variant="primary", min_width=80, elem_classes=["input-btn"])
                    clear_t2r = gr.Button("清除 Clear", scale=1, min_width=80, elem_classes=["input-btn"])

            # ==========================================================
            # Tab C: Intent Bridge
            # ==========================================================
            with gr.Tab("Intent Bridge"):
                try:
                    from intent_bridge.frontend.app import create_intent_bridge_tab
                    create_intent_bridge_tab()
                except ImportError:
                    gr.Markdown("Intent Bridge module not available.")

        # ==============================================================
        # Event handlers
        # ==============================================================

        def update_settings(api_key: str, model: str, sid: str):
            # Send key to backend (empty string = clear user key, revert to system key)
            try:
                httpx.post(
                    f"{_api_base()}/api/settings",
                    json={"api_key": api_key, "model": model or None},
                    headers={"X-Session-Id": sid},
                    timeout=5,
                )
            except Exception:
                pass

            # Update status indicator
            if api_key.strip():
                masked = api_key[:5] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
                return f"*Using custom key: `{masked}`*"
            elif has_sys_key:
                return "*Using system API key*"
            else:
                return "*No API key configured — please enter one above*"

        def chat_code_stream(message: str, history: list, show_full: bool, sid: str):
            if not message.strip():
                yield history, ""
                return
            history = history + [{"role": "user", "content": message}]
            yield history, ""
            try:
                with httpx.stream(
                    "POST",
                    f"{_api_base()}/api/chat",
                    json={"message": message, "session_id": sid, "show_full": show_full},
                    timeout=120,
                ) as resp:
                    resp.raise_for_status()
                    for content in _parse_sse_stream(resp):
                        yield history + [{"role": "assistant", "content": content}], ""
            except Exception as e:
                yield history + [{"role": "assistant", "content": f"Error: {e}"}], ""

        def chat_t2r_stream(message: str, history: list, sid: str):
            if not message.strip():
                yield history, ""
                return
            history = history + [{"role": "user", "content": message}]
            yield history, ""
            try:
                with httpx.stream(
                    "POST",
                    f"{_api_base()}/api/t2r/chat",
                    json={"message": message, "session_id": sid},
                    timeout=120,
                ) as resp:
                    resp.raise_for_status()
                    for content in _parse_sse_stream(resp):
                        yield history + [{"role": "assistant", "content": content}], ""
            except Exception as e:
                yield history + [{"role": "assistant", "content": f"Error: {e}"}], ""

        # --- Wire events ---
        api_key_input.change(update_settings, [api_key_input, model_dropdown, session_id], [key_status])
        model_dropdown.change(update_settings, [api_key_input, model_dropdown, session_id], [key_status])

        # Tab A
        send_code.click(
            chat_code_stream,
            [msg_code, chatbot_code, show_full_cb, session_id],
            [chatbot_code, msg_code],
        )
        msg_code.submit(
            chat_code_stream,
            [msg_code, chatbot_code, show_full_cb, session_id],
            [chatbot_code, msg_code],
        )
        clear_code.click(lambda: ([], ""), outputs=[chatbot_code, msg_code])

        # Tab B
        send_t2r.click(
            chat_t2r_stream,
            [msg_t2r, chatbot_t2r, session_id],
            [chatbot_t2r, msg_t2r],
        )
        msg_t2r.submit(
            chat_t2r_stream,
            [msg_t2r, chatbot_t2r, session_id],
            [chatbot_t2r, msg_t2r],
        )
        clear_t2r.click(lambda: ([], ""), outputs=[chatbot_t2r, msg_t2r])

    return app
