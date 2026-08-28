"""Choosing a device, and the one property that is not cosmetic.

`_best_default` ranks a simulator above a connected phone deliberately: acting
on someone's real device should be a choice rather than whatever was nearest.
A picker that pre-selects the phone hands that property back, because the
fastest thing a person does with a list is press enter.
"""

from __future__ import annotations

import asyncio

import pytest
from ios_tui.app import IosAgentApp
from ios_tui.devices import DevicePicker
from ios_tui.events import ActionFinished, DeviceReady, ScreenUpdated, StatsSnapshot
from ios_tui.runner import GoalRunner
from ios_tui.widgets import ScreenPane, StatsBar, StatusBar
from textual.widgets import Input, OptionList, Static
from tui_harness import ScriptedModel, settings

from ios_mcp.devices.base import DeviceInfo

BOOTED_SIM = DeviceInfo(
    udid="sim-booted", name="iPhone 17", os_version="26.5", kind="simulator",
    state="Booted", ready=True,
)
COLD_SIM = DeviceInfo(
    udid="sim-cold", name="iPhone 17 Pro", os_version="26.5", kind="simulator",
    state="Shutdown", ready=True,
)
PHONE = DeviceInfo(
    udid="phone", name="Ehsan's iPhone", os_version="26.6", kind="device",
    state="connected", ready=True,
)
BLOCKED = DeviceInfo(
    udid="blocked", name="Old iPhone", os_version="17.0", kind="device",
    state="connected", ready=False,
    blockers=("Developer Mode is off", "no RemoteXPC tunnel"),
)


@pytest.fixture
def devices(monkeypatch: pytest.MonkeyPatch) -> list[DeviceInfo]:
    """A device list, with the phone first so ordering cannot flatter us."""
    listed = [PHONE, BLOCKED, BOOTED_SIM, COLD_SIM]

    async def fake_list(_settings: object = None) -> list[DeviceInfo]:
        return listed

    async def fake_resolve(self: object, _wanted: str | None) -> DeviceInfo:
        # What the pool would choose unattended.
        return BOOTED_SIM

    # Patched in both modules. `pool.py` binds `list_devices` at import time,
    # so patching only the discovery module leaves the pool calling the real
    # one, which shells out to simctl.
    monkeypatch.setattr("ios_mcp.devices.discovery.list_devices", fake_list)
    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    monkeypatch.setattr("ios_mcp.devices.pool.DevicePool.resolve", fake_resolve)
    return listed


def _app() -> IosAgentApp:
    return IosAgentApp(lambda sink: GoalRunner(sink, settings(), model=ScriptedModel([])))


async def _settle(app: IosAgentApp, until: object, timeout: float = 10.0) -> None:
    assert callable(until)
    async with asyncio.timeout(timeout):
        while not until():
            await asyncio.sleep(0.02)


async def _picker(app: IosAgentApp) -> DevicePicker:
    screen = DevicePicker(settings())
    app.push_screen(screen)
    async with asyncio.timeout(10):
        while not screen.devices:
            await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)
    return screen


async def test_the_cursor_never_starts_on_a_real_phone(devices: list[DeviceInfo]) -> None:
    """The safety property, and the reason this screen exists at all.

    The phone is first in the list here on purpose: if the cursor simply sat at
    index zero it would land on it, and pressing enter would automate someone's
    actual device without a decision being made.
    """
    app = _app()
    async with app.run_test(size=(100, 30)) as pilot:
        picker = await _picker(app)
        await pilot.pause()

        listing = picker.query_one("#picker-list", OptionList)
        assert listing.highlighted is not None
        chosen = picker.devices[listing.highlighted]
        assert chosen.kind == "simulator"
        assert chosen.udid == "sim-booted", "the cursor drifted from the pool's own ranking"


