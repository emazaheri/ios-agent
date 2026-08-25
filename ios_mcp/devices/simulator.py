"""Simulator adapter, built on `xcrun simctl`.

simctl does a lot that WebDriverAgent cannot: granting privacy permissions,
overriding location, freezing the status bar for reproducible screenshots, and
streaming os_log. Those all live here.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from ios_mcp.config import Settings
from ios_mcp.devices.base import AppInfo, DeviceInfo, WdaEndpoint
from ios_mcp.devices.ports import free_port, release_port
from ios_mcp.devices.shell import run
from ios_mcp.errors import DeviceNotReady, NotSupported, ToolchainMissing

logger = logging.getLogger(__name__)

SIM_WDA_BUNDLE = "com.facebook.WebDriverAgentRunner.xctrunner"

#: simctl privacy service names, mapped from the friendlier names we expose.
PERMISSION_SERVICES = {
    "location": "location",
    "location-always": "location-always",
    "photos": "photos",
    "camera": "camera",
    "microphone": "microphone",
    "contacts": "contacts",
    "calendar": "calendar",
    "reminders": "reminders",
    "motion": "motion",
    "media-library": "media-library",
    "notifications": "user-tracking",
    "all": "all",
}


class SimulatorAdapter:
    """Drives one iOS Simulator."""

    def __init__(self, info: DeviceInfo, settings: Settings) -> None:
        self.info = info
        self.settings = settings
        self._endpoint: WdaEndpoint | None = None
        self._runner_proc: asyncio.subprocess.Process | None = None

    @property
    def udid(self) -> str:
        return self.info.udid

    # -- lifecycle ---------------------------------------------------------

    async def ensure_booted(self) -> None:
        state = await self._state()
        if state == "Booted":
            return
        logger.info("Booting simulator %s (%s)", self.info.name, self.udid)
        result = await run("xcrun", "simctl", "boot", self.udid, timeout=180.0)
        if not result.ok and "current state: Booted" not in result.stderr:
            raise DeviceNotReady(
                f"Could not boot simulator {self.info.name}",
                hint=result.stderr[:300] or "Try `xcrun simctl shutdown all` and retry.",
            )
        await run("xcrun", "simctl", "bootstatus", self.udid, "-b", timeout=180.0)

    async def ensure_runner(self) -> WdaEndpoint:
        """Start WebDriverAgent and wait for it to answer /status.

        WDA runs as an XCTest bundle, so it has to be launched by testmanagerd
        via xcodebuild. `simctl launch` on the .xctrunner app cannot work: the
        app aborts immediately without an XCTestConfiguration. (The prebuilt-app
        route does work on physical devices, where go-ios drives testmanagerd
        directly.)
        """
        if self._endpoint and await self._runner_alive(self._endpoint):
            return self._endpoint

        # A dead endpoint leaves a stale runner process holding the simulator
        # and a reserved port. Relaunching without clearing both means the new
        # runner fights the corpse and neither comes up.
        if self._endpoint is not None or self._runner_proc is not None:
            await self.teardown()

        await self.ensure_booted()
        port = free_port(*self.settings.wda.port_range)
        base_url = f"http://{self.settings.wda.host}:{port}"

        xctestrun = self.settings.wda.xctestrun_path or _discover_xctestrun()
        if xctestrun is not None:
            await self._launch_prebuilt(xctestrun, port)
        else:
            await self._launch_from_source(port)

        endpoint = WdaEndpoint(base_url=base_url, port=port, started_by_us=True)
        try:
            await self._wait_for_runner(endpoint)
        except DeviceNotReady:
            release_port(port)
            await self.teardown()
            raise
        self._endpoint = endpoint
        return endpoint

    async def _launch_prebuilt(self, xctestrun: Path, port: int) -> None:
        """Run an already-built test bundle. Seconds, rather than minutes."""
        logger.info("Starting WebDriverAgent from %s", xctestrun.name)
        self._runner_proc = await asyncio.create_subprocess_exec(
            "xcodebuild",
            "test-without-building",
            "-xctestrun",
            str(xctestrun),
            "-destination",
            f"id={self.udid}",
            env={**_env(), "USE_PORT": str(port)},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _launch_from_source(self, port: int) -> None:
        """Build and run WDA from a checkout. Slow; prefer a prebuilt bundle."""
        source = Path("vendor/wda/WebDriverAgent/WebDriverAgent.xcodeproj")
        if not source.exists():
            raise ToolchainMissing(
                "No WebDriverAgent build is available for the simulator",
                hint=(
                    "Run scripts/prepare_wda.sh to build one into vendor/wda/. "
                    "It clones appium/WebDriverAgent and builds it once."
                ),
            )
        if shutil.which("xcodebuild") is None:
            raise ToolchainMissing(
                "xcodebuild is required to run WebDriverAgent",
                hint="Install the full Xcode, then run scripts/prepare_wda.sh.",
            )
        logger.info("Building and launching WDA from source; this takes a few minutes")
        self._runner_proc = await asyncio.create_subprocess_exec(
            "xcodebuild",
            "-project",
            str(source),
            "-scheme",
            "WebDriverAgentRunner",
            "-destination",
            f"id={self.udid}",
            "test-without-building",
            env={**_env(), "USE_PORT": str(port)},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _wait_for_runner(self, endpoint: WdaEndpoint) -> None:
        deadline = asyncio.get_running_loop().time() + self.settings.wda.startup_timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if await self._runner_alive(endpoint):
                return
            await asyncio.sleep(0.5)
        raise DeviceNotReady(
            f"WebDriverAgent did not become ready within "
            f"{self.settings.wda.startup_timeout_s:.0f}s on {endpoint.base_url}",
            hint="Run `uv run ios-mcp doctor`; a stale or unsigned runner is the usual cause.",
        )

    async def _runner_alive(self, endpoint: WdaEndpoint) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{endpoint.base_url}/status")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def teardown(self) -> None:
        if self._runner_proc and self._runner_proc.returncode is None:
            self._runner_proc.terminate()
            try:
                await asyncio.wait_for(self._runner_proc.wait(), timeout=10.0)
            except TimeoutError:
                self._runner_proc.kill()
        self._runner_proc = None
        if self._endpoint is not None:
            release_port(self._endpoint.port)
        self._endpoint = None

    # -- app and device control -------------------------------------------

    async def install_app(self, path: Path) -> str:
        await run("xcrun", "simctl", "install", self.udid, str(path), timeout=300.0, check=True)
        return str(path)

    async def list_apps(self, kind: Literal["user", "system", "all"] = "user") -> list[AppInfo]:
        result = await run("xcrun", "simctl", "listapps", self.udid, timeout=60.0)
        if not result.ok:
            return []
        apps = _parse_listapps(result.stdout)
        if kind == "all":
            return apps
        return [a for a in apps if a.kind == kind]

    async def open_url(self, url: str) -> None:
        await run("xcrun", "simctl", "openurl", self.udid, url, timeout=30.0, check=True)

    async def set_permission(self, bundle_id: str, service: str, grant: bool) -> None:
        mapped = PERMISSION_SERVICES.get(service, service)
        action = "grant" if grant else "revoke"
        await run(
            "xcrun",
            "simctl",
            "privacy",
            self.udid,
            action,
            mapped,
            bundle_id,
            timeout=30.0,
            check=True,
        )

    async def set_location(self, latitude: float, longitude: float) -> None:
        await run(
            "xcrun",
            "simctl",
            "location",
            self.udid,
            "set",
            f"{latitude},{longitude}",
            timeout=30.0,
            check=True,
        )

    async def set_appearance(self, appearance: Literal["light", "dark"]) -> None:
        await run(
            "xcrun", "simctl", "ui", self.udid, "appearance", appearance, timeout=30.0, check=True
        )

    async def set_status_bar(self, **overrides: Any) -> None:
        """Freeze the status bar so screenshots are reproducible across runs."""
        argv = ["xcrun", "simctl", "status_bar", self.udid, "override"]
        for key, value in overrides.items():
            argv.extend([f"--{key}", str(value)])
        await run(*argv, timeout=30.0, check=True)

    async def system_log(
        self, since: float | None = None, predicate: str | None = None
    ) -> AsyncIterator[str]:
        argv = ["xcrun", "simctl", "spawn", self.udid, "log", "show", "--style", "compact"]
        if since is not None:
            argv.extend(["--last", f"{int(since)}s"])
        if predicate:
            argv.extend(["--predicate", predicate])
        result = await run(*argv, timeout=60.0)
        for line in result.stdout.splitlines():
            yield line

    async def _state(self) -> str:
        result = await run("xcrun", "simctl", "list", "devices", "--json", timeout=30.0)
        if not result.ok:
            raise ToolchainMissing(
                "xcrun simctl is unavailable",
                hint="Install the full Xcode; simctl does not ship with the Command Line Tools.",
            )
        for entries in result.json().get("devices", {}).values():
            for entry in entries:
                if entry.get("udid") == self.udid:
                    return str(entry.get("state", "Unknown"))
        raise DeviceNotReady(f"Simulator {self.udid} no longer exists")


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)


def _discover_xctestrun() -> Path | None:
    """Find a prebuilt simulator test bundle produced by scripts/prepare_wda.sh."""
    candidates = sorted(
        Path("vendor/wda").glob("**/WebDriverAgentRunner_iphonesimulator*.xctestrun"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _parse_listapps(text: str) -> list[AppInfo]:
    """`simctl listapps` emits an old-style plist; parse it without a plist lib.

    The output is not valid XML or JSON, so a light structural parse is the
    pragmatic option. Each top-level entry is `"bundle.id" = { ... };`.
    """
    import re

    apps: list[AppInfo] = []
    for match in re.finditer(r'"?([\w.\-]+)"?\s*=\s*\{(.*?)\n    \};', text, re.DOTALL):
        bundle_id, body = match.group(1), match.group(2)
        if "." not in bundle_id:
            continue
        name = _plist_field(body, "CFBundleDisplayName") or _plist_field(body, "CFBundleName")
        version = _plist_field(body, "CFBundleShortVersionString")
        app_type = _plist_field(body, "ApplicationType") or "User"
        apps.append(
            AppInfo(
                bundle_id=bundle_id,
                name=name,
                version=version,
                kind="system" if app_type.lower() == "system" else "user",
            )
        )
    return apps


def _plist_field(body: str, key: str) -> str | None:
    import re

    match = re.search(rf'{key}\s*=\s*"?([^";\n]+)"?;', body)
    return match.group(1).strip() if match else None


async def _unsupported(*_: object, **__: object) -> None:
    raise NotSupported("Not available on the Simulator")
