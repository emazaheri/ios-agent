"""What survives a narrow terminal.

Every one of these was found by rendering the app to an image and looking at
it, not by a failing assertion, which is the point: widget state can be
entirely correct while what a person sees is wrong. `scripts/tui_screenshot.py`
is how they were found; these are how they stay found.

64 columns is the width used throughout. It is a split pane inside a small
terminal, which leaves the transcript about 39 columns, and every fixed-width
assumption in the layout broke there.
"""

from __future__ import annotations

import asyncio

import pytest
from ios_tui.app import IosAgentApp
from ios_tui.events import ActionFinished, DeviceReady, Observed, Progress, StatsSnapshot
from ios_tui.runner import GoalRunner
from ios_tui.widgets import ScreenPane, StatsBar, StatusBar
from screens import DeviceModel, build_session
from textual.widgets import Static
from tui_harness import ScriptedModel, settings

NARROW = (64, 24)
WIDE = (120, 38)


class _Stub(GoalRunner):
    def __init__(self, sink: object, model: DeviceModel) -> None:
        session, _, _ = build_session(model, settings())
        super().__init__(sink, settings(), model=ScriptedModel([]))  # type: ignore[arg-type]
        self.session = session

    async def start(self) -> object:
        self.sink.emit(Progress(text="Booting simulator iPhone 17"))
        self.sink.emit(
            DeviceReady(
                lease={"device": {"name": "iPhone 17", "os_version": "26.5", "kind": "simulator"}}
            )
        )
        return self.session

    async def close(self) -> None:
        return None


def _app() -> IosAgentApp:
    model = DeviceModel()
    return IosAgentApp(lambda sink: _Stub(sink, model))


async def _ready(app: IosAgentApp) -> None:
    async with asyncio.timeout(10):
        while app.query_one(StatusBar).state == "starting":
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("size", [NARROW, WIDE])
async def test_a_transcript_row_is_never_padded_past_the_pane(size: tuple[int, int]) -> None:
    """Rows are as long as their content, and never longer.

    They used to pad the target out to a column measured against the pane,
    which was wrong twice over. At 64 columns the column was wider than the
    pane, so every row wrapped and the tail of a refusal landed alone on the
    next line. And the measurement itself could not be trusted: `RichLog` fixes
    a line's wrapping when the line is written, from a region it does not have
    until it has been rendered, so rows written during startup were padded to a
    width the pane did not have and wrapped at 80 columns regardless.

    Content-sized rows are correct at any width and at any moment. Genuinely
    long content still wraps, which is ordinary text behaviour and reads as
    such; padding that wraps reads as a broken layout.
    """
    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        # Only the rows added below. Free-text notes above them (a startup
        # warning, say) wrap because they are prose, which the docstring says
        # is fine; what must not wrap is a row this widget formats itself.
        before = len(app.transcript.lines)
        for i in range(12):
            app._apply(
                ActionFinished(
                    verb="set_value",
                    args={"target": "Bold Text", "value": "on"},
                    elapsed_ms=3721,
                    stats=StatsSnapshot(actions=i + 1),
                )
            )
        app._apply(Observed(stats=StatsSnapshot(observations=1, device_tokens=1275)))
        await pilot.pause()

        transcript = app.transcript
        rows = transcript.lines[before:]
        budget = transcript.size.width - 2
        too_wide = [line.text for line in rows if len(line.text.rstrip()) > budget]
        assert too_wide == [], (
            f"rows of ordinary length wrapped in {budget} columns:\n" + "\n".join(too_wide)
        )
        # One rendered line per row, at both widths, is what "never padded"
        # buys: the same transcript reads the same way in any terminal.
        assert len(rows) == 13


async def test_a_refusal_stays_visible_when_the_columns_are_squeezed() -> None:
    """It used to be a note tacked on the end, so it was the first thing cut.

    The stats bar would say nine refused while not one transcript row showed
    which. A refusal never reached the device, so it belongs in the verb column
    where nothing can truncate it away.

    The assertion is against the *visible* width, not the line's text. A
    `RichLog` keeps the whole string it was handed whether or not the pane can
    show it, so `"refused" in line.text` is true even when a person cannot see
    it, and the first version of this test passed against the bug.
    """
    app = _app()
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app)
        app._apply(
            ActionFinished(
                verb="scroll",
                args={"direction": "down"},
                refused=True,
                stats=StatsSnapshot(refusals=1),
            )
        )
        await pilot.pause()

        visible = max(1, app.transcript.size.width - 2)
        shown = "\n".join(line.text[:visible] for line in app.transcript.lines)
        assert "refused" in shown, (
            f"the refusal is past column {visible}, so nothing on screen says the "
            f"action never ran:\n" + "\n".join(line.text for line in app.transcript.lines)
        )


