"""The first run on a machine that is not set up yet.

Without this check the app spends a minute acquiring a device and then fails
with whatever the first missing tool happened to raise: one error, from one
layer, describing one symptom of a setup that was never done. A person with no
Xcode should be told they have no Xcode.

Every report here is fabricated, which is the only way to test the cases worth
testing. A machine that can run this suite has Xcode on it.
"""

from __future__ import annotations

import asyncio

import pytest
from ios_tui.app import IosAgentApp
from ios_tui.events import DeviceReady
from ios_tui.runner import GoalRunner
from ios_tui.widgets import StatusBar
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings

from ios_mcp.devices.doctor import Check, DoctorReport

NO_XCODE = [
    Check("host", "ok", "macOS 26.6 on arm64"),
    Check(
        "xcode",
        "fail",
        "Xcode is not installed",
        remedy="Install Xcode from the App Store, then run `xcode-select --switch`.",
    ),
    Check("simctl", "fail", "xcrun simctl is unavailable", remedy="Install the full Xcode."),
]

NO_WDA = [
    Check("host", "ok", "macOS 26.6 on arm64"),
    Check("xcode", "ok", "Xcode 26.6"),
    Check("simctl", "ok", "xcrun simctl available"),
    Check(
        "wda-bundle",
        "warn",
        "no WebDriverAgent build found",
        remedy="Run `scripts/prepare_wda.sh simulator` to build one into vendor/wda/.",
    ),
]

#: Everything works, but two things are worth mentioning. Neither prevents a
#: run: a stopped tunnel only matters for USB, and a profile with two days left
#: is a profile that still works today.
HEALTHY_WITH_WARNINGS = [
    Check("xcode", "ok", "Xcode 26.6"),
    Check("simctl", "ok", "xcrun simctl available"),
    Check(
        "wda-bundle",
        "warn",
        "profile expires in 2 day(s)",
        data={"xctestrun": "/f.xctestrun"},
        remedy="Re-sign soon with scripts/prepare_wda.sh device.",
    ),
    Check(
        "tunnel",
        "warn",
        "no RemoteXPC tunnel is running",
        remedy="Run `sudo ios tunnel start`.",
    ),
]


class _Runner(GoalRunner):
    """Records whether a device was ever asked for."""

    def __init__(self, sink: object) -> None:
        super().__init__(sink, settings(), model=ScriptedModel([]))  # type: ignore[arg-type]
        self.started = 0

    async def start(self) -> object:
        self.started += 1
        session, _, _ = build_session(DeviceModel(), settings())
        self.session = session
        self.sink.emit(
            DeviceReady(
                lease={"device": {"name": "iPhone 17", "os_version": "26.5", "kind": "simulator"}}
            )
        )
        return session

    async def close(self) -> None:
        return None


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> object:
    """Replace the autouse healthy machine with one of the above."""

    def use(checks: list[Check]) -> None:
        async def report(_settings: object = None) -> DoctorReport:
            return DoctorReport(checks=list(checks))

        monkeypatch.setattr("ios_mcp.devices.doctor.run_doctor", report)

    return use


async def _settle(app: IosAgentApp, until: object, timeout: float = 10.0) -> None:
    assert callable(until)
    async with asyncio.timeout(timeout):
        while not until():
            await asyncio.sleep(0.02)


async def test_a_machine_without_xcode_is_told_so_before_anything_else(machine: object) -> None:
    """The first-run case, and the one the old path handled worst.

    It would have acquired a device first and reported whichever tool happened
    to be missing at the moment it was reached.
    """
    assert callable(machine)
    machine(NO_XCODE)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(110, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "failed")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 0, "a device was acquired on a machine that cannot drive one"

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "not set up" in written
        assert "Xcode is not installed" in written
        assert "App Store" in written, "the remedy was not shown"
        assert "ios-agent doctor" in written


