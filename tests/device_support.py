"""Skip unless a real iPhone is actually drivable.

Mirrors `simulator_support.py`, and deliberately kept out of any conftest so
several suites can import it without the module-name collision two conftests
would create.

The check is not "is a phone plugged in". `ios list` speaks to usbmuxd and
returns nothing for a device on Wi-Fi, so discovery has to go through the same
merged path the pool uses, and readiness has to be asked rather than assumed: a
paired phone with no signed runner is discoverable and undrivable.
"""

from __future__ import annotations

import asyncio
import os

import pytest


def device_available() -> bool:
    """True when a physical device is present and reports itself ready.

    Opt-in on top of that. Tier 3 drives someone's actual phone and changes
    settings on it, so it does not run because hardware happened to be nearby;
    `IOS_MCP_ALLOW_DEVICE=1` is the deliberate act.
    """
    if os.environ.get("IOS_MCP_ALLOW_DEVICE") != "1":
        return False
    try:
        from ios_mcp.config import Settings
        from ios_mcp.devices.discovery import list_real_devices

        devices = asyncio.run(list_real_devices(Settings()))
    except Exception:
        return False
    return any(d.ready for d in devices)


requires_device = pytest.mark.skipif(
    not device_available(),
    reason=(
        "no ready physical iPhone, or IOS_MCP_ALLOW_DEVICE is not 1. "
        "Tier 3 changes settings on a real phone, so it is opt-in."
    ),
)