async def test_a_device_that_cannot_be_driven_is_listed_with_its_blockers(
    devices: list[DeviceInfo],
) -> None:
    """Hiding an unusable device hides the reason it is unusable.

    `blockers` is the diagnostic, and a person looking for a phone that is not
    in the list is a person with no way to find out why.
    """
    app = _app()
    async with app.run_test(size=(100, 30)) as pilot:
        picker = await _picker(app)
        await pilot.pause()

        listing = picker.query_one("#picker-list", OptionList)
        assert listing.option_count == len(devices), "an unusable device was dropped"

        blocked = listing.get_option("blocked")
        assert blocked.disabled, "an unusable device could still be chosen"
        assert "Developer Mode is off" in str(blocked.prompt)


async def test_choosing_returns_the_udid(devices: list[DeviceInfo]) -> None:
    app = _app()
    async with app.run_test(size=(100, 30)) as pilot:
        answer: list[str | None] = []
        picker = DevicePicker(settings())
        app.push_screen(picker, answer.append)
        async with asyncio.timeout(10):
            while not picker.devices:
                await asyncio.sleep(0.02)
        await pilot.pause()

        listing = picker.query_one("#picker-list", OptionList)
        listing.highlighted = next(i for i, d in enumerate(devices) if d.udid == "phone")
        await pilot.press("enter")
        async with asyncio.timeout(10):
            while not answer:
                await asyncio.sleep(0.02)

        assert answer == ["phone"], "choosing a phone deliberately must still work"


async def test_escape_declines_rather_than_choosing_something(
    devices: list[DeviceInfo],
) -> None:
    """Backing out is not the same as accepting the highlighted row."""
    app = _app()
    async with app.run_test(size=(100, 30)) as pilot:
        answer: list[str | None] = []
        picker = DevicePicker(settings())
        app.push_screen(picker, answer.append)
        async with asyncio.timeout(10):
            while not picker.devices:
                await asyncio.sleep(0.02)
        await pilot.pause()

        await pilot.press("escape")
        async with asyncio.timeout(10):
            while not answer:
                await asyncio.sleep(0.02)

        assert answer == [None]


