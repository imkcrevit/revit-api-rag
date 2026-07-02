"""
RAG 服务编排 — 检索 + 上下文组装 + 流式生成

process_chat() 是核心入口：
  1. show_full=True 且有缓存结果 → 用缓存上下文生成完整代码
  2. 检索 API + SDK
  3. 组装上下文 → 流式生成
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from server.app.session import Session
from server.app.deps import get_retriever, get_config, create_llm_for_session
from server.app.prompts.templates import get_system_prompt
from server.app.api.streaming import async_stream_tokens, format_sse_event, format_sse_done


async def process_chat(
    message: str,
    session: Session,
    show_full: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Main chat pipeline. Yields SSE-formatted strings.

    Events:
      - event: search    — search status
      - event: token     — streamed token
      - event: done      — generation complete
    """
    config = get_config()
    retriever = get_retriever()
    revit_version = config.get("revit_version", "2026")

    # If show_full and we have cached results, reuse them
    if show_full and session.last_search_results:
        results = session.last_search_results
    else:
        yield format_sse_event("search", "Searching API documentation and SDK examples...")
        retrieval_cfg = config.get("retrieval", {})
        api_top_k = retrieval_cfg.get("api", {}).get("rerank_top_n", 15)
        code_top_k = retrieval_cfg.get("code", {}).get("rerank_top_n", 3)
        results = await asyncio.to_thread(
            retriever.search, message, api_top_k=api_top_k, code_top_k=code_top_k
        )
        session.last_search_results = results

    # Build context
    context = retriever.build_context(results)
    system_prompt = get_system_prompt(
        show_full=show_full,
        api_context=context["api_context"],
        code_context=context["code_context"],
        revit_version=revit_version,
    )

    # Build conversation prompt (include recent history for context)
    prompt = _build_prompt(message, session)

    # Stream generation
    session.add_message("user", message)
    llm = create_llm_for_session(session)
    full_response = []

    async for token in async_stream_tokens(llm, prompt, system_prompt):
        full_response.append(token)
        yield format_sse_event("token", token)

    session.add_message("assistant", "".join(full_response))
    yield format_sse_done()


def _build_prompt(message: str, session: Session) -> str:
    """Build prompt with recent conversation history."""
    # Include last 3 exchanges for context continuity
    recent = session.history[-6:]  # 3 user + 3 assistant messages
    if not recent:
        return message

    parts = []
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Assistant"
        parts.append(f"{role}: {msg['content']}")
    parts.append(f"User: {message}")
    return "\n\n".join(parts)


async def process_search(query: str, api_top_k: int = 15, code_top_k: int = 5) -> dict:
    """Pure search without generation — returns formatted results."""
    retriever = get_retriever()
    results = await asyncio.to_thread(
        retriever.search, query, api_top_k=api_top_k, code_top_k=code_top_k
    )
    return {
        "query": results.query,
        "rewritten_query": results.rewritten_query,
        "api_results": [
            {
                "name": item.full_id or item.name,
                "summary": item.summary,
                "syntax": item.syntax,
                "parameters": item.parameters,
                "distance": item.distance,
            }
            for item in results.api_items
        ],
        "sdk_results": [
            {
                "project": item.project,
                "summary": item.summary,
                "content": item.content[:500],
                "distance": item.distance,
            }
            for item in results.sdk_items
        ],
    }
