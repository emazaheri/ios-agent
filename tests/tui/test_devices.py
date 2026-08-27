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
from ios_tui.runner import GoalRunner
from textual.widgets import OptionList, Static
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
