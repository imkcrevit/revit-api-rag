"""
Revit TCP Client — JSON-RPC 2.0 over TCP socket to Revit plugin (port 8080).

Translated from revit-mcp SocketClient.ts + ConnectionManager.ts.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field


@dataclass
class RevitResponse:
    """Structured response from Revit execution."""
    success: bool
    result: dict | list | str | None = None
    error: str | None = None
    raw: str = ""


class RevitClient:
    """Async TCP client that speaks JSON-RPC 2.0 to the Revit plugin."""

    def __init__(self, host: str = "localhost", port: int = 8080,
                 timeout: float = 120.0, connect_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    # -- connection lifecycle --------------------------------------------------

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.connect_timeout,
        )

    async def disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    # -- send command ----------------------------------------------------------

    @staticmethod
    def _make_id() -> str:
        return f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"

    async def send_command(self, method: str, params: dict | None = None) -> RevitResponse:
        """Send a JSON-RPC 2.0 command and wait for the response."""
        if not self.connected:
            await self.connect()

        request_id = self._make_id()
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": request_id,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._writer.write(data)
        await self._writer.drain()

        # read response — accumulate until valid JSON
        buf = b""
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self._reader.read(65536),
                    timeout=self.timeout,
                )
                if not chunk:
                    raise ConnectionError("Revit plugin closed connection")
                buf += chunk
                try:
                    resp = json.loads(buf.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue  # incomplete, keep reading
        except asyncio.TimeoutError:
            return RevitResponse(success=False, error=f"Timeout after {self.timeout}s")

        # parse JSON-RPC response
        if "error" in resp and resp["error"]:
            err = resp["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return RevitResponse(success=False, error=msg, raw=json.dumps(resp))

        return RevitResponse(success=True, result=resp.get("result"), raw=json.dumps(resp))

    # -- high-level: send code -------------------------------------------------

    async def send_code(self, code: str, parameters: list | None = None) -> RevitResponse:
        """Send C# code to Revit for execution (maps to send_code_to_revit command)."""
        return await self.send_command("send_code_to_revit", {
            "code": code,
            "parameters": parameters or [],
        })


async def with_revit_connection(operation):
    """Context-managed Revit connection (mirrors ConnectionManager.ts)."""
    client = RevitClient()
    try:
        await client.connect()
        return await operation(client)
    finally:
        await client.disconnect()
