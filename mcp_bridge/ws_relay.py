"""
WebSocket Slot Relay — 多用户 Revit 连接管理

Revit 插件通过 WebSocket 主动连接到服务器，
服务器维护 slot_id → WebSocket 映射，
前端请求通过 X-Slot-Id header 路由到对应的 Revit 实例。

每个 slot 同一时间只能处理一个请求（Revit 串行执行）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

from mcp_bridge.revit_client import RevitResponse

logger = logging.getLogger(__name__)

MAX_SLOTS = 5


@dataclass
class SlotConnection:
    """One Revit plugin connection on a named slot."""
    slot_id: str
    ws: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pending: asyncio.Future | None = field(default=None, repr=False)
    connected_at: float = field(default_factory=time.time)
    request_count: int = 0

    @property
    def connected(self) -> bool:
        try:
            return self.ws.client_state.name == "CONNECTED"
        except Exception:
            return False


class SlotManager:
    """Manages WebSocket slots for multiple Revit plugin connections."""

    def __init__(self, max_slots: int = MAX_SLOTS):
        self.max_slots = max_slots
        self._slots: dict[str, SlotConnection] = {}

    # ── Registration ─────────────────────────────────────────────────

    def register(self, slot_id: str, ws: WebSocket) -> bool:
        """Register a Revit plugin connection on a slot. Returns False if slot taken."""
        if slot_id in self._slots and self._slots[slot_id].connected:
            return False
        if len(self._slots) >= self.max_slots and slot_id not in self._slots:
            return False
        self._slots[slot_id] = SlotConnection(slot_id=slot_id, ws=ws)
        logger.info(f"Slot '{slot_id}' registered (total: {len(self._slots)})")
        return True

    def unregister(self, slot_id: str):
        """Remove a slot connection."""
        conn = self._slots.pop(slot_id, None)
        if conn:
            # Cancel any pending future
            if conn._pending and not conn._pending.done():
                conn._pending.cancel()
            logger.info(f"Slot '{slot_id}' unregistered (total: {len(self._slots)})")

    def get_connection(self, slot_id: str) -> SlotConnection | None:
        conn = self._slots.get(slot_id)
        if conn and conn.connected:
            return conn
        return None

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return all slot statuses for the frontend."""
        slots = {}
        for i in range(1, self.max_slots + 1):
            sid = str(i)
            conn = self._slots.get(sid)
            if conn and conn.connected:
                slots[sid] = {
                    "status": "connected",
                    "connected_at": conn.connected_at,
                    "requests": conn.request_count,
                }
            else:
                slots[sid] = {"status": "free"}
        return {
            "max_slots": self.max_slots,
            "connected": sum(1 for c in self._slots.values() if c.connected),
            "slots": slots,
        }

    # ── Message handling ─────────────────────────────────────────────

    def resolve_response(self, slot_id: str, data: str):
        """Called when a message arrives from Revit plugin (response)."""
        conn = self._slots.get(slot_id)
        if conn and conn._pending and not conn._pending.done():
            conn._pending.set_result(data)
        else:
            logger.warning(f"Slot '{slot_id}' received message but no pending request")

    # ── Send command ─────────────────────────────────────────────────

    async def send_command(
        self, slot_id: str, method: str, params: dict | None = None,
        timeout: float = 60.0,
    ) -> RevitResponse:
        """Send a JSON-RPC 2.0 command through the slot's WebSocket."""
        conn = self.get_connection(slot_id)
        if not conn:
            return RevitResponse(success=False, error=f"Slot '{slot_id}' not connected")

        async with conn.lock:
            request_id = f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"
            payload = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": request_id,
            }

            future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
            conn._pending = future

            try:
                await conn.ws.send_text(json.dumps(payload, ensure_ascii=False))
                raw = await asyncio.wait_for(future, timeout=timeout)
                conn.request_count += 1
            except asyncio.TimeoutError:
                return RevitResponse(success=False, error=f"Timeout after {timeout}s")
            except Exception as e:
                return RevitResponse(success=False, error=str(e))
            finally:
                conn._pending = None

            # Parse JSON-RPC response
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                return RevitResponse(success=False, error="Invalid JSON from Revit", raw=raw)

            # Validate response id
            if resp.get("id") != request_id:
                logger.warning(
                    f"Slot '{slot_id}' response id mismatch: "
                    f"expected={request_id}, got={resp.get('id')}"
                )

            if "error" in resp and resp["error"]:
                err = resp["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return RevitResponse(success=False, error=msg, raw=raw)

            return RevitResponse(success=True, result=resp.get("result"), raw=raw)

    async def send_code(
        self, slot_id: str, code: str, parameters: list | None = None,
        timeout: float = 60.0,
    ) -> RevitResponse:
        """Send C# code through the slot's WebSocket — same unwrapping as TCP client."""
        resp = await self.send_command(slot_id, "send_code_to_revit", {
            "code": code, "parameters": parameters or [],
        }, timeout=timeout)

        # Unwrap nested plugin response (same logic as RevitClient.send_code)
        if resp.success and isinstance(resp.result, dict):
            inner = resp.result
            if "success" in inner:
                inner_result = inner.get("result", "")
                if isinstance(inner_result, str) and inner_result.strip():
                    try:
                        parsed = json.loads(inner_result)
                        inner_result = parsed if parsed is not None else inner_result
                    except (json.JSONDecodeError, ValueError):
                        inner_result = {"raw_output": inner_result}

                is_success = bool(inner.get("success", False))
                if is_success and inner_result is None:
                    inner_result = {"Status": "Success", "Message": "Code executed (no return)"}
                elif is_success and inner_result == "":
                    inner_result = {"Status": "Success", "Message": "Code executed (empty return)"}

                return RevitResponse(
                    success=is_success,
                    result=inner_result,
                    error=inner.get("errorMessage") or resp.error,
                    raw=resp.raw,
                )

        return resp


class WebSocketRevitClient:
    """Adapter — makes SlotManager look like RevitClient for existing router code."""

    def __init__(self, manager: SlotManager, slot_id: str, timeout: float = 60.0):
        self._mgr = manager
        self._slot_id = slot_id
        self._timeout = timeout

    @property
    def connected(self) -> bool:
        conn = self._mgr.get_connection(self._slot_id)
        return conn is not None

    async def send_command(self, method: str, params: dict | None = None) -> RevitResponse:
        return await self._mgr.send_command(self._slot_id, method, params, self._timeout)

    async def send_code(self, code: str, parameters: list | None = None) -> RevitResponse:
        return await self._mgr.send_code(self._slot_id, code, parameters, self._timeout)

    async def ping(self) -> bool:
        try:
            resp = await self.send_command("say_hello", {"message": "ping"})
            return resp.success
        except Exception:
            return False


# ── Singleton ───────────────────────────────────────────────────────────

_manager: SlotManager | None = None


def get_slot_manager() -> SlotManager:
    global _manager
    if _manager is None:
        try:
            from server.app.deps import get_config
            max_slots = get_config().get("mcp_bridge", {}).get("max_slots", MAX_SLOTS)
        except Exception:
            max_slots = MAX_SLOTS
        _manager = SlotManager(max_slots=max_slots)
    return _manager