async def test_a_missing_webdriveragent_stops_the_run_too(machine: object) -> None:
    """It is a `warn`, and still means nothing can be automated.

    Blocking on failures alone would let this through, and the run would get as
    far as launching a runner that does not exist.
    """
    assert callable(machine)
    machine(NO_WDA)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(110, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "failed")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 0

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "prepare_wda.sh" in written


async def test_warnings_are_mentioned_and_do_not_stop_the_run(machine: object) -> None:
    """A stopped tunnel only matters over USB, and a profile with two days left
    still works today. Refusing to start on either would ground a working
    machine."""
    assert callable(machine)
    machine(HEALTHY_WITH_WARNINGS)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(110, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 1, "a warning stopped a machine that works"

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "expires in 2 day(s)" in written
        assert "tunnel" in written


async def test_a_check_that_cannot_run_does_not_ground_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The diagnostic failing is not the same as the machine being unusable.

    Refusing to start because a check crashed would turn a bug in the checker
    into a bug in the tool.
    """

    async def broken(_settings: object = None) -> DoctorReport:
        raise OSError("xcrun went missing mid-check")

    monkeypatch.setattr("ios_mcp.devices.doctor.run_doctor", broken)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(110, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 1

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "could not check the toolchain" in written


# -- the report itself -----------------------------------------------------


def test_a_device_signing_warning_does_not_make_the_simulator_unusable() -> None:
    """One check covers two artifacts and its status is the worse of them.

    A device provisioning profile with two days left made `wda-bundle` `warn`,
    which said the simulator could not be used, on a machine whose simulator
    was working. Found by wiring this check into startup and watching it refuse
    to start.
    """
    report = DoctorReport(
        checks=[
            Check("xcode", "ok", "Xcode 26.6"),
            Check("simctl", "ok", "available"),
            Check(
                "wda-bundle",
                "warn",
                "the device runner's provisioning profile expires in 0 day(s)",
                data={"xctestrun": "/f.xctestrun", "runner_app": "/f.app"},
            ),
        ]
    )

    assert report.can_use_simulator is True


def test_no_simulator_bundle_means_no_simulator() -> None:
    """The other side of it: an `ok` status with no `xctestrun` is a device-only
    build, which cannot drive a simulator however healthy it looks."""
    report = DoctorReport(
        checks=[
            Check("xcode", "ok", "Xcode 26.6"),
            Check("simctl", "ok", "available"),
            Check("wda-bundle", "ok", "device runner ready", data={"runner_app": "/f.app"}),
        ]
    )

    assert report.can_use_simulator is False


async def test_the_blocker_is_listed_before_the_irrelevant_warning(machine: object) -> None:
    """A stopped RemoteXPC tunnel led the list, and it was not what was wrong.

    It matters only for driving a physical phone over USB. The missing
    WebDriverAgent build is what stops a simulator, which is the path that
    needs no Apple Developer account and the one a first run will take.
    """
    assert callable(machine)
    machine(
        [
            Check("xcode", "ok", "Xcode 26.6"),
            Check("simctl", "ok", "xcrun simctl available"),
            Check("tunnel", "warn", "no RemoteXPC tunnel is running", remedy="sudo ios tunnel"),
            Check(
                "wda-bundle",
                "warn",
                "no WebDriverAgent build found",
                remedy="Run `scripts/prepare_wda.sh simulator`.",
            ),
        ]
    )

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(110, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "failed")
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert written.index("wda-bundle") < written.index("tunnel"), (
            "the list led with a warning that was not blocking anything"
        )


async def test_a_hard_failure_outranks_everything(machine: object) -> None:
    """A `fail` is a fact; a `warn` is a maybe."""
    assert callable(machine)
    machine(
        [
            Check("wda-bundle", "warn", "no WebDriverAgent build found", remedy="prepare_wda"),
            Check("xcode", "fail", "Xcode is not installed", remedy="App Store"),
        ]
    )

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(110, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "failed")
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "Xcode is not installed" in written
