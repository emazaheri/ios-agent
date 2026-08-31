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
from ios_tui.events import StatsSnapshot
from ios_tui.runner import GoalRunner
from ios_tui.widgets import GoalInput, SlashMenu, StatsBar, StatusBar
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
#
# Selecting is the copy. A Textual app turns on mouse reporting, so a drag goes
# to the app and the terminal's own select-and-copy never happens; Textual makes
# its own selection and stops there, which leaves text that can be highlighted
# and not copied.


async def _drag(app: IosAgentApp, pilot: object, row: int, x1: int, x2: int) -> None:
    """A real drag across one row of the transcript.

    Driven through `Screen._forward_event`, which is where Textual's selection
    logic lives. `post_message` reaches the widget's `on_mouse_down` handlers
    instead and bypasses selection entirely, so a test written that way says
    nothing about whether a drag selects.
    """
    from textual import events

    assert hasattr(pilot, "pause")
    transcript = app.transcript
    y = transcript.region.y + row - transcript.scroll_offset.y

    def at(kind: type, x: int, button: int) -> events.MouseEvent:
        return kind(None, x, y, 0, 0, button, False, False, False, screen_x=x, screen_y=y)

    app.screen._forward_event(at(events.MouseDown, x1, 1))
    await pilot.pause()  # type: ignore[attr-defined]
    app.screen._forward_event(at(events.MouseMove, x2, 1))
    await pilot.pause()  # type: ignore[attr-defined]
    app.screen._forward_event(at(events.MouseUp, x2, 0))
    for _ in range(3):
        await pilot.pause()  # type: ignore[attr-defined]


async def _selected(app: IosAgentApp, text: str | None) -> None:
    """Report a selection of exactly `text`, for the cases a drag cannot make.

    A drag of nothing, or of one character, is hard to place precisely with
    synthetic coordinates; what those cases test is the threshold, not the
    selection machinery, which `_drag` covers.
    """
    from textual import events

    app.screen.get_selected_text = lambda: text  # type: ignore[method-assign]
    app.post_message(events.TextSelected())


async def test_a_drag_selects_and_copies_without_being_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole feature, through Textual's real selection."""
    copied: list[str] = []
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: copied.append(text) or True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(120, 36)) as pilot:
        await _ready(app)
        app.transcript.note("a line worth selecting")
        await pilot.pause()

        row = next(
            i for i, line in enumerate(app.transcript.lines) if "worth selecting" in line.text
        )
        await _drag(app, pilot, row, app.transcript.region.x + 2, app.transcript.region.x + 24)

        assert copied, "a real drag copied nothing"
        assert "worth selecting" in copied[0]
        assert app.query_one(StatsBar).notice.startswith("copied ")


async def test_a_drag_copies_the_row_it_crossed_and_not_the_pane() -> None:
    """The line dragged over, not everything above it.

    Without the offset metadata `SelectableLog.render_line` stamps, the
    compositor cannot say which character is under the pointer, and the
    selection degrades to whole widgets: the drag comes back with the wordmark
    and every line since. Copying nine lines of ASCII phone because someone
    swiped one row is worse than copying nothing.
    """
    app = IosAgentApp(_Runner)
    async with app.run_test(size=(120, 36)) as pilot:
        await _ready(app)
        app.transcript.note("the only row that should be copied")
        await pilot.pause()

        row = next(
            i for i, line in enumerate(app.transcript.lines) if "should be copied" in line.text
        )
        await _drag(app, pilot, row, app.transcript.region.x + 1, app.transcript.region.x + 40)

        selected = app.screen.get_selected_text()
        assert selected is not None
        assert "should be copied" in selected
        assert "\n" not in selected.strip()
        assert "ios-agent" not in selected


