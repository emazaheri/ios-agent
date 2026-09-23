"""WebDriverAgent processes this interpreter did not start.

`DeviceAdapter.teardown()` kills the runner and the port forward it launched
itself, through handles it is holding. A run that crashed, was killed, or was
interrupted between the launch and the teardown leaves those processes behind
with nobody holding a handle to them, and a leftover runner holds the device:
the next run waits out `wda.startup_timeout_s` and fails. Until now the
documented fix was `pkill -f "test-without-building"`, which is a blind match
on a command line that a developer's own test run shares character for
character.

So the work here is the matcher, not the killing. The rule is that a process is
claimed only when something in its command line ties it to *WebDriverAgent*,
never when it merely has the shape of a runner:

- an `-xctestrun` path that is the configured one or whose name carries
  `WebDriverAgentRunner`, rather than the `test-without-building` subcommand,
  which says nothing about whose tests are running;
- a go-ios `runwda` whose `--bundleid` is the configured runner bundle, which
  is configurable precisely because a free Apple ID cannot sign
  `com.facebook.*`;
- a go-ios `forward` whose *device* port is 8100, WebDriverAgent's fixed one.

What that conservatively misses is a forked runner with a renamed bundle and a
renamed `.xctestrun`. Missing an orphan costs a timeout and a `pkill`; claiming
a stranger's build costs them their work, so the failure is pointed in the only
direction it can safely point, and `reset` says so when it finds nothing.

One thing this cannot know: whether a process it found is orphaned or in use
right now. Two `ios-mcp serve` instances on one host, or a person driving a
phone while someone else runs `reset`, look identical in `ps`. The only record
of who reserved what lives in `ports._reserved`, which is per-interpreter state
a separate CLI invocation cannot see. Hence the default is to list, and `--yes`
is a deliberate second step.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ios_mcp.config import Settings, get_settings
from ios_mcp.devices.doctor import Check, Status
from ios_mcp.devices.shell import run

#: What launched it, named after the command rather than after the device.
#:
#: Deliberately not "simulator-runner" and "device-runner": `xcodebuild
#: test-without-building` drives a simulator *and* a physical device reached
#: over Wi-Fi (`real_device.py:216`), so naming that kind after the simulator
#: would be wrong half the time and wrong in the half that is harder to debug.
Kind = Literal["xcodebuild", "runwda", "forward"]

#: WebDriverAgent listens on this inside the device, always. The host side is
#: whatever `free_port` handed out, which is why the forward's *second* port is
#: the one that identifies it.
_WDA_DEVICE_PORT = "8100"


@dataclass(frozen=True, slots=True)
class OrphanProcess:
    """One WebDriverAgent-related process, and why it was claimed."""

    pid: int
    kind: Kind
    udid: str | None
    command: str
    #: The anchor that tied this command line to WebDriverAgent. Reported
    #: rather than kept private, because a caller about to kill something is
    #: owed the reason, and because a wrong match is diagnosable only if the
    #: claim is legible.
    matched_on: str


def _value_after(tokens: list[str], flag: str) -> str | None:
    """The argument following `flag`, or the tail of `flag=value`."""
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return None


def _udid_of(tokens: list[str]) -> str | None:
    """Whichever of the two spellings this command uses, if either.

    `xcodebuild` takes `-destination id=<udid>`; go-ios takes `--udid <udid>`
    or `--udid=<udid>`.
    """
    destination = _value_after(tokens, "-destination")
    if destination and destination.startswith("id="):
        return destination.split("=", 1)[1] or None
    return _value_after(tokens, "--udid") or None


def _match_xcodebuild(tokens: list[str], cfg: Settings) -> str | None:
    """Why this `xcodebuild` is ours, or `None` if it is somebody else's.

    The subcommand is not evidence. `xcodebuild test-without-building
    -xctestrun <path> -destination id=<udid>` is exactly how anyone runs a
    prebuilt test bundle, so the claim rests entirely on which bundle.
    """
    if Path(tokens[0]).name != "xcodebuild" or "test-without-building" not in tokens:
        return None

    xctestrun = _value_after(tokens, "-xctestrun")
    if xctestrun is not None:
        configured = cfg.wda.xctestrun_path
        if configured is not None and Path(xctestrun) == Path(configured):
            return f"-xctestrun is the configured bundle, {configured}"
        if "WebDriverAgentRunner" in Path(xctestrun).name:
            return f"-xctestrun names WebDriverAgentRunner: {Path(xctestrun).name}"
        return None

    # The from-source route, which has no xctestrun: it builds and runs the
    # scheme directly (`simulator.py:_launch_from_source`).
    scheme = _value_after(tokens, "-scheme")
    if scheme is not None and scheme.startswith("WebDriverAgentRunner"):
        return f"-scheme is {scheme}"
    return None


def _match_goios(tokens: list[str], cfg: Settings) -> tuple[Kind, str] | None:
    """A go-ios `runwda` or `forward`, anchored on WebDriverAgent's own names."""
    if len(tokens) < 2 or Path(tokens[0]).name != Path(cfg.goios.binary).name:
        return None

    if tokens[1] == "runwda":
        bundle = _value_after(tokens, "--bundleid")
        if bundle == cfg.wda.bundle_id:
            return "runwda", f"--bundleid is the configured runner, {bundle}"
        config = _value_after(tokens, "--xctestconfig") or ""
        if config.startswith("WebDriverAgentRunner"):
            return "runwda", f"--xctestconfig is {config}"
        return None

    if tokens[1] == "forward" and len(tokens) >= 4 and tokens[3] == _WDA_DEVICE_PORT:
        low, high = cfg.wda.port_range
        try:
            host_port = int(tokens[2])
        except ValueError:
            return None
        if low <= host_port <= high:
            return "forward", f"forwards {host_port} to WebDriverAgent's {_WDA_DEVICE_PORT}"
    return None


