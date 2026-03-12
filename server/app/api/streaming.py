"""
SSE 适配器 — 将同步 LLMClient.generate_stream() 桥接为异步生成器

使用 asyncio.Queue + daemon thread 实现 sync → async 桥接。
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import AsyncGenerator

from pipeline.llm_client import LLMClient


_SENTINEL = object()


async def async_stream_tokens(
    client: LLMClient,
    prompt: str,
    system_prompt: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Bridge sync generate_stream() to async generator.
    Runs the sync generator in a daemon thread, yields tokens via asyncio.Queue.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _produce():
        try:
            for token in client.generate_stream(prompt, system_prompt):
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    thread = threading.Thread(target=_produce, daemon=True)
    thread.start()

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def format_sse_event(event: str, data: str) -> str:
    """Format a single SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_sse_done() -> str:
    return "event: done\ndata: [DONE]\n\n"
