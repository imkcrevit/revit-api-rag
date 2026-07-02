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
    prompt: str = "",
    system_prompt: str | None = None,
    messages: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Bridge sync generate_stream() to async generator.
    Runs the sync generator in a daemon thread, yields tokens via asyncio.Queue.

    Pass `messages` for structured multi-turn chat (system + per-role turns);
    otherwise `prompt`/`system_prompt` are used.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def _produce():
        try:
            for token in client.generate_stream(prompt, system_prompt, messages=messages):
                # Consumer disconnected — stop consuming upstream tokens so we
                # don't keep burning tokens or growing the queue unbounded.
                if stop.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    thread = threading.Thread(target=_produce, daemon=True)
    thread.start()

    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        # Signal the producer thread to stop on disconnect / exception / break.
        stop.set()


def format_sse_event(event: str, data: str) -> str:
    """Format a single SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_sse_done() -> str:
    return "event: done\ndata: [DONE]\n\n"
