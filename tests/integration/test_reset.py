"""`ios-mcp reset` against a WebDriverAgent that is really running.

The matcher is the part no fake can prove. A unit test asserts it against a
command line somebody typed into a test file; only a real `xcodebuild` on a
real machine says whether that is the command line `ps` actually prints, with
whatever Xcode prepends to argv0 and whatever the driver appends after the
destination.

The orphan is manufactured the way a crash makes one: the pool's handle on the
runner is dropped without tearing it down, so what is left is a live process
nobody is holding. That is exactly the state `pkill -f "test-without-building"`
used to be for.

This module sorts before `test_simulator.py`, which matters: it leaves the
machine with no runner, and the session-scoped pool there starts a fresh one.
"""

from __future__ import annotations

import pytest
from simulator_support import requires_simulator

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.devices.processes import find_orphans, reset

pytestmark = [pytest.mark.simulator, requires_simulator]


async def test_reset_finds_a_live_runner_and_stops_it_once_nobody_holds_it() -> None:
    cfg = Settings()
    cfg.wda.startup_timeout_s = 240.0
    pool = DevicePool(cfg)
    lease = await pool.acquire()
    udid = lease.device.udid

    found = await find_orphans(cfg, udid=udid)
    assert found is not None, "`ps` could not be read"
    assert found, "a runner is running and reset did not see it"
    assert "xcodebuild" in {p.kind for p in found}
    # The claim has to rest on the bundle, not on the subcommand, or it would
    # match any test run on this machine.
    assert all("WebDriverAgent" in p.matched_on or "configured" in p.matched_on for p in found)

    # Make it an orphan: drop the handle, then release without tearing down.
    # This is what a crashed run leaves behind.
    lease.adapter._runner_proc = None  # type: ignore[attr-defined]
    await pool.release(udid)

    still_there = await find_orphans(cfg, udid=udid)
    assert still_there, "dropping the handle should not have stopped the process"

    report = await reset(cfg, udid=udid, kill=True)
    assert report.killed
    processes = [c for c in report.checks if c.name.startswith("process:")]
    assert processes and all(c.status == "ok" for c in processes), report.render()

    assert await find_orphans(cfg, udid=udid) == []
