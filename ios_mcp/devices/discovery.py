"""Enumerate simulators and attached physical devices.

Kept separate from the adapters so that ``ios_list_devices`` works even when no
session has been opened and parts of the toolchain are missing.
"""

from __future__ import annotations

import platform
import re
from typing import Any

from ios_mcp.config import Settings, get_settings
from ios_mcp.devices.base import DeviceInfo
from ios_mcp.devices.shell import probe, which

_RUNTIME_RE = re.compile(r"iOS[-\s](\d+)[-.](\d+)(?:[-.](\d+))?", re.IGNORECASE)


async def list_devices(settings: Settings | None = None) -> list[DeviceInfo]:
    """All known simulators and attached devices, simulators first."""
    cfg = settings or get_settings()
    devices: list[DeviceInfo] = []
    devices.extend(await list_simulators())
    devices.extend(await list_real_devices(cfg))
    return devices


async def list_simulators() -> list[DeviceInfo]:
    if platform.system() != "Darwin":
        return []
    res = await probe("xcrun", "simctl", "list", "devices", "available", "--json", timeout=30.0)
    if res is None or not res.ok:
        return []
    try:
        payload: dict[str, Any] = res.json()
    except ValueError:
        return []

    out: list[DeviceInfo] = []
    for runtime, entries in payload.get("devices", {}).items():
        os_version = _runtime_to_version(runtime)
        if os_version is None:
            continue  # skip watchOS / tvOS / visionOS runtimes
        for entry in entries:
            if not entry.get("isAvailable", True):
                continue
            state = entry.get("state", "Shutdown")
            out.append(
                DeviceInfo(
                    udid=entry["udid"],
                    name=entry.get("name", "Simulator"),
                    os_version=os_version,
                    kind="simulator",
                    state=state,
                    model=entry.get("deviceTypeIdentifier"),
                    ready=True,  # a shutdown simulator can always be booted on demand
                )
            )
    out.sort(key=lambda d: (d.state != "Booted", d.os_version, d.name), reverse=False)
    return out


async def list_real_devices(settings: Settings | None = None) -> list[DeviceInfo]:
    cfg = settings or get_settings()
    binary = cfg.goios.binary
    if which(binary) is None:
        return []
    res = await probe(binary, "list", timeout=20.0)
    if res is None or not res.ok:
        return []
    try:
        udids: list[str] = res.json().get("deviceList", [])
    except (ValueError, AttributeError):
        udids = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]

    out: list[DeviceInfo] = []
    for udid in udids:
        detail = await probe(binary, "info", "--udid", udid, timeout=20.0)
        name, os_version, model = udid[:8], "unknown", None
        blockers: list[str] = []
        if detail is not None and detail.ok:
            try:
                info: dict[str, Any] = detail.json()
                name = info.get("DeviceName", name)
                os_version = str(info.get("ProductVersion", os_version))
                model = info.get("ProductType")
            except ValueError:
                blockers.append("could not read device info")
        else:
            blockers.append("device info unavailable; is it unlocked and trusted?")

        if _needs_tunnel(os_version):
            blockers.append("iOS 17+ requires a running `sudo ios tunnel start` daemon")

        out.append(
            DeviceInfo(
                udid=udid,
                name=name,
                os_version=os_version,
                kind="device",
                state="connected",
                model=model,
                ready=not blockers,
                blockers=tuple(blockers),
            )
        )
    return out


def _runtime_to_version(runtime: str) -> str | None:
    """`com.apple.CoreSimulator.SimRuntime.iOS-18-2` -> `18.2`. None for non-iOS."""
    if "iOS" not in runtime:
        return None
    match = _RUNTIME_RE.search(runtime)
    if not match:
        return None
    parts = [p for p in match.groups() if p]
    return ".".join(parts)


def _needs_tunnel(os_version: str) -> bool:
    try:
        return int(os_version.split(".")[0]) >= 17
    except (ValueError, IndexError):
        return False