def _claim(pid: int, command: str, cfg: Settings) -> OrphanProcess | None:
    """One `ps` line, judged. `None` means it belongs to somebody else."""
    tokens = command.split()
    if not tokens:
        return None

    reason = _match_xcodebuild(tokens, cfg)
    if reason is not None:
        return OrphanProcess(pid, "xcodebuild", _udid_of(tokens), command, reason)

    goios = _match_goios(tokens, cfg)
    if goios is not None:
        kind, reason = goios
        return OrphanProcess(pid, kind, _udid_of(tokens), command, reason)
    return None


def parse_orphans(ps_output: str, cfg: Settings, *, udid: str | None = None) -> list[OrphanProcess]:
    """Every claimed process in `ps -Ao pid=,command=` output.

    Pure, so the matcher is testable against captured command lines rather than
    against whatever happens to be running on the machine. Splitting on
    whitespace would mangle a path containing a space, and `ps` gives no
    quoting that would let anything do better; the anchors are all on tokens
    that contain no spaces, so a mangled path costs a missed match rather than
    a wrong one.
    """
    found: list[OrphanProcess] = []
    for line in ps_output.splitlines():
        head, _, command = line.strip().partition(" ")
        if not command.strip():
            continue
        try:
            pid = int(head)
        except ValueError:
            continue
        claimed = _claim(pid, command.strip(), cfg)
        if claimed is None:
            continue
        if udid is not None and claimed.udid != udid:
            continue
        found.append(claimed)
    return found


async def _ps() -> str | None:
    """Every process on the machine, or `None` if `ps` could not be asked.

    `command=` is last on purpose: macOS truncates the command column to the
    terminal width unless it is the final field, and a truncated command line
    loses the `-xctestrun` argument the whole match depends on.
    """
    try:
        result = await run("ps", "-Ao", "pid=,command=", timeout=10.0)
    except Exception:
        return None
    return result.stdout if result.ok else None


async def find_orphans(
    cfg: Settings | None = None, *, udid: str | None = None
) -> list[OrphanProcess] | None:
    """Claimed processes, or `None` when the process table could not be read."""
    settings = cfg or get_settings()
    output = await _ps()
    if output is None:
        return None
    return parse_orphans(output, settings, udid=udid)


