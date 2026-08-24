"""Session and device tools: diagnostics and device discovery."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ios_mcp.config import Settings
from ios_mcp.devices.discovery import list_devices
from ios_mcp.devices.doctor import run_doctor

READ_ONLY: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def register(mcp: FastMCP, cfg: Settings) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def ios_doctor() -> dict[str, Any]:
        """Check that this machine can drive an iOS device, and say how to fix what it can't.

        Run this first when a session fails to start, when a device does not
        appear, or after an Xcode or iOS upgrade. Every failing check comes with
        a concrete remedy.
        """
        report = await run_doctor(cfg)
        return report.to_dict()

    @mcp.tool(annotations=READ_ONLY)
    async def ios_list_devices(
        kind: Annotated[
            str,
            Field(description="Filter by device kind: 'simulator', 'device', or 'all'."),
        ] = "all",
    ) -> dict[str, Any]:
        """List iOS Simulators and attached physical iPhones available for automation.

        `ready` is false when something blocks automation; `blockers` says what.
        """
        devices = await list_devices(cfg)
        if kind in ("simulator", "device"):
            devices = [d for d in devices if d.kind == kind]
        return {
            "count": len(devices),
            "devices": [d.to_dict() for d in devices],
            "hint": (
                None if devices else "No devices found. Run ios_doctor to see what is missing."
            ),
        }
