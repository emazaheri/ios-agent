"""Enumerate simulators and attached physical devices.

Kept separate from the adapters so that ``ios_list_devices`` works even when no
session has been opened and parts of the toolchain are missing.
"""

from __future__ import annotations

import platform
import re
from typing import Any

from ios_mcp.config import Settings, get_settings
from ios_mcp.devices import devicectl
from ios_mcp.devices.base import DeviceInfo
from ios_mcp.devices.shell import probe, run, which
from ios_mcp.errors import DeviceNotReady

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
    """Physical devices, from both go-ios and CoreDevice.

    Neither source is sufficient alone. go-ios only sees cabled devices, so a
    phone on Wi-Fi is invisible to it; devicectl sees both but needs Xcode,
    which go-ios does not. Merging them means a device shows up however it
    happens to be attached.
    """
    cfg = settings or get_settings()
    by_udid: dict[str, DeviceInfo] = {}

    for device in await _from_devicectl():
        by_udid[device.udid] = device
    for device in await _from_goios(cfg):
        # go-ios is authoritative when present: if it can see the device, the
        # USB path is available, which is the faster one to drive.
        by_udid[device.udid] = device
    return list(by_udid.values())


async def _from_devicectl() -> list[DeviceInfo]:
    out: list[DeviceInfo] = []
    for device in await devicectl.list_devices():
        blockers: list[str] = []
        if not device.paired:
            blockers.append("device is not paired; connect it over USB once and tap Trust")
        if not device.is_wired:
            blockers.append(
                "connected over the network, so WebDriverAgent is launched via "
                "xcodebuild rather than go-ios"
            )
        out.append(
            DeviceInfo(
                udid=device.udid,
                name=device.name,
                os_version=device.os_version,
                kind="device",
                state="connected" if device.paired else "unpaired",
                model=device.model,
                ready=device.paired,
                blockers=tuple(blockers),
            )
        )
    return out


async def _from_goios(cfg: Settings) -> list[DeviceInfo]:
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


async def create_simulator(
    name: str = "iPhone 17", runtime: str | None = None, settings: Settings | None = None
) -> DeviceInfo:
    """Create one simulator, and return it.

    Only worth doing when a runtime is already installed: the runtime is the
    8 GB half, and this is the 0.2-second half. Reversible with
    `xcrun simctl delete`, which is what makes it reasonable to offer at all.

    Raises `DeviceNotReady` if there is nothing to create it from, rather than
    reporting success for a device that does not exist.
    """
    cfg = settings or get_settings()
    del cfg  # discovery shells out directly; kept for signature symmetry

    listing = await run("xcrun", "simctl", "list", "runtimes", "--json", timeout=30.0)
    available = [
        r
        for r in (listing.json().get("runtimes", []) if listing.ok else [])
        if r.get("isAvailable") and "iOS" in str(r.get("name", ""))
    ]
    if not available:
        raise DeviceNotReady(
            "no iOS simulator runtime is installed",
            hint="Run `xcodebuild -downloadPlatform iOS` first; it is about 8 GB.",
        )

    chosen = next((r for r in available if r.get("identifier") == runtime), available[-1])
    runtime = str(chosen.get("identifier"))
    device_type = _device_type_for(name, chosen)
    result = await run("xcrun", "simctl", "create", name, device_type, runtime, timeout=120.0)
    if not result.ok:
        raise DeviceNotReady(
            f"could not create a simulator called {name!r}",
            hint=result.stderr[:300] or "Check `xcrun simctl list devicetypes`.",
        )

    udid = result.stdout.strip()
    return DeviceInfo(
        udid=udid,
        name=name,
        os_version=_version_from(runtime),
        kind="simulator",
        state="Shutdown",
        ready=True,
    )


def _device_type_for(name: str, runtime: dict[str, Any]) -> str:
    """A device type this runtime actually supports.

    Read from the runtime's own `supportedDeviceTypes` rather than the flat
    `simctl list devicetypes`, which lists every type this Xcode knows about
    including ones the runtime refuses. Picking from the flat list produced
    `Incompatible device`: it is ordered newest first, so taking the last entry
    reached an iPhone 6s that iOS 26 will not run.

    A name rather than an identifier because identifiers change shape between
    Xcode versions and a name is what a person would recognise.
    """
    supported = runtime.get("supportedDeviceTypes", [])
    for entry in supported:
        if str(entry.get("name", "")).lower() == name.lower():
            return str(entry.get("identifier"))
    iphones = [t for t in supported if str(t.get("name", "")).startswith("iPhone")]
    if not iphones:
        raise DeviceNotReady(
            f"{runtime.get('name')} supports no iPhone device type",
            hint="`xcrun simctl list runtimes --json` shows what it does support.",
        )
    # Newest first, which is the order simctl reports them in.
    return str(iphones[0].get("identifier"))


def _version_from(runtime: str) -> str:
    """`...SimRuntime.iOS-26-5` -> `26.5`."""
    tail = runtime.rsplit(".", 1)[-1]
    return tail.removeprefix("iOS-").replace("-", ".")
