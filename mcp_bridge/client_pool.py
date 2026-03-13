"""
RevitClientPool — singleton TCP connection with auto-reconnect.

Avoids per-request connect/disconnect overhead (~1s ExternalEvent latency).
"""
from __future__ import annotations

import asyncio

from mcp_bridge.revit_client import RevitClient


class RevitClientPool:
    """Singleton Revit TCP client with auto-reconnect."""

    _instance: RevitClient | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_client(cls, host: str = "localhost", port: int = 18080,
                         timeout: float = 60.0, connect_timeout: float = 5.0) -> RevitClient:
        async with cls._lock:
            if cls._instance is None or not cls._instance.connected:
                cls._instance = RevitClient(
                    host=host, port=port,
                    timeout=timeout, connect_timeout=connect_timeout,
                )
                await cls._instance.connect()
            return cls._instance

    @classmethod
    async def disconnect(cls) -> None:
        async with cls._lock:
            if cls._instance:
                await cls._instance.disconnect()
                cls._instance = None

    @classmethod
    async def ping(cls, **kwargs) -> bool:
        try:
            client = await cls.get_client(**kwargs)
            return await client.ping()
        except Exception:
            return False
