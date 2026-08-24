"""A scriptable fake device for exercising IosSession without hardware.

The FakeWda in fake_wda.py serves a static tree. This builds on it to model a
device whose screen actually responds to taps, which is what the action layer
needs in order to be tested at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fake_wda import FakeWda

from ios_mcp.config import Settings
from ios_mcp.devices.base import DeviceInfo
from ios_mcp.devices.pool import Lease
from ios_mcp.session import IosSession
from ios_mcp.wda.client import WdaClient
from ios_mcp.wda.session import WdaSession


@dataclass
class ScriptedWda(FakeWda):
    """A FakeWda whose tree changes in response to gestures."""

    #: Called with (path, body) after any gesture; may mutate ``source_tree``.
    on_gesture: Callable[[str, dict[str, Any] | None], None] | None = None
    gestures: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)

    _GESTURE_PATHS = (
        "/wda/tap",
        "/wda/doubleTap",
        "/wda/touchAndHold",
        "/wda/dragfromtoforduration",
        "/wda/keys",
        "/wda/homescreen",
        "/wda/pressButton",
    )

    def _route(self, method: str, path: str, body: dict[str, Any] | None):  # type: ignore[no-untyped-def]
        response = super()._route(method, path, body)
        if any(path.endswith(p) for p in self._GESTURE_PATHS):
            self.gestures.append((path, body))
            if self.on_gesture is not None:
                self.on_gesture(path, body)
        return response

    def taps(self) -> list[tuple[float, float]]:
        return [
            (b["x"], b["y"]) for p, b in self.gestures if p.endswith("/wda/tap") and b and "x" in b
        ]

    def typed(self) -> str:
        return "".join(
            "".join(b.get("value", [])) for p, b in self.gestures if p.endswith("/wda/keys") and b
        )


class FakeAdapter:
    """Minimal DeviceAdapter stand-in."""

    def __init__(self, info: DeviceInfo) -> None:
        self.info = info
        self.urls_opened: list[str] = []
        self.permissions: list[tuple[str, str, bool]] = []
        self.torn_down = False

    async def ensure_booted(self) -> None: ...

    async def ensure_runner(self):  # type: ignore[no-untyped-def]
        from ios_mcp.devices.base import WdaEndpoint

        return WdaEndpoint(base_url="http://127.0.0.1:8100", port=8100)

    async def open_url(self, url: str) -> None:
        self.urls_opened.append(url)

    async def set_permission(self, bundle_id: str, service: str, grant: bool) -> None:
        self.permissions.append((bundle_id, service, grant))

    async def teardown(self) -> None:
        self.torn_down = True


def make_session(
    tree: dict[str, Any],
    settings: Settings | None = None,
    *,
    kind: str = "simulator",
    on_gesture: Callable[[str, dict[str, Any] | None], None] | None = None,
) -> tuple[IosSession, ScriptedWda, FakeAdapter]:
    """Build an IosSession wired to a scriptable fake device."""
    cfg = settings or _fast_settings()
    fake = ScriptedWda(source_tree=tree, on_gesture=on_gesture)

    client = WdaClient("http://127.0.0.1:8100", cfg.wda)
    client._http = fake.client_factory()
    wda_session = WdaSession(client, cfg)

    info = DeviceInfo(
        udid="FAKE-UDID",
        name="Fake iPhone",
        os_version="18.2",
        kind=kind,  # type: ignore[arg-type]
        state="Booted",
        ready=True,
    )
    adapter = FakeAdapter(info)
    lease = Lease(device=info, adapter=adapter, session=wda_session)  # type: ignore[arg-type]
    return IosSession(lease, cfg), fake, adapter


def _fast_settings() -> Settings:
    """Settle timings shrunk so tests do not spend their life sleeping."""
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    return cfg