async def test_a_drag_in_progress_is_visible() -> None:
    """A selection you cannot see is a gesture that looks like it failed.

    Textual highlights selected text inside the visual pipeline, which a
    `RichLog` never enters, so the transcript would copy on release while
    showing nothing at all in between. Asserted against the composited strips
    rather than the widget's own render, because the question is what reaches
    the terminal.
    """
    from textual import events

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(120, 36)) as pilot:
        await _ready(app)
        transcript = app.transcript
        transcript.note("highlight this row")
        await pilot.pause()

        row = next(i for i, line in enumerate(transcript.lines) if "highlight this" in line.text)
        y = transcript.region.y + row - transcript.scroll_offset.y

        def at(kind: type, x: int, button: int) -> events.MouseEvent:
            return kind(None, x, y, 0, 0, button, False, False, False, screen_x=x, screen_y=y)

        # Held down, not released: the highlight has to be there during the drag.
        app.screen._forward_event(at(events.MouseDown, transcript.region.x + 2, 1))
        await pilot.pause()
        app.screen._forward_event(at(events.MouseMove, transcript.region.x + 18, 1))
        for _ in range(4):
            await pilot.pause()

        component = app.screen.get_component_styles("screen--selection")
        expected = (transcript.background_colors[1] + component.background).rich_color

        strip = app.screen._compositor.render_strips()[y]
        painted = [
            segment.text
            for segment in strip
            if segment.style is not None and segment.style.bgcolor == expected
        ]
        assert painted, "the dragged span carries no selection colour"
        assert "highlight this" in "".join(painted)


@pytest.mark.parametrize("selection", [None, "", "a", "ab"])
async def test_a_stray_drag_leaves_the_clipboard_alone(
    monkeypatch: pytest.MonkeyPatch, selection: str | None
) -> None:
    """A click, or a drag of a character or two, is not an intention.

    Copying on those would quietly replace a clipboard someone was still using,
    which is worse than not copying at all.
    """
    copied: list[str] = []
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: copied.append(text) or True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        await _selected(app, selection)
        await pilot.pause()

        assert copied == []


async def test_the_status_bar_says_how_much_was_copied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In the bar rather than the transcript: it is a fact about the last two
    seconds, not part of the record of what happened to the phone."""
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        await _selected(app, "one\ntwo\nthree")
        await pilot.pause()

        bar = app.query_one(StatsBar)
        assert bar.notice == "copied 3 lines"
        assert "copied 3 lines" in bar.render().plain
        assert "copied" not in "\n".join(line.text for line in app.transcript.lines)


async def test_one_line_is_not_called_one_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        await _selected(app, "just the one")
        await pilot.pause()

        assert app.query_one(StatsBar).notice == "copied 1 line"


async def test_the_notice_goes_away_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short lived, or it becomes furniture and stops being read."""
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        bar = app.query_one(StatsBar)
        # Long enough that the assertion below is not racing the timer: a
        # pause can outlast a very short window on a loaded machine.
        bar.flash("copied 2 lines", seconds=0.4)
        await pilot.pause()
        assert bar.notice

        await asyncio.sleep(0.6)
        await pilot.pause()
        assert bar.notice == ""


async def test_a_second_copy_is_not_cut_short_by_the_first_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two copies in quick succession: the first one's timer must not clear the
    second one's message."""
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(100, 30)) as pilot:
        await _ready(app)
        bar = app.query_one(StatsBar)
        bar.flash("copied 1 line", seconds=0.15)
        await asyncio.sleep(0.1)
        bar.flash("copied 9 lines", seconds=0.5)

        await asyncio.sleep(0.15)  # the first timer fires in here
        await pilot.pause()
        assert bar.notice == "copied 9 lines"


async def test_the_notice_survives_a_narrow_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counters will still be there in a second; the notice will not."""
    monkeypatch.setattr("ios_tui.app._to_clipboard", lambda text: True)

    app = IosAgentApp(_Runner)
    async with app.run_test(size=(60, 24)) as pilot:
        await _ready(app)
        bar = app.query_one(StatsBar)
        bar.stats = StatsSnapshot(observations=1, actions=9, device_tokens=4321, refusals=2)
        bar.prompt_tokens, bar.completion_tokens, bar.elapsed_s = 90000, 1234, 240.0
        await _selected(app, "one\ntwo")
        await pilot.pause()

        rendered = bar.render()
        assert "copied 2 lines" in rendered.plain
        assert rendered.cell_len <= 60
