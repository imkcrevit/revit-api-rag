"""Regression tests for shared HTTP/WebSocket Bridge dependencies."""
from __future__ import annotations

import os
import time
import unittest

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mcp_bridge.router import _get_slot_tokens, bridge_router
from mcp_bridge.ws_relay import get_slot_manager
from server.main import _bridge_auth


class BridgeTransportTests(unittest.TestCase):
    token = "bridge-transport-test-token"

    @classmethod
    def setUpClass(cls):
        os.environ["MCP_BRIDGE_SLOT_TOKEN_1"] = cls.token
        os.environ["MCP_BRIDGE_REQUIRE_SLOT_TOKEN"] = "1"
        cls.app = FastAPI()
        cls.app.include_router(bridge_router, dependencies=[Depends(_bridge_auth)])

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("MCP_BRIDGE_SLOT_TOKEN_1", None)
        os.environ.pop("MCP_BRIDGE_REQUIRE_SLOT_TOKEN", None)
        get_slot_manager().unregister("1")

    def setUp(self):
        get_slot_manager().unregister("1")

    def test_http_routes_do_not_require_bogus_request_query(self):
        with TestClient(self.app) as client:
            response = client.get("/api/v1/bridge/slots")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("request", response.request.url.params)

    def test_slot_headers_require_matching_token(self):
        with TestClient(self.app) as client:
            missing_slot = client.get("/api/v1/bridge/unit")
            self.assertEqual(missing_slot.status_code, 403)

            denied = client.get(
                "/api/v1/bridge/unit",
                headers={"X-Slot-Id": "1"},
            )
            self.assertEqual(denied.status_code, 403)

            allowed = client.get(
                "/api/v1/bridge/unit",
                headers={"X-Slot-Id": "1", "X-Slot-Token": self.token},
            )
            self.assertEqual(allowed.status_code, 200)

    def test_websocket_auth_registers_slot(self):
        self.assertEqual(_get_slot_tokens()["1"], self.token)

        with TestClient(self.app) as client:
            with client.websocket_connect("/api/v1/bridge/ws/1") as websocket:
                websocket.send_json({
                    "type": "auth",
                    "slot_id": "1",
                    "token": self.token,
                })

                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if get_slot_manager().get_status()["connected"] == 1:
                        break
                    time.sleep(0.01)

                self.assertEqual(get_slot_manager().get_status()["connected"], 1)


if __name__ == "__main__":
    unittest.main()
