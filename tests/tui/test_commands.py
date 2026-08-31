"""The slash menu: what it offers, and when.

The matching rules are the interesting part. A command line is not a search
box: what has been typed is the beginning of a name, so a prefix match is the
answer and anything else is a guess about what someone meant.
"""

from __future__ import annotations

import asyncio

import pytest
from ios_tui.app import IosAgentApp
from ios_tui.commands import Command, matching
from ios_tui.devices import DevicePicker
from ios_tui.runner import GoalRunner
from ios_tui.widgets import GoalInput, SlashMenu, StatusBar
from screens import DeviceModel, build_session
from textual.widgets import Input
from tui_harness import ScriptedModel, settings

from ios_mcp.devices.base import DeviceInfo

REGISTRY = [
    Command("device", "switch device", lambda: None),
    Command("screen", "read again", lambda: None),
    Command("save", "write the trail", lambda: None),
    Command("stop", "stop the goal", lambda: None),
]


def test_an_empty_query_offers_everything() -> None:
    """Typing `/` alone is a request to see what there is."""
    assert matching(REGISTRY, "") == REGISTRY


def test_a_prefix_wins_over_a_mere_substring() -> None:
    """`/s` is someone reaching for a command starting with s.

    `device` does not begin with s but contains one, and offering it first
    would make the very first keystroke unpredictable.
    """
    offered = [c.name for c in matching(REGISTRY, "s")]
    assert offered[:3] == ["screen", "save", "stop"]


def test_a_substring_still_matches_when_no_prefix_does() -> None:
    """Half-remembered names are the reason to match loosely at all."""
    assert [c.name for c in matching(REGISTRY, "vic")] == ["device"]


def test_a_query_matching_nothing_offers_nothing() -> None:
    """An empty menu says the name is wrong. A full one says it was ignored."""
    assert matching(REGISTRY, "frobnicate") == []


def test_matching_ignores_case_and_surrounding_space() -> None:
    assert [c.name for c in matching(REGISTRY, " DEV ")] == ["device"]


# -- the menu in the app ---------------------------------------------------


class _Runner(GoalRunner):
    def __init__(self, sink: object) -> None:
        super().__init__(sink, settings(), model=ScriptedModel([]))  # type: ignore[arg-type]

    async def start(self) -> object:
        from ios_tui.events import DeviceReady

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


async def _ready(app: IosAgentApp) -> None:
    async with asyncio.timeout(10):
        while app.query_one(StatusBar).state != "ready":
            await asyncio.sleep(0.02)


@pytest.fixture
def devices(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = [
        DeviceInfo(
            udid="sim",
            name="iPhone 17",
            os_version="26.5",
            kind="simulator",
            state="Booted",
            ready=True,
        )
    ]

    async def fake_list(_settings: object = None) -> list[DeviceInfo]:
        return listed

    async def fake_resolve(self: object, _wanted: str | None) -> DeviceInfo:
        return listed[0]

    monkeypatch.setattr("ios_mcp.devices.discovery.list_devices", fake_list)
    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    monkeypatch.setattr("ios_mcp.devices.pool.DevicePool.resolve", fake_resolve)


async def test_typing_a_slash_opens_the_menu_and_typing_more_filters_it() -> None:
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        menu = app.query_one(SlashMenu)
        assert menu.display is False, "the menu should stay out of the way until asked for"

        box = app.query_one("#goal-input", Input)
        box.value = "/"
        await pilot.pause()
        assert menu.display is True
        assert len(menu.commands) == len(app.commands())

        box.value = "/de"
        await pilot.pause()
        assert [c.name for c in menu.commands] == ["device"]


async def test_the_menu_closes_when_the_slash_goes_away() -> None:
    """A goal is not a command, and the menu must not hover over one."""
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        box = app.query_one("#goal-input", Input)
        menu = app.query_one(SlashMenu)

        box.value = "/dev"
        await pilot.pause()
        assert menu.display is True

        box.value = "turn on bold text"
        await pilot.pause()
        assert menu.display is False


async def test_a_query_matching_nothing_hides_the_menu() -> None:
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        box = app.query_one("#goal-input", Input)

        box.value = "/frobnicate"
        await pilot.pause()
        assert app.query_one(SlashMenu).display is False


async def test_tab_completes_the_name_rather_than_running_it() -> None:
    """Completing and running are different intentions.

    A tab that runs something is a tab that cannot be used to look before
    leaping, which is the whole reason to complete a name.
    """
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        app.query_one("#goal-input", Input).value = "/de"
        await pilot.pause()

        await pilot.press("tab")
        await pilot.pause()

        assert app.query_one("#goal-input", Input).value == "/device"
        assert not isinstance(app.screen, DevicePicker), "tab ran the command"


async def test_the_arrows_move_the_menu_only_while_it_is_open() -> None:
    """Bound unconditionally they would take keys the input needs."""
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        assert app.check_action("menu_down", ()) is False

        app.query_one("#goal-input", Input).value = "/"
        await pilot.pause()
        assert app.check_action("menu_down", ()) is True

        menu = app.query_one(SlashMenu)
        await pilot.press("down")
        await pilot.pause()
        assert menu.highlighted == 1
        assert menu.chosen is not None
        assert menu.chosen.name == app.commands()[1].name


async def test_enter_runs_what_is_highlighted(devices: None) -> None:
    """The menu is filtered by what was typed, so its selection is the more
    specific answer and is the one on screen."""
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        app.query_one("#goal-input", Input).value = "/dev"
        await pilot.pause()

        await pilot.press("enter")
        async with asyncio.timeout(10):
            while not isinstance(app.screen, DevicePicker):
                await asyncio.sleep(0.02)

        assert app.query_one("#goal-input", Input).value == ""
        assert app.query_one(SlashMenu).display is False


def test_the_input_placeholder_does_not_explain_the_commands() -> None:
    """The menu is the explanation. A placeholder carrying instructions is a
    tooltip that never goes away and steals the row from what it is for."""
    assert GoalInput().placeholder == "what should it do?"


async def test_the_palette_offers_the_same_commands() -> None:
    """Two lists would drift, and a command found once is looked for again
    where it was found."""
    from ios_tui.commands import AppCommands

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)):
        await _ready(app)
        provider = AppCommands(app.screen)
        offered = [hit.display async for hit in provider.discover()]

        assert offered == [f"/{c.name}" for c in app.commands()]


