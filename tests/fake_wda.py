"""An in-process fake WebDriverAgent.

Layers 2 through 6 are fully testable without Xcode, a simulator, or a phone,
which is what keeps the unit suite fast and runnable in CI on Linux.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FakeWda:
    """Serves the subset of the WDA API this project uses."""

    session_id: str = "S1"
    alive: bool = True
    source_tree: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_TREE))
    screenshot_bytes: bytes = b"\x89PNG\r\n\x1a\nfake"
    window: dict[str, float] = field(default_factory=lambda: {"width": 393, "height": 852})
    alert_text: str | None = None
    alert_buttons: list[str] = field(default_factory=lambda: ["Cancel", "OK"])
    app_states: dict[str, int] = field(default_factory=dict)
    pasteboard: str = ""

    calls: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)
    settings_applied: dict[str, Any] = field(default_factory=dict)
    #: Queue of failures to inject, as (path_fragment, status, error_kind).
    failures: list[tuple[str, int, str]] = field(default_factory=list)
    sessions_created: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client_factory(self, base_url: str = "http://127.0.0.1:8100") -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, transport=self.transport())

    # -- routing -----------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body: dict[str, Any] | None = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = None
        self.calls.append((method, path, body))

        if not self.alive:
            raise httpx.ConnectError("connection refused", request=request)

        for i, (fragment, status, kind) in enumerate(self.failures):
            if fragment in path:
                self.failures.pop(i)
                return self._error(status, kind)

        return self._route(method, path, body)

    def _route(self, method: str, path: str, body: dict[str, Any] | None) -> httpx.Response:
        if path == "/status":
            return self._ok({"ready": True, "ios": {"sdkVersion": "18.2"}, "device": "iPhone"})
        if path == "/session" and method == "POST":
            self.sessions_created += 1
            return self._ok({"sessionId": self.session_id, "capabilities": {}})

        if not path.startswith(f"/session/{self.session_id}"):
            if path.startswith("/session/"):
                return self._error(404, "invalid session id")
            return self._error(404, "unknown command")

        tail = path[len(f"/session/{self.session_id}") :] or "/"

        if method == "DELETE" and tail == "/":
            return self._ok(None)
        if tail == "/appium/settings":
            self.settings_applied.update((body or {}).get("settings", {}))
            return self._ok(self.settings_applied)
        if tail == "/source":
            return self._ok(self.source_tree)
        if tail == "/screenshot":
            return self._ok(base64.b64encode(self.screenshot_bytes).decode())
        if tail == "/window/size":
            return self._ok(self.window)
        if tail == "/wda/activeAppInfo":
            return self._ok({"bundleId": "com.apple.Preferences", "pid": 1234})
        if tail == "/alert/text":
            if self.alert_text is None:
                return self._error(404, "no such alert")
            return self._ok(self.alert_text)
        if tail == "/wda/alert/buttons":
            if self.alert_text is None:
                return self._error(404, "no such alert")
            return self._ok(self.alert_buttons)
        if tail in ("/alert/accept", "/alert/dismiss"):
            self.alert_text = None
            return self._ok(None)
        if tail == "/wda/apps/state":
            return self._ok(self.app_states.get((body or {}).get("bundleId", ""), 1))
        if tail == "/wda/apps/terminate":
            self.app_states[(body or {}).get("bundleId", "")] = 1
            return self._ok(True)
        if tail in ("/wda/apps/launch", "/wda/apps/activate"):
            self.app_states[(body or {}).get("bundleId", "")] = 4
            return self._ok(None)
        if tail == "/wda/getPasteboard":
            return self._ok(base64.b64encode(self.pasteboard.encode()).decode())
        if tail == "/wda/setPasteboard":
            self.pasteboard = base64.b64decode((body or {}).get("content", "")).decode()
            return self._ok(None)

        # Everything else (taps, gestures, keys, buttons, url, orientation) succeeds.
        return self._ok(None)

    # -- envelopes ---------------------------------------------------------

    def _ok(self, value: Any) -> httpx.Response:
        return httpx.Response(200, json={"value": value, "sessionId": self.session_id})

    def _error(self, status: int, kind: str) -> httpx.Response:
        return httpx.Response(
            status,
            json={"value": {"error": kind, "message": f"fake: {kind}", "traceback": ""}},
        )

    # -- helpers for tests -------------------------------------------------

    def fail_next(self, path_fragment: str, kind: str, status: int = 404) -> None:
        self.failures.append((path_fragment, status, kind))

    def crash(self) -> None:
        """Simulate the runner process dying: the socket stops accepting."""
        self.alive = False

    def restart(self, new_session_id: str = "S2") -> None:
        self.alive = True
        self.session_id = new_session_id

    def paths_called(self) -> list[str]:
        return [p for _, p, _ in self.calls]


DEFAULT_TREE: dict[str, Any] = {
    "type": "Application",
    "name": "Settings",
    "label": "Settings",
    "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
    "isEnabled": "1",
    "isVisible": "1",
    "children": [
        {
            "type": "NavigationBar",
            "name": "Settings",
            "rect": {"x": 0, "y": 44, "width": 393, "height": 52},
            "isEnabled": "1",
            "isVisible": "1",
            "children": [
                {
                    "type": "StaticText",
                    "name": "Settings",
                    "label": "Settings",
                    "rect": {"x": 16, "y": 56, "width": 100, "height": 28},
                    "isEnabled": "1",
                    "isVisible": "1",
                    "children": [],
                }
            ],
        },
        {
            "type": "Table",
            "rect": {"x": 0, "y": 96, "width": 393, "height": 700},
            "isEnabled": "1",
            "isVisible": "1",
            "children": [
                {
                    "type": "Cell",
                    "name": "airplane_mode_cell",
                    "label": "Airplane Mode",
                    "rect": {"x": 0, "y": 100, "width": 393, "height": 44},
                    "isEnabled": "1",
                    "isVisible": "1",
                    "children": [
                        {
                            "type": "StaticText",
                            "label": "Airplane Mode",
                            "name": "Airplane Mode",
                            "rect": {"x": 16, "y": 110, "width": 150, "height": 24},
                            "isEnabled": "1",
                            "isVisible": "1",
                            "children": [],
                        },
                        {
                            "type": "Switch",
                            "name": "airplane_switch",
                            "label": "Airplane Mode",
                            "value": "0",
                            "rect": {"x": 320, "y": 108, "width": 51, "height": 31},
                            "isEnabled": "1",
                            "isVisible": "1",
                            "children": [],
                        },
                    ],
                },
                {
                    "type": "Cell",
                    "label": "Wi-Fi",
                    "name": "wifi_cell",
                    "rect": {"x": 0, "y": 144, "width": 393, "height": 44},
                    "isEnabled": "1",
                    "isVisible": "1",
                    "children": [
                        {
                            "type": "StaticText",
                            "label": "Wi-Fi",
                            "rect": {"x": 16, "y": 154, "width": 60, "height": 24},
                            "isEnabled": "1",
                            "isVisible": "1",
                            "children": [],
                        }
                    ],
                },
                {
                    "type": "Other",
                    "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
                    "isEnabled": "1",
                    "isVisible": "0",
                    "children": [],
                },
            ],
        },
    ],
}
