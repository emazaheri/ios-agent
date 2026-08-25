"""Preflight diagnostics.

Signing expiry, a missing tunnel, and a Command-Line-Tools-only Xcode install
are the three most common causes of a dead session, and all three produce
confusing downstream errors. This module checks them up front and returns
actionable remediation instead of letting the failure surface 20 seconds later
as a connection refused.
"""

from __future__ import annotations

import platform
import plistlib
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ios_mcp.config import Settings, get_settings
from ios_mcp.devices.shell import probe, run, which
from ios_mcp.devices.tunnel import list_tunnels

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass(slots=True)
class Check:
    name: str
    status: Status
    detail: str
    remedy: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"check": self.name, "status": self.status, "detail": self.detail}
        if self.remedy:
            out["remedy"] = self.remedy
        if self.data:
            out["data"] = self.data
        return out


@dataclass(slots=True)
class DoctorReport:
    checks: list[Check]

    @property
    def can_use_simulator(self) -> bool:
        return (
            self._status("xcode") == "ok"
            and self._status("simctl") == "ok"
            and self._status("wda-bundle") == "ok"
        )

    @property
    def can_use_real_device(self) -> bool:
        """Whether a physical iPhone could actually be driven right now.

        All four parts are required. A missing signed runner or a stopped
        tunnel does not degrade gracefully: the session fails a minute later
        with a connection error that says nothing about the real cause.
        """
        return (
            self._status("go-ios") == "ok"
            and self._status("devices") == "ok"
            and self._status("tunnel") == "ok"
            and self._has_signed_runner
        )

    @property
    def _has_signed_runner(self) -> bool:
        for check in self.checks:
            if check.name == "wda-bundle":
                return "runner_app" in check.data
        return False

    def _status(self, name: str) -> Status:
        for c in self.checks:
            if c.name == name:
                return c.status
        return "fail"

    @property
    def summary(self) -> str:
        counts = {s: sum(1 for c in self.checks if c.status == s) for s in ("ok", "warn", "fail")}
        parts = [f"{counts['ok']} ok"]
        if counts["warn"]:
            parts.append(f"{counts['warn']} warning")
        if counts["fail"]:
            parts.append(f"{counts['fail']} blocking")
        capability = []
        if self.can_use_simulator:
            capability.append("simulator")
        if self.can_use_real_device:
            capability.append("real device")
        cap = ", ".join(capability) if capability else "nothing yet"
        return f"{', '.join(parts)}. Ready to automate: {cap}."

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "can_use_simulator": self.can_use_simulator,
            "can_use_real_device": self.can_use_real_device,
            "checks": [c.to_dict() for c in self.checks],
        }

    def render(self) -> str:
        icon = {"ok": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
        lines = [self.summary, ""]
        for c in self.checks:
            lines.append(f"  [{icon[c.status]}] {c.name}: {c.detail}")
            if c.remedy and c.status in ("warn", "fail"):
                lines.append(f"          -> {c.remedy}")
        return "\n".join(lines)


async def run_doctor(settings: Settings | None = None) -> DoctorReport:
    """Run every preflight check. Never raises."""
    cfg = settings or get_settings()
    checks: list[Check] = [
        _check_host(),
        _check_python(),
    ]
    xcode = await _check_xcode()
    checks.append(xcode)
    checks.append(await _check_simctl())
    checks.append(await _check_devicectl())
    goios = await _check_goios(cfg)
    checks.append(goios)
    checks.append(await _check_tunnel(cfg))
    checks.append(await _check_wda_bundle(cfg))
    checks.append(await _check_attached_devices(cfg))
    return DoctorReport(checks=checks)


def _check_host() -> Check:
    system = platform.system()
    if system == "Darwin":
        return Check(
            "host",
            "ok",
            f"macOS {platform.mac_ver()[0]} on {platform.machine()}",
        )
    return Check(
        "host",
        "warn",
        f"{system} host: simulators are unavailable, real devices work via go-ios",
        remedy="Run on macOS to use the iOS Simulator.",
    )


def _check_python() -> Check:
    v = sys.version_info
    if v >= (3, 12):
        return Check("python", "ok", f"Python {v.major}.{v.minor}.{v.micro}")
    return Check(
        "python",
        "fail",
        f"Python {v.major}.{v.minor} is too old",
        remedy="This package requires Python 3.12+. Use `uv run ios-mcp` so uv provisions it.",
    )


async def _check_xcode() -> Check:
    sel = await probe("xcode-select", "-p")
    if sel is None or not sel.ok:
        return Check(
            "xcode",
            "fail",
            "xcode-select is unavailable",
            remedy=(
                "Install Xcode from the App Store, then run "
                "`sudo xcode-select -s /Applications/Xcode.app`."
            ),
        )
    path = sel.stdout
    build = await probe("xcodebuild", "-version")
    if build is None or not build.ok:
        return Check(
            "xcode",
            "fail",
            f"only Command Line Tools are active ({path}); "
            "xcodebuild, simctl and devicectl are missing",
            remedy=(
                "Install the full Xcode from the App Store, then run "
                "`sudo xcode-select -s /Applications/Xcode.app` and accept the licence with "
                "`sudo xcodebuild -license accept`."
            ),
            data={"developer_dir": path},
        )
    version = build.stdout.splitlines()[0] if build.stdout else "unknown"
    return Check("xcode", "ok", f"{version} at {path}", data={"developer_dir": path})


async def _check_simctl() -> Check:
    if platform.system() != "Darwin":
        return Check("simctl", "skip", "not macOS")
    res = await probe("xcrun", "simctl", "help", timeout=20.0)
    if res is None or not res.ok:
        return Check(
            "simctl",
            "fail",
            "xcrun simctl is unavailable",
            remedy=(
                "Install the full Xcode (see the xcode check above); simctl ships "
                "with it, not with the Command Line Tools."
            ),
        )
    return Check("simctl", "ok", "xcrun simctl available")


async def _check_devicectl() -> Check:
    if platform.system() != "Darwin":
        return Check("devicectl", "skip", "not macOS")
    res = await probe("xcrun", "devicectl", "--version", timeout=20.0)
    if res is None or not res.ok:
        return Check(
            "devicectl",
            "warn",
            "xcrun devicectl is unavailable",
            remedy=(
                "Optional. Ships with Xcode 15+. go-ios covers the same ground for real devices."
            ),
        )
    return Check(
        "devicectl", "ok", f"devicectl {res.stdout.splitlines()[0] if res.stdout else ''}".strip()
    )


async def _check_goios(cfg: Settings) -> Check:
    binary = cfg.goios.binary
    if which(binary) is None:
        return Check(
            "go-ios",
            "warn",
            f"`{binary}` not found on PATH",
            remedy=(
                "Needed for physical iPhones. Install with `npm install -g go-ios`, "
                "or download go-ios-mac.zip from "
                "https://github.com/danielpaulus/go-ios/releases. "
                "There is no Homebrew formula."
            ),
        )
    res = await probe(binary, "version")
    version = res.stdout.strip() if res and res.ok else "unknown"
    return Check("go-ios", "ok", f"{binary} {version}", data={"version": version})


async def _check_tunnel(cfg: Settings) -> Check:
    """iOS 17+ needs a RemoteXPC tunnel before anything can reach the device."""
    if which(cfg.goios.binary) is None:
        return Check("tunnel", "skip", "go-ios not installed")

    tunnels = await list_tunnels(cfg.goios)
    if tunnels:
        udids = [str(t.get("udid", "?")) for t in tunnels]
        return Check(
            "tunnel",
            "ok",
            f"{len(tunnels)} tunnel(s) up: {', '.join(u[:12] for u in udids)}",
            data={"tunnels": tunnels},
        )

    return Check(
        "tunnel",
        "warn",
        "no RemoteXPC tunnel is running",
        remedy=(
            "Required for iOS 17+ physical devices, not for simulators. "
            "Run `sudo ios tunnel start` and leave it running; "
            "see scripts/start_tunnel.sh for a launchd setup."
        ),
    )


async def _check_wda_bundle(cfg: Settings) -> Check:
    """Verify the WebDriverAgent builds, and their signing expiry.

    Simulators and physical devices need different artifacts. A simulator needs
    an .xctestrun bundle, because WDA can only be started through xcodebuild;
    a device needs the signed runner .app, which go-ios installs and launches
    through testmanagerd.
    """
    xctestrun = cfg.wda.xctestrun_path or _discover_xctestrun()
    runner = cfg.wda.runner_app_path or _discover_wda_app()

    if xctestrun is None and runner is None:
        return Check(
            "wda-bundle",
            "warn",
            "no WebDriverAgent build found",
            remedy=(
                "Run `scripts/prepare_wda.sh simulator` (or `device` with your TEAM_ID) "
                "to build one into vendor/wda/. Nothing can be automated without it."
            ),
        )

    data: dict[str, Any] = {}
    parts: list[str] = []
    if xctestrun is not None:
        data["xctestrun"] = str(xctestrun)
        parts.append("simulator bundle ready")
    # A runner app is only usable on a phone if it carries a provisioning
    # profile. The simulator build produces the same bundle name unsigned, so
    # its presence alone says nothing about device readiness.
    profile = runner / "embedded.mobileprovision" if runner is not None else None
    if runner is not None and profile is not None and profile.exists():
        data["runner_app"] = str(runner)
        parts.append("device runner ready")
        if True:
            expiry = _provisioning_expiry(profile)
            if expiry is not None:
                days = (expiry - datetime.now(UTC)).days
                data["signing_expires"] = expiry.isoformat()
                data["days_remaining"] = days
                if days < 0:
                    return Check(
                        "wda-bundle",
                        "fail",
                        f"the device runner's provisioning profile expired {abs(days)} day(s) ago",
                        remedy=(
                            "Re-sign with scripts/prepare_wda.sh device. Free Apple IDs "
                            "get 7-day profiles; a paid Developer Program membership "
                            "gets a year."
                        ),
                        data=data,
                    )
                if days <= 2:
                    return Check(
                        "wda-bundle",
                        "warn",
                        f"the device runner's provisioning profile expires in {days} day(s)",
                        remedy="Re-sign soon with scripts/prepare_wda.sh device.",
                        data=data,
                    )

    if not parts:
        return Check(
            "wda-bundle",
            "warn",
            "a WebDriverAgent build exists but is not usable",
            remedy="Re-run scripts/prepare_wda.sh for the target you want.",
            data=data,
        )

    status: Status = "ok" if xctestrun is not None else "warn"
    remedy = (
        None
        if xctestrun is not None
        else "Run `scripts/prepare_wda.sh simulator` to enable Simulator automation."
    )
    if "device runner ready" not in parts:
        parts.append("no signed device runner")
    return Check("wda-bundle", status, ", ".join(parts), remedy=remedy, data=data)


async def _check_attached_devices(cfg: Settings) -> Check:
    binary = cfg.goios.binary
    if which(binary) is None:
        return Check("devices", "skip", "go-ios not installed; cannot enumerate physical devices")
    res = await run(binary, "list", timeout=20.0)
    if not res.ok:
        return Check(
            "devices",
            "warn",
            "could not list physical devices",
            remedy=res.stderr[:200] or "Check that the device is unlocked and trusted.",
        )
    try:
        udids = res.json().get("deviceList", [])
    except (ValueError, AttributeError):
        udids = [line for line in res.stdout.splitlines() if line.strip()]
    if not udids:
        return Check(
            "devices",
            "warn",
            "no physical iOS devices attached",
            remedy=(
                "Connect an iPhone over USB, unlock it, tap Trust, and enable "
                "Settings > Privacy & Security > Developer Mode."
            ),
        )
    return Check(
        "devices", "ok", f"{len(udids)} physical device(s) attached", data={"udids": udids}
    )


def _discover_wda_app() -> Path | None:
    """The signed runner app used on physical devices."""
    for candidate in Path("vendor/wda").glob("**/WebDriverAgentRunner-Runner.app"):
        return candidate
    return None


def _discover_xctestrun() -> Path | None:
    """The prebuilt test bundle used on simulators."""
    candidates = sorted(
        Path("vendor/wda").glob("**/WebDriverAgentRunner_iphonesimulator*.xctestrun"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _provisioning_expiry(profile: Path) -> datetime | None:
    """Extract ExpirationDate from a CMS-wrapped .mobileprovision without shelling out."""
    try:
        blob = profile.read_bytes()
        start = blob.find(b"<?xml")
        end = blob.find(b"</plist>")
        if start == -1 or end == -1:
            return None
        parsed = plistlib.loads(blob[start : end + len(b"</plist>")])
        expiry = parsed.get("ExpirationDate")
        if isinstance(expiry, datetime):
            return expiry if expiry.tzinfo else expiry.replace(tzinfo=UTC)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return None
