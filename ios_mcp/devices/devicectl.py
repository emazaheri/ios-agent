"""Device discovery through Apple's CoreDevice (`xcrun devicectl`).

go-ios talks to usbmuxd, so it only ever sees cabled devices: unplug the phone
and `ios list` returns an empty list even while the device is happily reachable
over the network. devicectl discovers devices over Bonjour as well as USB, so
it is the only way to find a Wi-Fi-connected phone.

It needs Xcode, where go-ios does not, so the two are complementary rather than
interchangeable.
"""

from __future__ import annotations

import json
import logging
import platform
import tempfile
from pathlib import Path
from typing import Any, Literal

from ios_mcp.devices.shell import probe, which

logger = logging.getLogger(__name__)

#: How a device is attached. `wired` means USB is available and go-ios can
#: drive it; anything else means the network path is the only option.
Transport = Literal["wired", "network", "unknown"]


class DevicectlDevice:
    """One physical device as CoreDevice reports it."""

    __slots__ = ("hostnames", "model", "name", "os_version", "paired", "transport", "udid")

    def __init__(
        self,
        udid: str,
        name: str,
        os_version: str,
        model: str | None,
        transport: Transport,
        paired: bool,
        hostnames: tuple[str, ...],
    ) -> None:
        self.udid = udid
        self.name = name
        self.os_version = os_version
        self.model = model
        self.transport = transport
        self.paired = paired
        self.hostnames = hostnames

    @property
    def is_wired(self) -> bool:
        return self.transport == "wired"


async def available() -> bool:
    return platform.system() == "Darwin" and which("xcrun") is not None


async def list_devices() -> list[DevicectlDevice]:
    """Every device CoreDevice can see, cabled or not. Never raises."""
    if not await available():
        return []

    # devicectl only emits structured output to a file, not stdout.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "devices.json"
        result = await probe(
            "xcrun", "devicectl", "list", "devices", "--json-output", str(out), timeout=90.0
        )
        if result is None or not result.ok or not out.exists():
            return []
        try:
            payload = json.loads(out.read_text())
        except (OSError, ValueError):
            return []

    devices: list[DevicectlDevice] = []
    for entry in payload.get("result", {}).get("devices", []):
        parsed = _parse(entry)
        if parsed is not None:
            devices.append(parsed)
    return devices


async def find(udid: str) -> DevicectlDevice | None:
    for device in await list_devices():
        if device.udid == udid:
            return device
    return None


def _parse(entry: dict[str, Any]) -> DevicectlDevice | None:
    hardware = entry.get("hardwareProperties", {})
    properties = entry.get("deviceProperties", {})
    connection = entry.get("connectionProperties", {})

    udid = hardware.get("udid")
    if not udid:
        return None
    # Simulators and paired watches also appear here; only iPhones and iPads
    # are drivable by this project.
    platform_name = str(hardware.get("platform", "")).lower()
    if platform_name and platform_name not in ("ios", "ipados"):
        return None

    return DevicectlDevice(
        udid=str(udid),
        name=str(properties.get("name") or udid[:8]),
        os_version=str(properties.get("osVersionNumber") or "unknown"),
        model=hardware.get("productType"),
        transport=_transport(connection.get("transportType")),
        paired=str(connection.get("pairingState", "")).lower() == "paired",
        hostnames=tuple(connection.get("localHostnames") or ()),
    )


def _transport(raw: Any) -> Transport:
    value = str(raw or "").lower()
    if value in ("wired", "usb"):
        return "wired"
    if "network" in value:
        return "network"
    return "unknown"