async def _command_of(pid: int) -> str | None:
    result = await run("ps", "-o", "command=", "-p", str(pid), timeout=5.0)
    if not result.ok:
        return None
    return result.stdout.strip() or None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # running, and not ours to signal
    return True


async def kill_orphans(
    procs: list[OrphanProcess], cfg: Settings | None = None, *, term_timeout: float = 5.0
) -> list[tuple[OrphanProcess, str]]:
    """SIGTERM, then SIGKILL what survives. Returns each process and its outcome.

    The outcome is a sentence rather than a bool because the interesting cases
    are neither "killed" nor "failed": a process that had already exited, one
    whose pid now belongs to something else, and one that outlives SIGKILL
    because it is a zombie or stuck in device I/O. Each wants saying, and none
    of them is a reason to keep waiting.
    """
    settings = cfg or get_settings()
    outcomes: list[tuple[OrphanProcess, str]] = []
    for proc in procs:
        if not _alive(proc.pid):
            outcomes.append((proc, "already gone"))
            continue
        # A pid is reused the moment it is reaped, and listing and signalling
        # are separate calls. Re-read this one and re-run the same predicate,
        # so the thing being killed is still the thing that was claimed.
        current = await _command_of(proc.pid)
        if current is None or _claim(proc.pid, current, settings) is None:
            outcomes.append((proc, "skipped: the pid now belongs to something else"))
            continue

        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            outcomes.append((proc, "already gone"))
            continue
        except PermissionError:
            outcomes.append((proc, "refused: started by another user"))
            continue

        deadline = asyncio.get_running_loop().time() + term_timeout
        while asyncio.get_running_loop().time() < deadline:
            if not _alive(proc.pid):
                break
            await asyncio.sleep(0.1)
        if not _alive(proc.pid):
            outcomes.append((proc, "stopped"))
            continue

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(proc.pid, signal.SIGKILL)
        await asyncio.sleep(0.2)
        outcomes.append(
            (proc, "stopped (SIGKILL)" if not _alive(proc.pid) else "still present after SIGKILL")
        )
    return outcomes


async def probe_wda_ports(cfg: Settings | None = None) -> list[int]:
    """Host ports in `wda.port_range` answering WebDriverAgent's `/status`.

    Diagnostic only, and never something `--yes` acts on: a port is not a thing
    that can be signalled. It is here because it sees what `ps` cannot, a
    runner reachable at an address with no local process behind it, which is
    what a tunnel or a runner started by hand looks like.
    """
    import httpx

    settings = cfg or get_settings()
    low, high = settings.wda.port_range

    async def answering(port: int) -> int | None:
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                response = await client.get(f"http://{settings.wda.host}:{port}/status")
        except httpx.HTTPError:
            return None
        return port if response.status_code == 200 else None

    results = await asyncio.gather(*(answering(port) for port in range(low, high + 1)))
    return [port for port in results if port is not None]


