"""
Revit TCP Client — JSON-RPC 2.0 over raw TCP socket to Revit plugin.

Protocol confirmed from monorepo SocketService.cs:
- Transport: TcpListener / TcpClient (NOT WebSocket)
- Port: 18080 (changed from 8080 — 8080 conflicts with AdskLicensingAgent)
- Message format: JSON-RPC 2.0, UTF-8, no delimiter (raw read)
- Buffer: 8192 bytes per read on plugin side
- send_code_to_revit timeout: 60s (plugin-side RaiseAndWaitForCompletion)
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass


@dataclass
class RevitResponse:
    """Structured response from Revit execution."""
    success: bool
    result: dict | list | str | None = None
    error: str | None = None
    raw: str = ""


class RevitClient:
    """Async TCP client that speaks JSON-RPC 2.0 to the Revit plugin."""

    def __init__(self, host: str = "localhost", port: int = 18080,
                 timeout: float = 60.0, connect_timeout: float = 5.0):
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

        # Read response — accumulate until valid JSON.
        # Plugin sends raw UTF-8 JSON with no delimiter, reads up to 8192 bytes.
        buf = b""
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self._reader.read(8192),
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

        # Parse JSON-RPC response
        if "error" in resp and resp["error"]:
            err = resp["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return RevitResponse(success=False, error=msg, raw=json.dumps(resp))

        return RevitResponse(success=True, result=resp.get("result"), raw=json.dumps(resp))

    # -- high-level: send code -------------------------------------------------

    async def send_code(self, code: str, parameters: list | None = None) -> RevitResponse:
        """Send C# code to Revit for dynamic compilation and execution.

        Maps to the send_code_to_revit command. The plugin wraps user code in:
            public static object Execute(Document document, object[] parameters)
        and compiles it with Roslyn. A Transaction is already active — user code
        must NOT create its own Transaction.

        The plugin returns: {"success": bool, "result": "JSON string", "errorMessage": ""}
        We unwrap this nested structure so callers get parsed data directly.
        """
        import logging
        _log = logging.getLogger("mcp_bridge.revit_client")

        resp = await self.send_command("send_code_to_revit", {
            "code": code,
            "parameters": parameters or [],
        })

        # Unwrap the plugin's nested response format
        _log.info(f"[send_code] resp.success={resp.success} result_type={type(resp.result).__name__}")
        if resp.success and isinstance(resp.result, dict):
            inner = resp.result
            if "success" in inner:
                inner_result = inner.get("result", "")
                _log.info(f"[send_code] inner.success={inner.get('success')} inner_result_type={type(inner_result).__name__} len={len(str(inner_result)[:100])}")
                if not inner.get("success"):
                    _log.error(f"[send_code] EXECUTION FAILED — errorMessage: {inner.get('errorMessage', '(none)')}")
                # The result field is often a JSON string — parse it
                if isinstance(inner_result, str) and inner_result.strip():
                    try:
                        inner_result = json.loads(inner_result)
                        _log.info(f"[send_code] parsed inner_result: type={type(inner_result).__name__} len={len(inner_result) if isinstance(inner_result, list) else 'N/A'}")
                    except (json.JSONDecodeError, ValueError) as e:
                        _log.warning(f"[send_code] JSON parse failed: {e}")
                error_msg = inner.get("errorMessage") or None
                return RevitResponse(
                    success=bool(inner.get("success", False)),
                    result=inner_result,
                    error=error_msg if error_msg else resp.error,
                    raw=resp.raw,
                )
        elif resp.success:
            _log.info(f"[send_code] result is NOT dict: {type(resp.result).__name__}, value={str(resp.result)[:200]}")

        return resp

    async def ping(self) -> bool:
        """Quick connectivity check via say_hello command."""
        try:
            resp = await self.send_command("say_hello", {"message": "ping"})
            return resp.success
        except Exception:
            return False


async def with_revit_connection(operation):
    """Context-managed Revit connection."""
    client = RevitClient()
    try:
        await client.connect()
        return await operation(client)
    finally:
        await client.disconnect()