# -- copying -----------------------------------------------------------------


async def test_copy_puts_the_whole_transcript_on_the_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Textual app turns on mouse reporting, so the terminal hands a drag to
    the app rather than selecting text. Textual has its own selection and a
    `copy_text` action and binds no key to it, which leaves a log that can be
    highlighted and not copied.

    With no selection the whole transcript is taken, because someone saying the
    logs are not copiable wants the log rather than a rectangle of it.
    """
    copied: list[str] = []
    monkeypatch.setattr(
        "ios_tui.app._to_clipboard", lambda text: copied.append(text) or True
    )

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        app.transcript.goal("turn on bold text")
        await pilot.pause()

        await pilot.press("ctrl+y")
        await pilot.pause()

        assert copied, "nothing reached the clipboard"
        assert "turn on bold text" in copied[0]


async def test_copy_falls_back_when_pbcopy_is_not_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSC 52 cannot be the only route on a macOS-only tool: Textual's own
    docstring says it does not work on macOS Terminal. It is the fallback."""
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: False)
    sent: list[str] = []
    monkeypatch.setattr(IosAgentApp, "copy_to_clipboard", lambda self, text: sent.append(text))

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        app.transcript.goal("turn on bold text")
        await pilot.pause()

        await pilot.press("ctrl+y")
        await pilot.pause()

        assert sent and "turn on bold text" in sent[0]


async def test_copying_an_empty_transcript_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence would read as a copy that worked."""
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: True)

    app = IosAgentApp(_Runner, inline=True)  # inline draws no banner
    async with app.run_test(size=(100, 20)) as pilot:
        await _ready(app)
        # Emptied rather than assumed empty: startup legitimately writes to it,
        # a warning about the model most often, so a fresh app is not a blank
        # one.
        app.transcript.clear()
        await pilot.press("ctrl+y")
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "nothing to copy" in written


def test_copy_is_offered_in_the_command_menu() -> None:
    """Discoverable, since the key is not one anyone would guess."""
    app = IosAgentApp(_Runner)
    assert any(c.name == "copy" for c in app.commands())


async def test_the_wordmark_is_not_copied_with_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nine lines of ASCII phone is not what someone pasting a log wants.

    It is also the one thing on screen carrying no information, which is why
    the copy is rebuilt from the entries rather than scraped off the rendered
    lines.
    """
    copied: list[str] = []
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: copied.append(text) or True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        assert app.transcript.has_banner, "this test needs the banner to be there"
        app.transcript.goal("turn on bold text")
        await pilot.pause()

        await pilot.press("ctrl+y")
        await pilot.pause()

        assert copied
        assert "╭─" not in copied[0]
        assert "turn on bold text" in copied[0]
