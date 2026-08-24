"""Physical iPhone adapter, built on go-ios.

iOS 17 moved device communication from TCP/lockdown to QUIC + RemoteXPC, so a
tunnel daemon must be running before anything can reach the device. go-ios
owns that, and also starts the WebDriverAgent XCTest runner without needing
Xcode, which is what lets this work from Linux as well as macOS.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import httpx

from ios_mcp.config import Settings
from ios_mcp.devices.base import AppInfo, DeviceInfo, WdaEndpoint
from ios_mcp.devices.ports import free_port, release_port
from ios_mcp.devices.shell import run, which
from ios_mcp.errors import DeviceNotReady, NotSupported, ToolchainMissing, TunnelDown

logger = logging.getLogger(__name__)


class RealDeviceAdapter:
    """Drives one physical iPhone over USB or Wi-Fi."""

    def __init__(self, info: DeviceInfo, settings: Settings) -> None:
        self.info = info
        self.settings = settings
        self._endpoint: WdaEndpoint | None = None
        self._runner_proc: asyncio.subprocess.Process | None = None
        self._forward_proc: asyncio.subprocess.Process | None = None

    @property
    def udid(self) -> str:
        return self.info.udid

    @property
    def _ios(self) -> str:
        binary = self.settings.goios.binary
        if which(binary) is None:
            raise ToolchainMissing(
                f"go-ios (`{binary}`) is not installed",
                hint="Install with `brew install go-ios`, or set goios.binary to its path.",
            )
        return binary

    @property
    def needs_tunnel(self) -> bool:
        try:
            return int(self.info.os_version.split(".")[0]) >= 17
        except (ValueError, IndexError):
            return True  # assume modern; a spurious check is cheaper than a hang

    # -- lifecycle ---------------------------------------------------------

    async def ensure_booted(self) -> None:
        """A physical device cannot be booted; verify it is reachable instead."""
        if self.needs_tunnel:
            await self._require_tunnel()
        result = await run(self._ios, "info", "--udid", self.udid, timeout=30.0)
        if not result.ok:
            raise DeviceNotReady(
                f"Device {self.info.name} is not reachable",
                hint=(
                    "Unlock the phone, confirm the Trust prompt, and enable "
                    "Settings > Privacy & Security > Developer Mode."
                ),
                details={"stderr": result.stderr[:300]},
            )

    async def _require_tunnel(self) -> None:
        cfg = self.settings.goios
        url = f"http://{cfg.tunnel_api_host}:{cfg.tunnel_api_port}/tunnel/list"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(url)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass

        if cfg.auto_start_tunnel:
            logger.info("Starting go-ios tunnel daemon")
            await asyncio.create_subprocess_exec(
                "sudo",
                self._ios,
                "tunnel",
                "start",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            for _ in range(20):
                await asyncio.sleep(0.5)
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        if (await client.get(url)).status_code == 200:
                            return
                except httpx.HTTPError:
                    continue

        raise TunnelDown(
            f"iOS {self.info.os_version} needs a RemoteXPC tunnel, but no go-ios "
            f"tunnel daemon is listening on {cfg.tunnel_api_host}:{cfg.tunnel_api_port}",
            hint=(
                "Run `sudo ios tunnel start` once and leave it running "
                "(see scripts/start_tunnel.sh), or set goios.auto_start_tunnel = true "
                "if this process can obtain sudo without a prompt."
            ),
        )

    async def ensure_runner(self) -> WdaEndpoint:
        if self._endpoint and await self._runner_alive(self._endpoint):
            return self._endpoint

        await self.ensure_booted()
        port = free_port(*self.settings.wda.port_range)
        base_url = f"http://{self.settings.wda.host}:{port}"

        await self._start_runner()
        await self._start_forward(port)

        endpoint = WdaEndpoint(base_url=base_url, port=port, started_by_us=True)
        try:
            await self._wait_for_runner(endpoint)
        except DeviceNotReady:
            release_port(port)
            await self.teardown()
            raise
        self._endpoint = endpoint
        return endpoint

    async def _start_runner(self) -> None:
        """Launch the preinstalled WDA runner. Requires WDA 5.10+ on iOS 17+."""
        logger.info("Starting WebDriverAgent on %s", self.info.name)
        self._runner_proc = await asyncio.create_subprocess_exec(
            self._ios,
            "ui",
            "run",
            "wda",
            "--udid",
            self.udid,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(1.0)
        if self._runner_proc.returncode not in (None, 0):
            stderr = b""
            if self._runner_proc.stderr is not None:
                stderr = await self._runner_proc.stderr.read()
            raise DeviceNotReady(
                "go-ios could not start WebDriverAgent",
                hint=(
                    "The runner is probably missing or unsigned. Run "
                    "`scripts/prepare_wda.sh device` with your TEAM_ID, then install it. "
                    "On iOS 17+ the embedded XC*.framework copies must be stripped."
                ),
                details={"stderr": stderr.decode(errors="replace")[:300]},
            )

    async def _start_forward(self, port: int) -> None:
        """Forward a host port to WDA's port 8100 on the device."""
        self._forward_proc = await asyncio.create_subprocess_exec(
            self._ios,
            "forward",
            str(port),
            "8100",
            "--udid",
            self.udid,
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
            hint=(
                "Check that the runner is installed and its provisioning profile has not "
                "expired: `uv run ios-mcp doctor`. Free Apple IDs expire after 7 days."
            ),
        )

    async def _runner_alive(self, endpoint: WdaEndpoint) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{endpoint.base_url}/status")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def teardown(self) -> None:
        for proc in (self._forward_proc, self._runner_proc):
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except TimeoutError:
                    proc.kill()
        self._forward_proc = None
        self._runner_proc = None
        if self._endpoint:
            release_port(self._endpoint.port)
        self._endpoint = None

    # -- app and device control -------------------------------------------

    async def install_app(self, path: Path) -> str:
        await run(
            self._ios,
            "install",
            "--path",
            str(path),
            "--udid",
            self.udid,
            timeout=600.0,
            check=True,
        )
        return str(path)

    async def list_apps(self, kind: Literal["user", "system", "all"] = "user") -> list[AppInfo]:
        argv = [self._ios, "apps", "--udid", self.udid]
        if kind in ("system", "all"):
            argv.append("--system")
        result = await run(*argv, timeout=60.0)
        if not result.ok:
            return []
        try:
            entries: list[dict[str, Any]] = result.json()
        except (ValueError, AttributeError):
            return []
        apps = [
            AppInfo(
                bundle_id=e.get("CFBundleIdentifier", ""),
                name=e.get("CFBundleDisplayName") or e.get("CFBundleName"),
                version=e.get("CFBundleShortVersionString"),
                kind="system" if e.get("ApplicationType") == "System" else "user",
            )
            for e in entries
            if e.get("CFBundleIdentifier")
        ]
        if kind == "all":
            return apps
        return [a for a in apps if a.kind == kind]

    async def open_url(self, url: str) -> None:
        """No go-ios equivalent; the WDA session handles this instead."""
        raise NotSupported(
            "Opening a URL on a physical device goes through the WDA session",
            hint="Use the session's open_url, which posts to /session/:id/url.",
        )

    async def set_permission(self, bundle_id: str, service: str, grant: bool) -> None:
        raise NotSupported(
            "Privacy permissions cannot be set programmatically on a physical device",
            hint=(
                "simctl privacy is Simulator-only. On a real phone, drive the "
                "Settings app or accept the permission alert with ios_handle_alert."
            ),
        )

    async def system_log(
        self, since: float | None = None, predicate: str | None = None
    ) -> AsyncIterator[str]:
        proc = await asyncio.create_subprocess_exec(
            self._ios,
            "syslog",
            "--udid",
            self.udid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if predicate and predicate not in line:
                    continue
                yield line
        finally:
            if proc.returncode is None:
                proc.terminate()