@pytest.mark.parametrize("size", [NARROW, WIDE])
async def test_the_status_bar_never_drops_the_state(size: tuple[int, int]) -> None:
    """The state is the field that says whether anything is happening.

    Laid out left to right it was last, so a narrow terminal cut exactly the
    one thing worth keeping and left the device name intact.
    """
    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        bar = app.query_one(StatusBar)
        bar.model = "openai:gpt-5.6-sol"
        bar.state = "working"
        await pilot.pause()

        rendered = bar.render()
        assert "working" in rendered.plain
        assert rendered.cell_len <= size[0], "the status bar overflowed its row"


async def test_the_screen_pane_says_so_before_anything_has_been_read() -> None:
    """An empty pane and a pane showing an empty screen look the same.

    Asserted against the `Static`'s own content rather than against the pane's
    `displayed_text` property. The property was always right; the bug was that
    nothing drew it, because the placeholder lived in `compose` and every later
    update went through `_draw`. Testing the property tests the wrong half.
    """
    from textual.widgets import Static

    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        await pilot.pause()

        drawn = str(app.query_one("#screen-text", Static).content)
        assert "nothing read yet" in drawn


@pytest.mark.parametrize("size", [NARROW, WIDE])
async def test_nothing_in_the_approval_modal_falls_off_the_screen(size: tuple[int, int]) -> None:
    """The one widget where cut text is a safety problem, not a cosmetic one.

    Fixed at 70 columns the modal overflowed a 64-column terminal: the reason
    widget's right edge sat at column 67, three columns past the end of the
    display, and the sentence explaining why the action was flagged was clipped
    to one row. Approving an action whose justification ran off the screen is
    consent to something nobody read.

    The assertion is on `region`, the widget's absolute position, rather than
    on `size`. `size` is the content box and excludes the border, so in the
    broken case it reported a comfortable 64 while the modal was painting past
    the edge of the terminal. Asserting the wrong box is how the first version
    of this test passed against the bug it was written for.
    """
    from ios_tui.approval import ApprovalModal

    reason = "type_text on 'Search' matches the destructive rule 'delete'"
    goal = "search my messages for the word delete and remove every one of them"

    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        app.push_screen(
            ApprovalModal(
                {
                    "action": "type_text",
                    "signature": "type_text:Search|delete everything",
                    "reason": reason,
                    "goal": goal,
                }
            )
        )
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ApprovalModal)

        width, height = size
        overflowing = [
            f"{widget.id or type(widget).__name__} occupies {widget.region!r}"
            for widget in modal.query("*")
            if widget.region.right > width or widget.region.bottom > height
        ]
        assert overflowing == [], (
            f"parts of the approval modal are outside a {width}x{height} terminal:\n"
            + "\n".join(overflowing)
        )

        # And the sentences wrapped rather than being cut to one row.
        for widget_id, sentence in (("#approval-reason", reason), ("#approval-goal", goal)):
            widget = modal.query_one(widget_id)
            if len(sentence) > widget.size.width:
                assert widget.size.height > 1, f"{widget_id} was clipped to a single row"

        # Both answers stay reachable: a narrow terminal must not leave a
        # person with only one button.
        assert modal.query_one("#refuse").size.width > 0
        assert modal.query_one("#allow").size.width > 0


@pytest.mark.parametrize("size", [NARROW, WIDE])
async def test_no_status_row_ends_in_a_dangling_separator(size: tuple[int, int]) -> None:
    """A cut sentence reads as a rendering fault; a short whole one does not.

    Both bars used to be written left to right and left to truncate, so a
    narrow terminal sliced the last field mid-word and left the separator that
    introduced it stranded at the edge: `... 9 refused ·`.

    The stats bar now drops whole segments from the right, and the currency
    strip picks the longest wording that fits. Neither ever renders a partial
    field.
    """
    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        bars = app.query_one(StatsBar)
        bars.stats = StatsSnapshot(observations=1, actions=3, device_tokens=271, refusals=9)
        bars.prompt_tokens, bars.completion_tokens, bars.elapsed_s = 8429, 110, 24.0
        pane = app.query_one(ScreenPane)
        pane.show('screen: com.apple.Preferences / "Settings"')
        for _ in range(3):
            pane.overtaken()
        await pilot.pause()

        for rendered, where in (
            (bars.render(), "stats bar"),
            (pane.query_one("#screen-currency", Static).content, "currency strip"),
        ):
            text = str(rendered).rstrip()
            assert not text.endswith("·"), f"the {where} ends in a stranded separator: {text!r}"
            assert len(text) <= size[0], f"the {where} overflowed its row: {text!r}"

        # The count survives at any width; only the hint after it is dropped.
        assert "3 actions behind" in str(pane.query_one("#screen-currency", Static).content)
