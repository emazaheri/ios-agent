"""Children outlive no parent: the reaper, and the server's lifespan.

Real processes, no device. The thing under test is process-group bookkeeping,
which a fake cannot get wrong in the ways the real one can.
"""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest
from fastmcp import Client

from ios_mcp.config import Settings
from ios_mcp.devices import reaper
from ios_mcp.server.app import build_server


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie still answers kill(0); it is not running anything.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def _wait_gone(pid: int, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_guard_and_release_lines_leave_what_is_still_guarded() -> None:
    assert reaper.watch(io.StringIO("+10\n+11\n-10\n+12\nnoise\n")) == {11, 12}


def test_stopping_a_group_stops_the_children_the_leader_started() -> None:
    """xcodebuild starts helpers; stopping only the leader left them running."""
    leader = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 60 & echo $!; wait"],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert leader.stdout is not None
    child = int(leader.stdout.readline())

    reaper.stop_groups({leader.pid}, grace_s=2.0)

    leader.wait(timeout=5)
    assert _wait_gone(child)


def test_a_killed_parent_does_not_leave_its_runner_behind() -> None:
    """The case `teardown()` cannot cover: SIGKILL runs no Python at all."""
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import asyncio, sys
                from ios_mcp.devices.reaper import spawn_guarded

                async def go():
                    proc = await spawn_guarded("sleep", "60")
                    print(proc.pid, flush=True)
                    await asyncio.sleep(60)

                asyncio.run(go())
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout is not None
    runner = int(parent.stdout.readline())
    assert _alive(runner)

    os.kill(parent.pid, signal.SIGKILL)
    parent.wait(timeout=5)

    assert _wait_gone(runner), "the runner outlived the process that started it"


async def test_a_runner_stopped_on_purpose_is_released() -> None:
    proc = await reaper.spawn_guarded("sleep", "60")
    await reaper.stop_guarded(proc, timeout_s=2.0)
    assert proc.returncode is not None


async def test_the_server_tears_down_when_its_client_goes(monkeypatch) -> None:
    """Over stdio a client quitting is stdin closing; nothing used to notice."""
    mcp = build_server(Settings())
    calls: list[str] = []

    async def shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(mcp.ios_context, "shutdown", shutdown)  # type: ignore[attr-defined]
    async with Client(mcp) as client:
        await client.list_tools()
    assert calls == ["shutdown"]


@pytest.fixture(autouse=True)
def _no_leftover_reaper():
    """Each test's reaper is its own; one left running would guard the next test's pids."""
    yield
    if reaper._reaper is not None and reaper._reaper.poll() is None:
        reaper._reaper.stdin.close()  # type: ignore[union-attr]
        reaper._reaper.wait(timeout=10)
    reaper._reaper = None
