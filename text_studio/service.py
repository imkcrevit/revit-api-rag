"""
TextStudio 服务 — 多语言翻译与文本优化

支持多语言互译（类似 Google Translate），保证精度和准确度。
使用 DeepSeek 作为后端模型，通过 OpenRouter 调用。
"""
from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

from server.app.api.streaming import async_stream_tokens, format_sse_event, format_sse_done
from pipeline.llm_client import LLMClient
from prompts import load_prompt

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324"
_BASE_URL = "https://openrouter.ai/api/v1"

# ── Supported languages ─────────────────────────────────────────────────

LANGUAGES = {
    "auto":  "Auto-detect",
    "zh":    "中文",
    "en":    "English",
    "ja":    "日本語",
    "ko":    "한국어",
    "fr":    "Français",
    "de":    "Deutsch",
    "es":    "Español",
    "ru":    "Русский",
    "pt":    "Português",
    "it":    "Italiano",
    "ar":    "العربية",
    "th":    "ไทย",
    "vi":    "Tiếng Việt",
}

# ── System prompt ────────────────────────────────────────────────────────

def _build_system_prompt(source_lang: str, target_lang: str) -> str:
    src_label = LANGUAGES.get(source_lang, source_lang)
    tgt_label = LANGUAGES.get(target_lang, target_lang)

    return load_prompt("text_studio.system.md").format(
        src_label=src_label,
        tgt_label=tgt_label,
        auto_detect_note=(
            "If the source language is Auto-detect, identify the input language first, "
            "then translate to the target language."
            if source_lang == "auto"
            else ""
        ),
    )


def _create_client() -> LLMClient:
    """Create LLM client using config or defaults."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY 环境变量未设置。"
            "请在 .env 中添加你的 OpenRouter API Key。"
        )

    from server.app.deps import get_config
    config = get_config()

    ts_cfg = config.get("text_studio", {})
    model = ts_cfg.get("model", _DEFAULT_MODEL)
    temperature = ts_cfg.get("temperature", 0.7)
    max_tokens = ts_cfg.get("max_tokens", 4096)

    proxy_cfg = config.get("proxy", {})
    proxy_url = None
    if proxy_cfg.get("enabled", False):
        proxy_url = proxy_cfg.get("https") or proxy_cfg.get("http")

    return LLMClient(
        base_url=_BASE_URL,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        proxy=proxy_url,
    )


async def process_text_studio_chat(
    message: str,
    session,
    source_lang: str = "auto",
    target_lang: str = "en",
) -> AsyncGenerator[str, None]:
    """
    Process TextStudio chat request, SSE streaming response.
    Records cost after completion.
    """
    system_prompt = _build_system_prompt(source_lang, target_lang)

    from server.app.skill_store import get_skill_store
    skills_ctx = get_skill_store().get_active_prompt("text_studio")
    if skills_ctx:
        system_prompt += (
            "\n\n---\n\n## 项目规范（来自用户 Skills 配置）\n\n"
            "翻译或优化文本时**必须**遵循以下用户配置的项目规范，"
            "确保输出术语、命名格式符合标准：\n\n"
            + skills_ctx
        )

    session.touch()
    session.add_message("user", message)

    history_parts: list[str] = []
    for msg in session.history[:-1]:
        role_label = "User" if msg["role"] == "user" else "TextStudio"
        history_parts.append(f"{role_label}: {msg['content']}")

    if history_parts:
        full_prompt = (
            "Previous conversation:\n\n"
            + "\n\n".join(history_parts)
            + f"\n\nUser: {message}"
        )
    else:
        full_prompt = message

    try:
        client = _create_client()
        content_parts: list[str] = []

        async for token in async_stream_tokens(client, full_prompt, system_prompt):
            content_parts.append(token)
            yield format_sse_event("token", token)

        full_content = "".join(content_parts)
        if full_content:
            session.add_message("assistant", full_content)

        # Record cost
        from text_studio.cost_tracker import get_cost_tracker
        get_cost_tracker().record(full_prompt, full_content)

        yield format_sse_done()

    except Exception as e:
        logger.error(f"TextStudio chat error: {e}")
        error_msg = f"Error: {str(e)}"
        yield format_sse_event("token", error_msg)
        session.add_message("assistant", error_msg)
        yield format_sse_done()
