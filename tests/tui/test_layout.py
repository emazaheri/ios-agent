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
from ios_tui.events import (
    ActionFinished,
    DeviceReady,
    GoalFinished,
    GoalStarted,
    ModelTurn,
    Observed,
    Progress,
    StatsSnapshot,
)
from ios_tui.runner import GoalRunner
from ios_tui.widgets import (
    BANNER,
    BANNER_NARROW,
    BANNER_SMALL,
    ScreenPane,
    StatsBar,
    StatusBar,
    Transcript,
)
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


def _app(*, inline: bool = False) -> IosAgentApp:
    model = DeviceModel()
    return IosAgentApp(lambda sink: _Stub(sink, model), inline=inline)


async def _ready(app: IosAgentApp) -> None:
    """Wait for the device *and* for startup to finish drawing.

    The banner is deferred past the first refresh, so it lands after the state
    goes ready. A test that measures the transcript before it arrives measures
    a transcript the app has not finished writing.
    """
    async with asyncio.timeout(10):
        while app.query_one(StatusBar).state == "starting":
            await asyncio.sleep(0.02)
        if not app.inline_mode:
            while not app.transcript.has_banner:
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


# -- the wordmark ----------------------------------------------------------


async def test_the_banner_is_drawn_at_a_width_that_fits_it() -> None:
    """A phone drawn wider than its pane wraps, and half a phone reads as a
    rendering fault rather than as a picture."""
    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        await pilot.pause()

        transcript = app.transcript
        drawn = [line.text for line in transcript.lines if line.text.strip()]
        assert drawn[0].startswith("╭"), "the drawing was not shown on a wide pane"
        assert all(len(line.rstrip()) <= transcript.size.width for line in drawn)


async def test_a_narrow_pane_gets_the_name_and_not_a_broken_drawing() -> None:
    """The mark has a fixed width, so below it there is nothing to shrink.

    A line of text reads as a line of text; a truncated phone does not.
    """
    app = _app()
    async with app.run_test(size=(50, 24)) as pilot:
        await _ready(app)
        await pilot.pause()

        transcript = app.transcript
        drawn = [line.text for line in transcript.lines if line.text.strip()]
        assert drawn[0].startswith("ios-agent")
        assert not any("╭" in line for line in drawn), "half a phone was drawn"

        # Asserted on the rows the banner produces rather than on the whole
        # transcript, which by now also holds whatever startup had to say.
        rows = transcript._banner_rows("an agent that drives an iPhone")
        assert len(rows) == 1
        assert rows[0].cell_len <= transcript.size.width


async def test_the_banner_is_measured_after_the_pane_has_been_laid_out() -> None:
    """Written during mount it measures `RichLog`'s 80-column default.

    The pane in a 50-column terminal is 31, so the drawing was chosen and then
    wrapped. This is the same timing trap that made the transcript's columns
    unreliable, and it bites anything with a fixed width.
    """
    app = _app()
    async with app.run_test(size=(50, 24)) as pilot:
        await _ready(app)
        await pilot.pause()

        transcript = app.transcript
        assert transcript.size.width < 34, "this test no longer exercises a narrow pane"
        # The drawing is all-or-nothing, so this is the assertion that matters:
        # a pane too narrow for it must not contain any of it.
        assert not any("─" * 10 in line.text for line in transcript.lines)


async def test_inline_mode_spends_its_rows_on_the_run() -> None:
    """Twenty rows is the whole of that shape. Nine on a drawing is a third of
    the transcript gone before anything has happened."""
    app = _app(inline=True)
    async with app.run_test(size=(120, 20)) as pilot:
        await _ready(app)
        await pilot.pause()

        assert not any("╭" in line.text for line in app.transcript.lines)


# -- what a run says at the end --------------------------------------------


async def test_a_chatty_reply_is_not_printed_three_times() -> None:
    """Typing "hello" printed "Hello! How can I help?" three times.

    All three are correct in the agent: it is the turn's text, it is the
    summary, and it is why the loop ended without calling `done`. None of the
    fields is wrong; showing all of them is.
    """
    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        reply = "Hello! How can I help?"
        app._apply(GoalStarted(goal="hello", model="openai:gpt-5.6-sol"))
        app._apply(ModelTurn(text=reply))
        app._apply(GoalFinished(goal="hello", summary=reply, stopped_because=reply))
        await pilot.pause()

        shown = [line.text for line in app.transcript.lines if reply in line.text]
        assert len(shown) == 1, "the same sentence was printed more than once:\n" + "\n".join(shown)


async def test_a_summary_that_says_something_new_is_still_shown() -> None:
    """The deduplication is against repetition, not against summaries."""
    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        app._apply(GoalStarted(goal="turn on bold text", model="m"))
        app._apply(ModelTurn(text="I'll open Accessibility."))
        app._apply(
            GoalFinished(goal="turn on bold text", succeeded=True, summary="Bold Text is on.")
        )
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "I'll open Accessibility." in written
        assert "Bold Text is on." in written