async def test_an_empty_list_says_what_to_run_next(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty screen is an invitation to act, not a dead end."""

    async def nothing(_settings: object = None) -> list[DeviceInfo]:
        return []

    monkeypatch.setattr("ios_mcp.devices.discovery.list_devices", nothing)
    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", nothing)

    app = _app()
    async with app.run_test(size=(100, 30)) as pilot:
        picker = DevicePicker(settings())
        app.push_screen(picker)
        await pilot.pause()
        await asyncio.sleep(0.3)

        status = str(picker.query_one("#picker-status", Static).content)
        assert "doctor" in status, "the empty state should name the command that explains it"


# -- switching device from inside the running app --------------------------


class _SwitchableRunner(GoalRunner):
    """Records what it was asked to switch to, without touching a device."""

    def __init__(self, sink: object) -> None:
        super().__init__(sink, settings(), model=ScriptedModel([]))  # type: ignore[arg-type]
        self.switched_to: list[str] = []
        self.closed = 0

    async def start(self) -> object:
        from screens import DeviceModel, build_session

        session, _, _ = build_session(DeviceModel(), settings())
        self.session = session
        self.sink.emit(
            DeviceReady(
                lease={
                    "device": {
                        "name": self.device or "iPhone 17",
                        "os_version": "26.5",
                        "kind": "simulator",
                    }
                }
            )
        )
        return session

    async def switch(self, device: str) -> object:
        self.switched_to.append(device)
        self.closed += 1
        self.device = device
        self._last_screen = ""
        return await self.start()

    async def close(self) -> None:
        self.closed += 1


def _switchable() -> IosAgentApp:
    return IosAgentApp(_SwitchableRunner)


async def test_the_shortcut_switches_device_without_leaving_the_app(
    devices: list[DeviceInfo],
) -> None:
    """The point of the binding: no restart, no flag, no remembering a name."""
    app = _switchable()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        await pilot.press("ctrl+o")
        await _settle(app, lambda: isinstance(app.screen, DevicePicker))
        picker = app.screen
        assert isinstance(picker, DevicePicker)
        await _settle(app, lambda: bool(picker.devices))

        listing = picker.query_one("#picker-list", OptionList)
        listing.highlighted = next(i for i, d in enumerate(devices) if d.udid == "sim-cold")
        await pilot.press("enter")

        runner = app.runner
        assert isinstance(runner, _SwitchableRunner)
        await _settle(app, lambda: bool(runner.switched_to))
        assert runner.switched_to == ["sim-cold"]


async def test_switching_clears_what_belonged_to_the_old_device(
    devices: list[DeviceInfo],
) -> None:
    """The screen and the counters describe a device, not the app.

    Carrying either across a switch means the pane shows one phone's screen
    while the header names another, which is the exact confusion the currency
    strip exists to prevent.
    """
    app = _switchable()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app._apply(ScreenUpdated(text='screen: com.apple.Preferences / "Settings"'))
        app._apply(
            ActionFinished(verb="tap", args={"target": "Wi-Fi"}, stats=StatsSnapshot(actions=4))
        )
        await pilot.pause()
        assert app.query_one(ScreenPane).text
        assert app.query_one(StatsBar).stats.actions == 4

        await pilot.press("ctrl+o")
        await _settle(app, lambda: isinstance(app.screen, DevicePicker))
        picker = app.screen
        assert isinstance(picker, DevicePicker)
        await _settle(app, lambda: bool(picker.devices))
        picker.query_one("#picker-list", OptionList).highlighted = next(
            i for i, d in enumerate(devices) if d.udid == "sim-cold"
        )
        await pilot.press("enter")

        runner = app.runner
        assert isinstance(runner, _SwitchableRunner)
        await _settle(app, lambda: bool(runner.switched_to))
        await pilot.pause()

        assert app.query_one(ScreenPane).text == "", "the old device's screen survived"
        assert app.query_one(StatsBar).stats.actions == 0, "the old device's counters survived"


async def test_switching_is_refused_while_a_goal_is_running(
    devices: list[DeviceInfo],
) -> None:
    """Taking the device away mid-action leaves the phone somewhere unrecorded."""
    app = _switchable()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        app._busy = True  # a run is in flight

        await pilot.press("ctrl+o")
        await pilot.pause()

        assert not isinstance(app.screen, DevicePicker), "the picker opened during a run"
        written = "\n".join(line.text for line in app.transcript.lines)
        assert "stop the current run first" in written


async def test_slash_device_opens_the_picker(devices: list[DeviceInfo]) -> None:
    """The discoverable form, and the one the shortcut exists to shortcut.

    `ctrl+d` would have been the obvious key and is unusable: `Input` binds it
    to `delete_right`, so with the goal box focused it deletes a character.
    A typed command cannot collide with a widget's keymap.
    """
    app = _switchable()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        goal_input = app.query_one("#goal-input", Input)
        goal_input.value = "/device"
        await pilot.press("enter")

        await _settle(app, lambda: isinstance(app.screen, DevicePicker))
        assert goal_input.value == "", "the command was left in the box"


async def test_a_slash_command_is_never_mistaken_for_a_goal(
    devices: list[DeviceInfo],
) -> None:
    """A goal is a sentence about a phone; a command instructs the front end.

    The slash is what tells them apart, so someone whose goal genuinely is
    "device settings" can still ask for it.
    """
    app = _switchable()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app.query_one("#goal-input", Input).value = "device settings"
        await pilot.press("enter")
        await pilot.pause()

        assert not isinstance(app.screen, DevicePicker), "a goal was read as a command"


async def test_an_unknown_command_points_at_the_menu(devices: list[DeviceInfo]) -> None:
    """The menu is the listing, so the message names it rather than repeating it."""
    app = _switchable()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app.query_one("#goal-input", Input).value = "/frobnicate"
        await pilot.press("enter")
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "no command matching" in written
        assert "Type /" in written
