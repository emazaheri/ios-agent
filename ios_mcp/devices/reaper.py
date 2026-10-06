"""Stop the processes this interpreter started when it dies, however it dies.

`teardown()` stops the runner, the port forward and the tunnel, but only when
something calls it, and a process that is killed calls nothing. Measured on a
simulator: an MCP server whose client closed stdin, and one sent SIGKILL, both
left `xcodebuild test-without-building` reparented to launchd and the runner
still serving inside the simulator, holding the device until `ios-mcp reset`.
The other iOS agent tools carry the same failure in their trackers, up to a
4GB leak and hundreds of orphaned `simctl` processes.

macOS has no parent-death signal, so the watch has to live outside the process
being watched. The first process this interpreter guards also starts a reaper:
`python -m ios_mcp.devices.reaper`, reading a pipe whose write end only this
interpreter holds. The kernel closes that end when the interpreter exits, for
any reason including SIGKILL, and the reaper then stops every process group it
was told to guard. Each guarded process is started in its own session, so its
group is itself and everything it spawned.

The reaper is a plain stdin loop with no dependencies, and runs in a session of
its own so that a Ctrl-C delivered to the terminal's process group does not
take it down before it has done its job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from typing import IO, Any

logger = logging.getLogger(__name__)

#: How long a guarded group gets to exit on SIGTERM before SIGKILL.
_GRACE_S = 5.0

_reaper: subprocess.Popen[bytes] | None = None


async def spawn_guarded(*argv: str, **kwargs: Any) -> asyncio.subprocess.Process:
    """Start a long-lived child in its own session, guarded by the reaper."""
    proc = await asyncio.create_subprocess_exec(*argv, start_new_session=True, **kwargs)
    guard(proc.pid)
    return proc


async def stop_guarded(proc: asyncio.subprocess.Process | None, *, timeout_s: float = 10.0) -> None:
    """Stop a guarded child and everything it started, then stop guarding it.

    The group, not the leader: `xcodebuild` and `ios` both start helpers of
    their own, and a terminate sent to the leader alone left them to finish or
    not on their own schedule.
    """
    if proc is None:
        return
    if proc.returncode is None:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGTERM)
        # The leader directly as well: a group it does not own, such as a
        # tunnel started under sudo, refuses the group signal.
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except TimeoutError:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                proc.kill()
    release(proc.pid)


def guard(pid: int) -> None:
    """Stop ``pid``'s process group if this interpreter dies before releasing it.

    ``pid`` must lead its own group, which `start_new_session=True` arranges.
    Best effort: a reaper that cannot be started is logged and skipped, since
    failing the launch over it would trade a possible orphan for a certain
    outage.
    """
    _send(f"+{pid}\n")


def release(pid: int) -> None:
    """Stop guarding ``pid``, because it has been stopped deliberately."""
    _send(f"-{pid}\n")


def _send(line: str) -> None:
    global _reaper
    try:
        if _reaper is None or _reaper.poll() is not None:
            if line.startswith("-"):
                return
            _reaper = subprocess.Popen(
                [sys.executable, "-m", "ios_mcp.devices.reaper"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        assert _reaper.stdin is not None
        _reaper.stdin.write(line.encode())
        _reaper.stdin.flush()
    except OSError:
        logger.warning("Could not reach the reaper; %r is unguarded", line.strip(), exc_info=True)


def watch(stream: IO[str]) -> set[int]:
    """Read guard and release lines until EOF. Returns what is still guarded."""
    guarded: set[int] = set()
    for line in stream:
        line = line.strip()
        if not line[1:].isdigit():
            continue
        pid = int(line[1:])
        if line[0] == "+":
            guarded.add(pid)
        elif line[0] == "-":
            guarded.discard(pid)
    return guarded


def stop_groups(pgids: set[int], *, grace_s: float = _GRACE_S) -> None:
    """SIGTERM each group, then SIGKILL whatever is still there after ``grace_s``."""
    # By group, not by leader: xcodebuild may already be gone while the
    # processes it started are not, and those are what hold the device.
    live = {pgid for pgid in pgids if _group_alive(pgid)}
    for pgid in live:
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while live and time.monotonic() < deadline:
        time.sleep(0.1)
        live = {pgid for pgid in live if _group_alive(pgid)}
    for pgid in live:
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    stop_groups(watch(sys.stdin))


if __name__ == "__main__":
    main()
