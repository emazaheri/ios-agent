"""The matcher behind `ios-mcp reset`.

The positives are the easy half. The file exists for the negatives: a
developer's own `xcodebuild test-without-building` shares its command line with
ours down to the flag order, and the only thing telling them apart is which
`.xctestrun` is named. Killing a stranger's build is the one failure this
command must never have, so those cases are written first and asserted hardest.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from ios_mcp.config import Settings
from ios_mcp.devices.processes import (
    OrphanProcess,
    ResetReport,
    kill_orphans,
    parse_orphans,
)

XCODE = "/Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild"
WDA_XCTESTRUN = "/repo/vendor/wda/WebDriverAgentRunner_iphonesimulator26.0-arm64.xctestrun"
MINE = "/Users/someone/Build/Products/MyAppUITests_iphonesimulator26.0-arm64.xctestrun"

OURS_SIMULATOR = f"{XCODE} test-without-building -xctestrun {WDA_XCTESTRUN} -destination id=SIM-1"
OURS_FROM_SOURCE = (
    f"{XCODE} -project vendor/wda/WebDriverAgent/WebDriverAgent.xcodeproj "
    "-scheme WebDriverAgentRunner -destination id=SIM-1 test-without-building"
)
OURS_RUNWDA = (
    "/opt/homebrew/bin/ios runwda "
    "--bundleid=com.facebook.WebDriverAgentRunner.xctrunner "
    "--testrunnerbundleid=com.facebook.WebDriverAgentRunner.xctrunner "
    "--xctestconfig=WebDriverAgentRunner.xctest --udid=PHONE-9"
)
OURS_FORWARD = "/opt/homebrew/bin/ios forward 8101 8100 --udid PHONE-9"


def _ps(*commands: str) -> str:
    """`ps -Ao pid=,command=` output, with the leading column padding it has."""
    return "".join(f"{pid:>6} {command}\n" for pid, command in enumerate(commands, start=1001))


def _found(*commands: str, cfg: Settings | None = None) -> list[OrphanProcess]:
    return parse_orphans(_ps(*commands), cfg or Settings())


# -- the negatives, which are the point -------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # Identical in shape to ours. Only the bundle differs, and that is the
        # entire safety argument.
        f"{XCODE} test-without-building -xctestrun {MINE} -destination id=SIM-1",
        f"{XCODE} build -scheme MyApp -destination id=SIM-1",
        f"{XCODE} test -scheme MyAppUITests",
        # go-ios doing something else, and a forward to a port that is not WDA's.
        "/opt/homebrew/bin/ios forward 9000 9001 --udid PHONE-9",
        "/opt/homebrew/bin/ios listen",
        # The recorder `scripts/record_demo.sh` starts, which must survive a
        # reset: it needs SIGINT to finalise its container, and it is not ours
        # to stop anyway.
        "xcrun simctl io SIM-1 recordVideo --codec h264 --force /repo/.artifacts/sim.mp4",
        # Something whose command line merely mentions the name.
        "grep -r WebDriverAgentRunner /repo",
    ],
)
def test_someone_elses_process_is_never_claimed(command: str) -> None:
    assert _found(command) == []


def test_a_renamed_runner_is_missed_rather_than_guessed() -> None:
    """The cost of the conservative rule, asserted so it stays deliberate.

    A fork with its own bundle id and its own `.xctestrun` name is invisible
    here. That is the failure direction chosen on purpose, and the report says
    so when it finds nothing.
    """
    forked = f"{XCODE} test-without-building -xctestrun /repo/MyWDA.xctestrun -destination id=SIM-1"
    assert _found(forked) == []


# -- the positives ----------------------------------------------------------


def test_the_prebuilt_simulator_runner_is_claimed_by_its_bundle_name() -> None:
    (found,) = _found(OURS_SIMULATOR)
    assert (found.pid, found.kind, found.udid) == (1001, "xcodebuild", "SIM-1")
    assert "WebDriverAgentRunner" in found.matched_on


def test_a_configured_xctestrun_is_claimed_even_when_it_is_named_nothing_like_it() -> None:
    """`wda.xctestrun_path` is the operator saying which bundle is ours."""
    cfg = Settings()
    cfg.wda.xctestrun_path = Path("/elsewhere/Private.xctestrun")
    command = f"{XCODE} test-without-building -xctestrun /elsewhere/Private.xctestrun"
    (found,) = _found(command, cfg=cfg)
    assert "configured bundle" in found.matched_on


def test_the_from_source_route_is_claimed_by_its_scheme() -> None:
    """It has no `-xctestrun` at all, so the scheme is the only anchor."""
    (found,) = _found(OURS_FROM_SOURCE)
    assert found.kind == "xcodebuild"
    assert found.matched_on == "-scheme is WebDriverAgentRunner"


def test_runwda_is_claimed_by_the_configured_bundle_id() -> None:
    (found,) = _found(OURS_RUNWDA)
    assert (found.kind, found.udid) == ("runwda", "PHONE-9")


def test_a_runwda_for_a_different_bundle_is_still_claimed_by_its_xctestconfig() -> None:
    """A free Apple ID cannot sign `com.facebook.*`, so the bundle id moves."""
    command = OURS_RUNWDA.replace(
        "--bundleid=com.facebook.WebDriverAgentRunner.xctrunner", "--bundleid=com.me.wda.xctrunner"
    )
    (found,) = _found(command)
    assert found.matched_on == "--xctestconfig is WebDriverAgentRunner.xctest"


def test_the_forward_is_claimed_by_the_device_side_port() -> None:
    (found,) = _found(OURS_FORWARD)
    assert (found.kind, found.udid) == ("forward", "PHONE-9")


def test_a_forward_outside_the_configured_range_is_not_ours() -> None:
    cfg = Settings()
    cfg.wda.port_range = (8100, 8110)
    assert _found("/opt/homebrew/bin/ios forward 8500 8100 --udid PHONE-9", cfg=cfg) == []


def test_the_device_filter_narrows_without_changing_what_matches() -> None:
    output = _ps(OURS_SIMULATOR, OURS_RUNWDA, OURS_FORWARD)
    assert len(parse_orphans(output, Settings())) == 3
    narrowed = parse_orphans(output, Settings(), udid="PHONE-9")
    assert [p.kind for p in narrowed] == ["runwda", "forward"]


def test_junk_lines_are_skipped_rather_than_raising() -> None:
    assert parse_orphans("\n\nnot-a-pid something\n  \n", Settings()) == []


# -- killing ----------------------------------------------------------------


def _proc(pid: int = 4242) -> OrphanProcess:
    return OrphanProcess(pid, "xcodebuild", "SIM-1", OURS_SIMULATOR, "test")


@pytest.fixture
def signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Every `os.kill` this module makes, without any of them landing."""
    sent: list[tuple[int, int]] = []

    def record(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    monkeypatch.setattr(os, "kill", record)
    return sent


async def test_a_process_that_stops_on_sigterm_is_never_sigkilled(
    monkeypatch: pytest.MonkeyPatch, signals: list[tuple[int, int]]
) -> None:
    alive = iter([True, False, False, False])

    def kill(pid: int, sig: int) -> None:
        if sig == 0 and not next(alive, False):
            raise ProcessLookupError
        signals.append((pid, sig))

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr("ios_mcp.devices.processes._command_of", _returning(OURS_SIMULATOR))

    [(_, outcome)] = await kill_orphans([_proc()])
    assert outcome == "stopped"
    assert signal.SIGKILL not in [sig for _, sig in signals]


async def test_a_process_that_outlives_sigkill_is_reported_rather_than_waited_on(
    monkeypatch: pytest.MonkeyPatch, signals: list[tuple[int, int]]
) -> None:
    """A zombie, or an `xcodebuild` stuck on device I/O. `--yes` must return."""
    monkeypatch.setattr("ios_mcp.devices.processes._command_of", _returning(OURS_SIMULATOR))
    [(_, outcome)] = await kill_orphans([_proc()], term_timeout=0.2)
    assert outcome == "still present after SIGKILL"
    assert (4242, signal.SIGTERM) in signals
    assert (4242, signal.SIGKILL) in signals


async def test_an_already_gone_process_is_a_success_with_no_signal_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", kill)
    [(_, outcome)] = await kill_orphans([_proc()])
    assert outcome == "already gone"
    assert [sig for _, sig in sent] == [0]


async def test_a_pid_that_now_belongs_to_something_else_is_not_signalled(
    monkeypatch: pytest.MonkeyPatch, signals: list[tuple[int, int]]
) -> None:
    """Listing and signalling are separate calls, and a pid is reused the
    moment it is reaped. The claim is re-made against the live command line."""
    monkeypatch.setattr(
        "ios_mcp.devices.processes._command_of", _returning("/usr/bin/vim notes.txt")
    )
    [(_, outcome)] = await kill_orphans([_proc()])
    assert "belongs to something else" in outcome
    assert [sig for _, sig in signals if sig != 0] == []


def _returning(command: str | None):
    async def _command_of(_pid: int) -> str | None:
        return command

    return _command_of


# -- the report -------------------------------------------------------------


def test_the_report_reads_like_a_doctor_report() -> None:
    """`reset --json` and `doctor --json` are one document to a caller."""
    from ios_mcp.devices.doctor import Check

    report = ResetReport(
        checks=[Check("process:xcodebuild:1", "warn", "detail", remedy="do it")], killed=False
    )
    payload = report.to_dict()
    assert set(payload) == {"summary", "checks"}
    assert payload["checks"][0] == {
        "check": "process:xcodebuild:1",
        "status": "warn",
        "detail": "detail",
        "remedy": "do it",
    }
    assert report.summary == "1 WebDriverAgent process found."
    assert "do it" in report.render()


def test_a_report_with_nothing_found_says_so() -> None:
    assert ResetReport(checks=[], killed=False).summary.startswith("No WebDriverAgent")


# -- the command end to end -------------------------------------------------


async def test_listing_warns_without_touching_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default is a report, and the report says these may be in use."""
    from ios_mcp.devices import processes

    monkeypatch.setattr(processes, "_ps", _ps_returning(_ps(OURS_SIMULATOR, OURS_FORWARD)))
    monkeypatch.setattr(processes, "probe_wda_ports", _returning_list([]))
    monkeypatch.setattr(os, "kill", _must_not_be_called)

    report = await processes.reset(Settings())
    assert not report.killed
    assert report.summary == "2 WebDriverAgent processes found."
    names = [c.name for c in report.checks]
    assert "process:xcodebuild:1001" in names
    assert "caution" in names
    assert "in use" in report.render()


async def test_a_port_answering_with_no_process_behind_it_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one thing `ps` cannot see: a runner reached over a tunnel, or one
    someone started by hand."""
    from ios_mcp.devices import processes

    monkeypatch.setattr(processes, "_ps", _ps_returning(""))
    monkeypatch.setattr(processes, "probe_wda_ports", _returning_list([8104]))

    report = await processes.reset(Settings())
    ports = next(c for c in report.checks if c.name == "wda-ports")
    assert ports.status == "warn"
    assert "8104" in ports.detail


async def test_an_unreadable_process_table_is_a_skip_rather_than_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ios_mcp.devices import processes

    monkeypatch.setattr(processes, "_ps", _ps_returning(None))
    monkeypatch.setattr(os, "kill", _must_not_be_called)

    report = await processes.reset(Settings(), kill=True)
    assert [c.status for c in report.checks] == ["skip"]
    assert not report.killed


def _ps_returning(output: str | None):
    async def _ps_stub() -> str | None:
        return output

    return _ps_stub


def _returning_list(ports: list[int]):
    async def _probe(_cfg: object = None) -> list[int]:
        return ports

    return _probe


def _must_not_be_called(*_args: object) -> None:
    raise AssertionError("reset signalled something while only listing")


# -- the CLI ----------------------------------------------------------------


def test_the_cli_exits_zero_on_a_clear_machine_and_one_while_a_device_is_held(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exit code is what makes `ios-mcp reset && <run>` mean something.

    Listing a process is a failure, because the device is still held. Finding
    none is a pass. That is the only contract a script can hang off.
    """
    import json

    from ios_mcp import __main__ as cli
    from ios_mcp.devices import processes

    monkeypatch.setattr(processes, "probe_wda_ports", _returning_list([]))

    monkeypatch.setattr(processes, "_ps", _ps_returning(""))
    assert cli.main(["reset", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"].startswith("No WebDriverAgent")

    monkeypatch.setattr(processes, "_ps", _ps_returning(_ps(OURS_SIMULATOR)))
    assert cli.main(["reset"]) == 1
    assert "Re-run with --yes" in capsys.readouterr().out


async def test_a_port_with_a_process_beside_it_is_not_an_accusation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A simulator runner's port comes from `USE_PORT`, not from its argv.

    So a port can never be tied to a pid, and reporting an answering port as
    unexplained while its own runner is listed two lines above would be the
    command contradicting itself.
    """
    from ios_mcp.devices import processes

    monkeypatch.setattr(processes, "_ps", _ps_returning(_ps(OURS_SIMULATOR)))
    monkeypatch.setattr(processes, "probe_wda_ports", _returning_list([8100]))

    report = await processes.reset(Settings())
    ports = next(c for c in report.checks if c.name == "wda-ports")
    assert ports.status == "ok"
    assert "cannot be tied to a pid" in ports.detail
