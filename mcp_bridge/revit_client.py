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
        self._lock = asyncio.Lock()  # prevent concurrent command interleaving

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
        """Send a JSON-RPC 2.0 command and wait for the response.

        Uses a lock to prevent concurrent commands from interleaving on the
        same TCP connection.  Validates that the response id matches the
        request id — stale responses from previous commands (e.g. a late
        health-check reply) are discarded automatically.
        """
        import logging
        _log = logging.getLogger("mcp_bridge.revit_client")

        async with self._lock:
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
            # Loop to skip stale responses whose id doesn't match request_id.
            deadline = time.monotonic() + self.timeout
            while True:
                buf = b""
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return RevitResponse(success=False, error=f"Timeout after {self.timeout}s")
                        chunk = await asyncio.wait_for(
                            self._reader.read(8192),
                            timeout=remaining,
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

                # Validate response id matches our request
                resp_id = resp.get("id")
                if resp_id != request_id:
                    _log.warning(
                        f"[send_command] Discarding stale response: "
                        f"expected id={request_id}, got id={resp_id} "
                        f"(likely from previous '{resp.get('result', {}).get('message', '?') if isinstance(resp.get('result'), dict) else '?'}' call)"
                    )
                    continue  # discard and read the next response

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
        _log.info(f"[send_code] RAW resp.result = {json.dumps(resp.result, ensure_ascii=False, default=str)[:500]}")

        if resp.success and isinstance(resp.result, dict):
            inner = resp.result
            if "success" in inner:
                inner_result_raw = inner.get("result", "")
                _log.info(f"[send_code] inner.success={inner.get('success')} inner_result_raw type={type(inner_result_raw).__name__} value={str(inner_result_raw)[:300]}")
                if not inner.get("success"):
                    _log.error(f"[send_code] EXECUTION FAILED — errorMessage: {inner.get('errorMessage', '(none)')}")

                inner_result = inner_result_raw

                # The result field is often a JSON string — parse it
                if isinstance(inner_result, str) and inner_result.strip():
                    try:
                        parsed = json.loads(inner_result)
                        _log.info(f"[send_code] parsed JSON string → type={type(parsed).__name__}")
                        # json.loads("null") → None, keep original string in that case
                        inner_result = parsed if parsed is not None else inner_result
                    except (json.JSONDecodeError, ValueError) as e:
                        _log.warning(f"[send_code] JSON parse failed: {e}, keeping raw string")
                        inner_result = {"raw_output": inner_result}

                # If result is truly empty/None, provide minimal feedback
                is_success = bool(inner.get("success", False))
                if is_success and inner_result is None:
                    inner_result = {"Status": "Success", "Message": "Code executed (no return statement in generated code)"}
                    _log.warning("[send_code] C# code returned null — generated code likely missing 'return' statement")
                elif is_success and inner_result == "":
                    inner_result = {"Status": "Success", "Message": "Code executed (empty return value)"}
                    _log.warning("[send_code] C# code returned empty string")

                error_msg = inner.get("errorMessage") or None
                return RevitResponse(
                    success=is_success,
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
