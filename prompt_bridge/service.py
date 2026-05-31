"""
PromptBridge 服务 — 加载知识库 Markdown，构建 system prompt，流式对话

帮助设计师将模糊需求转化为精确的 AI 提示词。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import AsyncGenerator

from server.app.api.streaming import async_stream_tokens, format_sse_event, format_sse_done
from pipeline.llm_client import LLMClient
from prompts import load_prompt

logger = logging.getLogger(__name__)

# PromptBridge markdown 文件根目录
_BRIDGE_DIR = Path(__file__).resolve().parent

# 默认模型配置
_DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324"
_BASE_URL = "https://openrouter.ai/api/v1"


def _load_markdown(path: Path) -> str:
    """读取单个 markdown 文件，返回内容。"""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        return ""


def _build_knowledge_context() -> str:
    """加载所有 PromptBridge 知识文件，拼接为 system prompt 上下文。"""
    sections: list[str] = []

    # 1. 设计师指南
    guide = _load_markdown(_BRIDGE_DIR / "_guide.md")
    if guide:
        sections.append(f"## 设计师提问指南\n\n{guide}")

    # 2. 术语表
    glossary = _load_markdown(_BRIDGE_DIR / "glossary.md")
    if glossary:
        sections.append(f"## 术语映射表\n\n{glossary}")

    # 3. 场景卡片
    scenarios_dir = _BRIDGE_DIR / "scenarios"
    if scenarios_dir.is_dir():
        for md_file in sorted(scenarios_dir.glob("*.md")):
            content = _load_markdown(md_file)
            if content:
                sections.append(f"## 场景：{md_file.stem}\n\n{content}")

    return "\n\n---\n\n".join(sections)


def _build_system_prompt(knowledge: str) -> str:
    """构建完整的 system prompt。"""
    return load_prompt("prompt_bridge.system.md").format(knowledge=knowledge)


# 缓存加载的知识和 system prompt
_cached_system_prompt: str | None = None


def _get_system_prompt() -> str:
    global _cached_system_prompt
    if _cached_system_prompt is None:
        knowledge = _build_knowledge_context()
        _cached_system_prompt = _build_system_prompt(knowledge)
        logger.info(f"PromptBridge system prompt loaded ({len(_cached_system_prompt)} chars)")
    return _cached_system_prompt


def _create_client() -> LLMClient:
    """创建使用免费模型的 LLM 客户端。"""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY 环境变量未设置。"
            "请在 .env 中添加你的 OpenRouter API Key。"
        )

    from server.app.deps import get_config
    config = get_config()

    # 读取 prompt_bridge 配置，如果没有则使用默认免费模型
    pb_cfg = config.get("prompt_bridge", {})
    model = pb_cfg.get("model", _DEFAULT_MODEL)
    temperature = pb_cfg.get("temperature", 0.7)
    max_tokens = pb_cfg.get("max_tokens", 4096)

    # Proxy
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


async def process_prompt_bridge_chat(
    message: str,
    session,
) -> AsyncGenerator[str, None]:
    """
    处理 PromptBridge 对话请求，SSE 流式返回。

    与 RAG chat 不同，这里：
    - 不做 RAG 检索（知识已在 system prompt 中）
    - 保留对话历史（多轮优化）
    """
    base_prompt = _get_system_prompt()
    from server.app.skill_store import get_skill_store
    skills_ctx = get_skill_store().get_active_prompt("prompt_bridge")
    if skills_ctx:
        system_prompt = (
            base_prompt
            + "\n\n---\n\n## 项目规范（来自用户 Skills 配置）\n\n"
            "以下是用户配置的 BIM/项目规范。生成提示词时**必须**遵循这些标准：\n"
            "- 命名必须符合规范格式\n"
            "- 参数取值必须在规范允许的范围内\n"
            "- 如果用户输入违反规范，在纠正中指出并替换为规范写法\n\n"
            + skills_ctx
        )
    else:
        system_prompt = base_prompt

    session.touch()
    session.add_message("user", message)

    # 构建包含历史的 prompt
    history_parts: list[str] = []
    for msg in session.history[:-1]:  # 不包含刚加的这条
        role_label = "设计师" if msg["role"] == "user" else "PromptBridge"
        history_parts.append(f"{role_label}：{msg['content']}")

    if history_parts:
        full_prompt = (
            "以下是之前的对话历史：\n\n"
            + "\n\n".join(history_parts)
            + f"\n\n设计师：{message}"
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

        yield format_sse_done()

    except Exception as e:
        logger.error(f"PromptBridge chat error: {e}")
        error_msg = f"抱歉，出现了错误：{str(e)}"
        yield format_sse_event("token", error_msg)
        session.add_message("assistant", error_msg)
        yield format_sse_done()
