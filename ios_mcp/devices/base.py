"""Device abstraction shared by the simulator and real-device adapters.

Everything platform-specific lives behind ``DeviceAdapter`` so that layers 2
through 6 never branch on device kind.
"""

from __future__ import annotations

import difflib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

DeviceKind = Literal["simulator", "device"]


@dataclass(slots=True, frozen=True)
class DeviceInfo:
    udid: str
    name: str
    os_version: str
    kind: DeviceKind
    state: str = "unknown"
    model: str | None = None
    ready: bool = False
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "udid": self.udid,
            "name": self.name,
            "os_version": self.os_version,
            "kind": self.kind,
            "state": self.state,
            "ready": self.ready,
        }
        if self.model:
            out["model"] = self.model
        if self.blockers:
            out["blockers"] = list(self.blockers)
        return out


@dataclass(slots=True, frozen=True)
class AppInfo:
    bundle_id: str
    name: str | None = None
    version: str | None = None
    kind: Literal["user", "system"] = "user"

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "bundle_id": self.bundle_id,
                "name": self.name,
                "version": self.version,
                "kind": self.kind,
            }.items()
            if v is not None
        }


@dataclass(slots=True)
class WdaEndpoint:
    """Where a live WebDriverAgent runner can be reached."""

    base_url: str
    port: int
    started_by_us: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeviceAdapter(Protocol):
    """Lifecycle and out-of-band control for one device.

    In-band UI automation goes through the WDA client instead; this protocol
    covers only what WebDriverAgent cannot do.
    """

    info: DeviceInfo

    async def ensure_booted(self) -> None: ...

    async def ensure_runner(self) -> WdaEndpoint: ...

    async def install_app(self, path: Path) -> str: ...

    async def list_apps(self, kind: Literal["user", "system", "all"] = "user") -> list[AppInfo]: ...

    async def open_url(self, url: str) -> None: ...

    async def set_permission(self, bundle_id: str, service: str, grant: bool) -> None: ...

    def system_log(
        self, since: float | None = None, predicate: str | None = None
    ) -> AsyncIterator[str]: ...

    async def teardown(self) -> None: ...


#: Below this ratio a fuzzy app-name match is a guess rather than a match.
_APP_FUZZY_THRESHOLD = 0.6


def best_app_match(name: str, apps: list[AppInfo]) -> str | None:
    """Rank installed apps against a spoken name, best first, ties broken."""
    needle = name.strip().lower()
    if not needle:
        return None

    def label(app: AppInfo) -> str:
        return (app.name or "").strip().lower()

    for app in apps:  # exact name, the overwhelmingly common case
        if label(app) == needle:
            return app.bundle_id
    for app in apps:  # someone pasted a bundle id
        if app.bundle_id.lower() == needle:
            return app.bundle_id
    partial = [a for a in apps if needle in label(a) or label(a) in needle]
    if partial:  # shortest name containing it: "Maps" over "Maps Settings"
        return min(partial, key=lambda a: len(label(a))).bundle_id
    scored = [
        (difflib.SequenceMatcher(None, needle, label(a)).ratio(), a) for a in apps if label(a)
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored and scored[0][0] >= _APP_FUZZY_THRESHOLD:
        return scored[0][1].bundle_id
    return None


def closest_app_names(name: str, apps: list[AppInfo]) -> list[str]:
    needle = name.strip().lower()
    scored = [
        (difflib.SequenceMatcher(None, needle, (a.name or "").lower()).ratio(), a)
        for a in apps
        if a.name
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [f"{a.name} ({a.bundle_id})" for _, a in scored[:5]]