@dataclass(slots=True)
class ResetReport:
    """What was found, and what happened to it.

    Carries `Check` rather than a shape of its own, so `reset --json` and
    `doctor --json` are one document to a caller. `DoctorReport` itself is not
    reused: `can_use_simulator` and `can_use_real_device` are answers to a
    question reset never asked, and publishing them here would be a claim about
    a machine this command did not inspect.
    """

    checks: list[Check]
    killed: bool

    @property
    def problems(self) -> int:
        return sum(1 for c in self.checks if c.status in ("warn", "fail"))

    @property
    def summary(self) -> str:
        runners = [c for c in self.checks if c.name.startswith("process:")]
        if not runners:
            return "No WebDriverAgent processes found."
        verb = "stopped" if self.killed else "found"
        plural = "" if len(runners) == 1 else "es"
        return f"{len(runners)} WebDriverAgent process{plural} {verb}."

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "checks": [c.to_dict() for c in self.checks]}

    def render(self) -> str:
        icon = {"ok": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
        lines = [self.summary, ""]
        for check in self.checks:
            lines.append(f"  [{icon[check.status]}] {check.name}: {check.detail}")
            if check.remedy and check.status in ("warn", "fail"):
                lines.append(f"          -> {check.remedy}")
        return "\n".join(lines)


#: Said in the `detail`, not the `remedy`, because nothing is wrong: the render
#: shows a remedy only for a problem, and this has to be visible on the happy
#: path. It is the one place the matcher's deliberate blind spot is admitted to
#: someone whose device is still held.
_NOTHING_FOUND = (
    "no WebDriverAgent processes. A runner built from a fork, with both a "
    "renamed bundle id and a renamed .xctestrun, is not claimed on purpose; if "
    'a device is still held, fall back to `pkill -f "test-without-building"`.'
)

_IN_USE_WARNING = (
    "Killing these interrupts any session currently using them. `ps` cannot "
    "tell an orphan from a live session; pass --device to narrow it."
)


async def reset(
    cfg: Settings | None = None, *, udid: str | None = None, kill: bool = False
) -> ResetReport:
    """List, and optionally stop, WebDriverAgent processes on this machine."""
    settings = cfg or get_settings()
    orphans = await find_orphans(settings, udid=udid)

    if orphans is None:
        return ResetReport(
            checks=[
                Check(
                    "processes",
                    "skip",
                    "the process table could not be read",
                    remedy="`ps` is unavailable here. Nothing was inspected and nothing killed.",
                )
            ],
            killed=False,
        )

    checks: list[Check] = []
    outcomes = await kill_orphans(orphans, settings) if kill and orphans else []
    results = {proc.pid: outcome for proc, outcome in outcomes}

    for proc in orphans:
        status: Status
        detail: str
        remedy: str | None
        if not kill:
            status, detail, remedy = "warn", proc.command, "Re-run with --yes to stop it."
        else:
            outcome = results.get(proc.pid, "not attempted")
            stopped = outcome.startswith("stopped") or outcome == "already gone"
            status = "ok" if stopped else "fail"
            detail = f"{outcome}: {proc.command}"
            remedy = None if stopped else f"Still there. Try `kill -9 {proc.pid}` by hand."
        checks.append(
            Check(
                f"process:{proc.kind}:{proc.pid}",
                status,
                detail,
                remedy=remedy,
                data={
                    "pid": proc.pid,
                    "kind": proc.kind,
                    "udid": proc.udid,
                    "matched_on": proc.matched_on,
                },
            )
        )

    if not orphans:
        checks.append(Check("processes", "ok", _NOTHING_FOUND))
    elif not kill:
        checks.append(Check("caution", "warn", "these may be in use", remedy=_IN_USE_WARNING))

    checks.append(await _port_check(settings, orphans))
    return ResetReport(checks=checks, killed=kill)


async def _port_check(cfg: Settings, orphans: list[OrphanProcess]) -> Check:
    """What is answering, and what that can and cannot be attributed to.

    A port cannot be tied to a pid here. A simulator runner takes its host port
    from `USE_PORT` in its environment (`simulator.py:_launch_prebuilt`), which
    `ps` does not print, so the only port that ever appears in a command line is
    a go-ios forward's. That makes this a count rather than an accusation,
    except in the one case it can be sure of: something answering while nothing
    at all was claimed is a runner reset cannot stop.
    """
    answering = await probe_wda_ports(cfg)
    low, high = cfg.wda.port_range
    if not answering:
        return Check("wda-ports", "ok", f"nothing listening in {low}-{high}")

    listed = ", ".join(str(port) for port in answering)
    if orphans:
        return Check(
            "wda-ports",
            "ok",
            f"WebDriverAgent answers on {listed}; a port cannot be tied to a pid",
            data={"answering": answering},
        )
    return Check(
        "wda-ports",
        "warn",
        f"WebDriverAgent answers on {listed} with nothing claimed behind it",
        remedy=(
            "A runner is reachable that this command cannot stop: one behind a tunnel, "
            "on a device farm, or started by hand. Stop it where it was started."
        ),
        data={"answering": answering},
    )