async def test_a_real_stop_reason_survives() -> None:
    """`stopped:` earns its place when it says something the summary does not."""
    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        app._apply(GoalStarted(goal="scroll forever", model="m"))
        app._apply(
            GoalFinished(
                goal="scroll forever",
                summary="I could not find it.",
                stopped_because="gave up after 24 turns",
            )
        )
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "I could not find it." in written
        assert "stopped: gave up after 24 turns" in written


async def test_a_reply_from_an_earlier_goal_does_not_silence_a_later_summary() -> None:
    """The lookback stops at the goal that started this run.

    Two goals answered the same way would otherwise have the second summary
    suppressed by the first goal's reply.
    """
    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        reply = "Done."
        app._apply(GoalStarted(goal="first", model="m"))
        app._apply(ModelTurn(text=reply))
        app._apply(GoalFinished(goal="first", succeeded=True, summary=reply))
        app._apply(GoalStarted(goal="second", model="m"))
        app._apply(GoalFinished(goal="second", succeeded=True, summary=reply))
        await pilot.pause()

        shown = [line.text for line in app.transcript.lines if reply in line.text]
        assert len(shown) == 2, "the second goal's summary was suppressed by the first goal's"


# -- naming the screen, and wrapping what is said about it ------------------


async def test_the_strip_names_the_screen_rather_than_asserting_it_is_current() -> None:
    """ " current" said only that the pane was not stale. It left the reader to
    work out what they were looking at from the body text."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _ready(app)
        pane = app.query_one(ScreenPane)
        pane.show('screen: com.apple.Maps / "Display & Text Size"\ne1 button "Back"')
        await pilot.pause()

        strip = str(pane.query_one("#screen-currency", Static).content)
        assert "Maps" in strip
        assert "Display & Text Size" in strip
        assert "com.apple.Maps" not in strip, "the bundle id spends columns saying Maps"


async def test_a_screen_with_no_title_still_names_its_app() -> None:
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _ready(app)
        pane = app.query_one(ScreenPane)
        pane.show('screen: com.apple.springboard / ""\ne1 icon "Maps"')
        await pilot.pause()
        assert "springboard" in str(pane.query_one("#screen-currency", Static).content)


async def test_an_unparsable_body_falls_back_rather_than_showing_chrome() -> None:
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _ready(app)
        pane = app.query_one(ScreenPane)
        pane.show("something that is not a digest header")
        await pilot.pause()
        assert "on screen" in str(pane.query_one("#screen-currency", Static).content)


@pytest.mark.parametrize("size", [(90, 30), (140, 40)])
async def test_a_long_goal_wraps_inside_the_transcript(size: tuple[int, int]) -> None:
    """RichLog fixes a line's wrapping when it is written, against a width it
    does not have until it has been rendered, so a goal longer than the pane
    ran off under the device readout instead of wrapping."""
    goal = (
        "What's the estimated driving time from North York to downtown Toronto, "
        "and which route does it suggest taking at this hour?"
    )
    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        transcript = app.query_one(Transcript)
        transcript.goal(goal)
        await pilot.pause()

        pane_width = transcript.size.width
        widest = max((strip.cell_length for strip in transcript.lines), default=0)
        assert widest <= pane_width, (
            f"a row is {widest} columns wide in a {pane_width}-column pane, "
            "so it runs under the pane beside it"
        )
        assert "downtown Toronto" in transcript.as_text()


# -- the wordmark degrades in steps -----------------------------------------


@pytest.mark.parametrize(
    ("width", "expected"),
    # Terminal widths, and the form the transcript pane can hold at each. The
    # pane is the left half of a split, so 70 columns of terminal is 44 of pane.
    [(120, "large"), (70, "small"), (44, "text")],
)
async def test_the_wordmark_picks_the_largest_drawing_that_fits(width: int, expected: str) -> None:
    """A pane too narrow for the wordmark is usually still wide enough for a
    phone, so there are two drawings before the fallback to a line of text."""
    app = _app()
    async with app.run_test(size=(width, 40)) as pilot:
        await _ready(app)
        transcript = app.query_one(Transcript)
        transcript.clear()
        transcript.banner("an agent that drives an iPhone")
        await pilot.pause()

        # The banner is deliberately absent from `as_text`, so read what was
        # actually drawn.
        text = "\n".join(strip.text for strip in transcript.lines)
        drawn = {
            "large": BANNER.splitlines()[3] in text,
            "small": BANNER_SMALL.splitlines()[3] in text,
            "text": BANNER_NARROW in text,
        }
        assert drawn[expected], f"expected the {expected} form at width {width}"

        pane = transcript.size.width
        widest = max((strip.cell_length for strip in transcript.lines), default=0)
        assert widest <= pane, f"the wordmark is {widest} wide in a {pane}-column pane"
