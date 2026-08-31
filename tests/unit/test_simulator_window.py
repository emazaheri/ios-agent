"""Showing Simulator.app, and the reasons it is not part of booting.

`simctl boot` starts the runtime, not the window. The device runs headlessly
and nothing appears on the Mac, which is right for CI and wrong for a person
watching an agent drive a phone they cannot see.

Every command here is faked. The point is which commands are issued and in
what order, and a test that actually booted a simulator would take a minute to
tell us that.
"""

from __future__ import annotations

import json

import pytest

from ios_mcp.config import Settings
from ios_mcp.devices.base import DeviceInfo
from ios_mcp.devices.shell import CommandResult
from ios_mcp.devices.simulator import SimulatorAdapter

#: What `simctl list devices --json` says. `_state` parses this, so a bare
#: string here fails in the JSON decoder rather than in the assertion.
_LISTING = json.dumps(
    {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
        {"udid": "SIM-UDID", "name": "iPhone 17", "state": "Shutdown"}
    ]}}
)

SIM = DeviceInfo(
    udid="SIM-UDID",
    name="iPhone 17",
    os_version="26.5",
    kind="simulator",
    state="Shutdown",
    ready=True,
)


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record every command, answering as though each one worked."""
    issued: list[tuple[str, ...]] = []

    async def fake_run(*argv: str, **_kwargs: object) -> CommandResult:
        issued.append(argv)
        return CommandResult(argv=argv, returncode=0, stdout=_LISTING, stderr="")

    monkeypatch.setattr("ios_mcp.devices.simulator.run", fake_run)
    return issued


def _adapter(*, show_window: bool = True) -> SimulatorAdapter:
    settings = Settings()
    settings.simulator.show_window = show_window
    return SimulatorAdapter(SIM, settings)


async def test_booting_a_simulator_also_puts_it_on_screen(
    commands: list[tuple[str, ...]],
) -> None:
    """The gap this closes: a booted simulator nobody can see."""
    await _adapter().ensure_booted()

    assert ("xcrun", "simctl", "boot", "SIM-UDID") in commands
    assert ("open", "-a", "Simulator") in commands


async def test_the_window_is_shown_after_the_boot_has_finished(
    commands: list[tuple[str, ...]],
) -> None:
    """Opening it first would show a window with nothing in it.

    `bootstatus -b` is what waits for the device to finish coming up.
    """
    await _adapter().ensure_booted()

    boot_done = commands.index(("xcrun", "simctl", "bootstatus", "SIM-UDID", "-b"))
    shown = commands.index(("open", "-a", "Simulator"))
    assert shown > boot_done


async def test_an_already_booted_simulator_is_still_shown(
    monkeypatch: pytest.MonkeyPatch, commands: list[tuple[str, ...]]
) -> None:
    """The common case, and the one an early return would miss.

    A simulator left booted by a previous run is exactly the situation where
    the window is closed and the runtime is not, so returning early on
    "already booted" would skip the one thing worth doing.
    """

    async def already_booted(self: SimulatorAdapter) -> str:
        return "Booted"

    monkeypatch.setattr(SimulatorAdapter, "_state", already_booted)
    await _adapter().ensure_booted()

    assert ("open", "-a", "Simulator") in commands
    assert not any("boot" in argv for argv in commands if "bootstatus" not in argv), (
        "a booted simulator was booted again"
    )


async def test_a_headless_run_opens_nothing(commands: list[tuple[str, ...]]) -> None:
    """CI has no screen, and a window there is at best pointless."""
    await _adapter(show_window=False).ensure_booted()

    assert ("xcrun", "simctl", "boot", "SIM-UDID") in commands
    assert not any(argv[0] == "open" for argv in commands)


async def test_a_window_that_will_not_open_does_not_fail_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Presentation is not automation.

    Everything the agent needs goes through `simctl` and WebDriverAgent,
    neither of which cares whether the UI is up. Failing a run because a window
    would not open would trade a working session for a cosmetic one.
    """
    issued: list[tuple[str, ...]] = []

    async def fake_run(*argv: str, **_kwargs: object) -> CommandResult:
        issued.append(argv)
        if argv[0] == "open":
            return CommandResult(argv=argv, returncode=1, stdout="", stderr="no such app")
        return CommandResult(argv=argv, returncode=0, stdout=_LISTING, stderr="")

    monkeypatch.setattr("ios_mcp.devices.simulator.run", fake_run)

    await _adapter().ensure_booted()  # must not raise

    assert ("open", "-a", "Simulator") in issued


def test_showing_the_window_is_on_by_default() -> None:
    """The library's callers are a developer tool and an agent driven by one.

    Someone who wants it headless is running CI and knows to say so; someone
    who wants to watch should not have to find a setting first.
    """
    assert Settings().simulator.show_window is True
