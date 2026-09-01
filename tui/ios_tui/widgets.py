"""The panes. Each one owns a piece of state and how it is drawn, nothing else.

The layout answers a question a coding agent does not have: *where is the phone
right now?* A transcript alone cannot say. So the screen is split, with the
conversation on the left and the device's current digest on the right, and the
numbers this project is judged on pinned along the bottom where they can be
watched climbing rather than read afterwards.
"""

from __future__ import annotations

import re
import time

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Resize
from textual.reactive import reactive
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from ios_tui.commands import Command
from ios_tui.events import ActionFinished, GoalFinished, Observed, StatsSnapshot

#: The wordmark, on a phone screen, because that is what this tool is: text a
#: model can read where a screen used to be. 32 columns, which fits the
#: transcript on a 64-column terminal with room to spare.
BANNER = """\
╭────────────────────────────────────────────╮
│                  ▔▔▔▔▔▔▔▔                  │
│                                            │
│           ██████   ████    █████           │
│             ██    ██  ██  ██               │
│             ██    ██  ██   ████            │
│             ██    ██  ██      ██           │
│           ██████   ████   █████            │
│                                            │
│    ████    █████  ██████  ██  ██  ██████   │
│   ██  ██  ██      ██      ███ ██    ██     │
│   ██████  ██ ███  █████   ██████    ██     │
│   ██  ██  ██  ██  ██      ██ ███    ██     │
│   ██  ██   █████  ██████  ██  ██    ██     │
│                                            │
│               ▁▁▁▁▁▁▁▁▁▁▁▁▁▁               │
╰────────────────────────────────────────────╯"""

#: The same phone, drawn small. A pane can be too narrow for the wordmark
#: above and still wide enough for a drawing, and a drawing is the point.
BANNER_SMALL = """\
╭──────────────────────────────╮
│           ▔▔▔▔▔▔▔▔           │
│                              │
│   ┬┌─┐┌─┐  ┌─┐┌─┐┌─┐┌┐┌┌┬┐   │
│   ││ │└─┐──├─┤│ ┬├┤ │││ │    │
│   ┴└─┘└─┘  ┴ ┴└─┘└─┘┘└┘ ┴    │
│                              │
│         ▁▁▁▁▁▁▁▁▁▁▁▁         │
╰──────────────────────────────╯"""

#: When there is no room for the drawing. Not a truncated banner: half a phone
#: reads as a rendering fault, where a line of text reads as a line of text.
BANNER_NARROW = "ios-agent"

#: Verbs that changed the device get one colour, reads another. A transcript
#: where everything looks the same is a log, not a view.
_ACTING = {"tap", "type_text", "set_value", "scroll", "press_button", "open_url"}

#: Width of the transcript's right-hand column: timing, token count, or the
#: word "refused". Wide enough for "refused" and for four digits of
#: milliseconds, which covers a real device's ~3.7s snapshots.
_RIGHT = 8


class StatusBar(Static):
    """Who we are talking to, and what state the app is in."""

    device: reactive[str] = reactive("no device yet")
    model: reactive[str] = reactive("")
    state: reactive[str] = reactive("starting")
    #: Seconds since the last sign of life. `DevicePool.acquire` can be silent
    #: for minutes, and a number that keeps moving is the difference between
    #: "working" and "hung" when there is nothing else to show.
    waiting_for: reactive[float] = reactive(0.0)

    def render(self) -> Text:
        """Build right to left, because the state is what must never be cut.

        Laid out left to right, a narrow terminal truncates whatever is last,
        and what is last is the one field that says whether anything is
        happening. So the state and the waiting counter are budgeted first and
        the device name gives up its characters to them.
        """
        state = Text(f" {self.state}", style=_STATE_STYLES.get(self.state, "dim"))
        if self.state == "starting" and self.waiting_for >= 3.0:
            state.append(f" +{self.waiting_for:.0f}s", style="dim")

        width = self.size.width or 80
        line = Text()
        line.append(" ios-agent ", style="bold reverse")
        remaining = width - line.cell_len - state.cell_len - 2

        identity = Text()
        if self.device:
            identity.append(f" {self.device}")
        if self.model:
            identity.append(f" · {self.model}", style="dim")
        if identity.cell_len > remaining > 1:
            identity.truncate(remaining, overflow="ellipsis")

        line.append_text(identity)
        line.append(" ·")
        line.append_text(state)
        return line


_STATE_STYLES = {
    "starting": "yellow",
    "ready": "green",
    "working": "cyan",
    "stopping": "yellow",
    "stale": "red",
    "failed": "red",
}


class StatsBar(Static):
    """The numbers, live.

    Deliberately the same four the eval harness reports, in the same order, so
    what a person watches and what the project is measured on are one thing.
    """

    #: When the current notice stops being true. Held apart from the timer so a
    #: flash arriving during an earlier one is not cut short by its timer.
    _clear_notice_at = 0.0

    stats: reactive[StatsSnapshot] = reactive(StatsSnapshot)
    prompt_tokens: reactive[int] = reactive(0)
    completion_tokens: reactive[int] = reactive(0)
    elapsed_s: reactive[float] = reactive(0.0)
    #: Something that just happened, shown for a moment and then gone. The
    #: transcript is the record; this is for things that are not worth a line
    #: in it, like a copy that worked.
    notice: reactive[str] = reactive("")

    def flash(self, message: str, seconds: float = 2.5) -> None:
        """Say something briefly, without writing it down.

        Long enough to read, short enough that it does not become part of the
        furniture. A later flash replaces the earlier one rather than queueing,
        because only the most recent one is still true.
        """
        self.notice = message
        self._clear_notice_at = time.monotonic() + seconds
        self.set_timer(seconds, self._expire_notice)

    def _expire_notice(self) -> None:
        # A second flash arriving inside the first one's window pushes the
        # deadline out, and this timer must not clear the newer message.
        if time.monotonic() >= self._clear_notice_at:
            self.notice = ""

    def render(self) -> Text:
        """Segments, dropped from the right until the row fits.

        Written left to right and left to truncate, a narrow terminal cut the
        last field mid-word and stranded the separator that introduced it:
        `... 9 refused ·`. The order below is the priority order: what the
        device cost first, what the model cost next, wall time last.
        """
        s = self.stats
        overhead = f" ({s.observation_overhead:.2f}/action)" if s.actions else ""
        segments: list[tuple[str, str]] = [
            (f"{s.actions} actions · {s.observations} obs{overhead}", "dim"),
            (f"{s.device_tokens} device tok", "dim"),
        ]
        if s.refusals:
            segments.append((f"{s.refusals} refused", "yellow"))
        if self.prompt_tokens or self.completion_tokens:
            segments.append((f"model {self.prompt_tokens}/{self.completion_tokens}", "dim"))
        if self.elapsed_s >= 0.05:
            # Below that it rounds to "0.0s", which claims a run took no time.
            segments.append((f"{self.elapsed_s:.1f}s", "dim"))

        # The notice is reserved before anything is dropped: it is transient
        # and it is the answer to something the person just did, where the
        # counters will still be there a second later.
        width = self.size.width or 80
        reserved = len(self.notice) + 2 if self.notice else 0
        while len(segments) > 1 and _joined_width(segments) + 1 + reserved > width:
            segments.pop()

        line = Text(" ", style="dim")
        for index, (text, style) in enumerate(segments):
            if index:
                line.append(" · ", style="dim")
            line.append(text, style=style)
        if self.notice:
            line.append("  ")
            line.append(self.notice, style="green")
        return line


#: One row of the transcript, kept as data so it can be laid out again.
Entry = tuple[str, object]


class SelectableLog(RichLog):
    """A `RichLog` a mouse drag can actually select.

    A plain one cannot be selected at all, and the reason is two layers deep.

    Textual finds the character under the pointer by rendering the line and
    reading an `offset` key out of each segment's **style metadata**
    (`_compositor.get_widget_and_offset_at`). A `Static` gets that for free,
    because rendering a `Text` stamps it. A `RichLog` writes strips it has
    already rendered, and those carry no such metadata, so the compositor
    reports no offset for any point over the log.

    That is not merely a loss of precision. With no offset the widget is not a
    *content* widget, so the selection anchors to the log itself as its own
    container, and `SelectState._walk_selected_widgets` then walks that
    container's **descendants** for something to select. A log has none. The
    drag therefore selects nothing, highlights nothing, and never reaches
    `get_selection`. This is why the transcript was dead to the mouse while the
    stats bar, one line of `Static`, selected normally.

    So the metadata is stamped back on, per segment, and `get_selection`
    answers in the same coordinates.
    """

    def render_line(self, y: int) -> Strip:
        """Tag each segment with where its text lives in the log.

        `y` is a visible row; the offset has to be an absolute one or a
        selection means something different after a scroll. The x offset counts
        **characters**, not cells, because that is what the compositor's own
        arithmetic assumes when it walks a segment.
        """
        strip = super().render_line(y)
        row = self.scroll_offset.y + y
        if row >= len(self.lines):
            return strip

        segments: list[Segment] = []
        x = 0
        for segment in strip:
            meta = Style(meta={"offset": (x, row)})
            style = segment.style + meta if segment.style else meta
            segments.append(Segment(segment.text, style, segment.control))
            x += len(segment.text)
        return self._highlight(Strip(segments, strip.cell_length), row)

    def _highlight(self, strip: Strip, row: int) -> Strip:
        """Paint the selected span of one row.

        Textual paints selection inside the visual pipeline, which a log does
        not go through, so a drag would select and copy while looking like it
        had done nothing. The colour is the screen's own `screen--selection`
        component style rather than a chosen one, so the highlight matches
        every other selectable widget in the app.
        """
        selection = self.text_selection
        if selection is None:
            return strip
        span = selection.get_span(row)
        if span is None:
            return strip

        start, end = span
        if end == -1:
            end = strip.cell_length
        if end <= start:
            return strip

        styles = self.screen.get_component_styles("screen--selection")
        # The selection colour is half transparent, and a Rich style has no
        # alpha: converting it directly yields the background it was meant to
        # tint, so the highlight comes out invisible. Blend it against this
        # widget's own background first, which is what the visual pipeline
        # does for every widget that gets highlighting for free.
        style = Style(bgcolor=(self.background_colors[1] + styles.background).rich_color)
        if styles.color.a:
            style += Style(color=styles.color.rich_color)

        before, selected, after = strip.divide([start, end, strip.cell_length])
        # Composed over each segment rather than through `Strip.apply_style`,
        # which applies a style *underneath* the ones already there: the
        # transcript colours every row it writes, so a base style loses to
        # every one of them and highlights nothing.
        highlighted = Strip(
            [
                Segment(segment.text, segment.style + style if segment.style else style)
                for segment in selected
            ],
            selected.cell_length,
        )
        return Strip.join([before, highlighted, after])

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """The selected text, in the coordinates `render_line` handed out.

        Extracted from the rendered lines rather than from whatever produced
        them, because the offsets above are rendered rows: a wrapped entry
        occupies several of them, and counting in entries would land in the
        wrong place on any line long enough to matter.
        """
        text = "\n".join(strip.text for strip in self.lines)
        return selection.extract(text), "\n"


class Transcript(SelectableLog):
    """What was said and done, in order.

    Two things about `RichLog` shape this class.

    **A line is committed when it is written.** Appending streamed model text
    per delta would produce one line per token, so the live fragment lives in
    `Thinking` below and is promoted here once the turn completes.

    **A line's wrapping is fixed when it is written**, against a width the
    widget does not know until it has been rendered. That is why no row here
    pads to the pane: a layout measured against the pane is measured against
    the wrong number for every row written during startup, and those rows wrap
    at `RichLog`'s 80-column default however wide the pane actually is. Rows
    are sized by their content instead, which is correct at any width and at
    every moment. See `_action_row`.
    """

    def __init__(self, id: str = "transcript") -> None:
        super().__init__(markup=True, wrap=True, id=id)
        #: Kept as data, not only as rendered lines. Nothing re-lays them out
        #: today, but a row is a record of something that happened and the log
        #: is the only place it exists.
        self._entries: list[Entry] = []

    # -- what happened -----------------------------------------------------

    def banner(self, subtitle: str) -> None:
        """The mark, once, at the top of the session."""
        self._add(("banner", subtitle))

    def clear(self) -> Transcript:
        """Empty it, entries and all.

        `RichLog.clear` drops the rendered lines and knows nothing about the
        entries this class keeps beside them, which left the widget with two
        notions of what it contained: a cleared transcript still had a full
        history to rebuild from. Anything reading the entries, `as_text` most
        of all, then disagreed with the screen.
        """
        self._entries = []
        super().clear()
        return self

    def as_text(self) -> str:
        """The transcript as something worth pasting.

        Rebuilt from the entries rather than scraped off the rendered lines,
        which lets the wordmark be left out. Nine lines of ASCII phone is not
        what someone copying a log into a bug report is after, and it is the
        one thing on screen that carries no information.
        """
        rows: list[str] = []
        for entry in self._entries:
            if entry[0] == "banner":
                continue
            rows.extend(row.plain.rstrip() for row in self._rows(entry))
        return "\n".join(rows).strip()

    @property
    def has_banner(self) -> bool:
        """Whether the mark has been written.

        Reads the entries rather than the rendered lines, because `RichLog`
        fills `lines` incrementally: a test waiting for "any line at all" wakes
        up three rows into a nine-row drawing and measures a transcript the app
        is still writing.
        """
        return any(kind == "banner" for kind, _ in self._entries)

    def goal(self, text: str) -> None:
        self._add(("goal", text))

    def said(self, text: str) -> None:
        if text.strip():
            self._add(("said", text.strip()))

    def acted(self, event: ActionFinished) -> None:
        self._add(("acted", event))

    def observed(self, event: Observed) -> None:
        self._add(("observed", event))

    def note(self, text: str, style: str = "dim") -> None:
        self._add(("note", (text, style)))

    def finished(self, event: GoalFinished) -> None:
        self._add(("finished", event))

    def _add(self, entry: Entry) -> None:
        self._entries.append(entry)
        for line in self._rows(entry):
            self._write_row(line)

    def _write_row(self, line: Text) -> None:
        """Write one row, wrapped to the pane rather than to a guess.

        `RichLog` fixes a line's wrapping when it is written and, given no
        width, wraps at its own 80-column default however wide the pane is. In
        a pane narrower than that the row simply runs past the edge and under
        the device readout beside it, which is what a goal longer than the pane
        did. Handing it the measured width is the whole fix; before the first
        render there is no width to hand it, which is what `on_resize` is for.
        """
        self.write(line, width=self.content_size.width or None)

    def on_resize(self, event: Resize) -> None:
        """Lay the rows out again, now that the pane's width is known.

        `RichLog` fixes a line's wrapping when the line is written, against a
        width it does not have until it has been rendered, so every row written
        before the first render wraps at its 80-column default however wide the
        pane is. A goal longer than the pane therefore ran off the right-hand
        edge and under the device readout instead of wrapping.

        The rows are kept as entries precisely so they can be written again.
        This is the only thing that re-lays them out, which is why the note on
        `_entries` said nothing did.
        """
        super().on_resize(event)
        RichLog.clear(self)
        for entry in self._entries:
            for line in self._rows(entry):
                self._write_row(line)

    def _rows(self, entry: Entry) -> list[Text]:
        kind, data = entry
        match kind:
            case "banner":
                return self._banner_rows(str(data))
            case "goal":
                return [Text(f"\n> {data}", style="bold")]
            case "said":
                return [Text(str(data), style="italic")]
            case "note":
                assert isinstance(data, tuple)
                note, note_style = data
                return [Text(f"  {note}", style=str(note_style))]
            case "acted":
                assert isinstance(data, ActionFinished)
                rows = [self._action_row(data)]
                if data.hint:
                    # Under the row it explains, and only when there is one:
                    # the hint is a sentence and the row has one line.
                    rows.append(Text(f"    {data.hint}", style="dim"))
                return rows
            case "observed":
                assert isinstance(data, Observed)
                return [Text(f"  {'observe':<12}{data.stats.device_tokens} tok", "dim")]
            case _:
                assert isinstance(data, GoalFinished)
                return self._finished_rows(data)

    def _finished_rows(self, event: GoalFinished) -> list[Text]:
        """What the run says at the end, minus what it has already said.

        A model that answers without calling a tool puts one sentence in three
        places, and all three are correct in the library: it is the turn's
        text, it is the agent's summary, and it is why the loop ended without
        `done`. Typing "hello" therefore printed "Hello! How can I help?" three
        times, once in italics and twice in orange.

        Deduplicated here rather than in the agent, because none of those three
        fields is wrong. Only showing all of them is.
        """
        summary = event.summary.strip()
        stopped = (event.stopped_because or "").strip()

        rows: list[Text] = []
        if summary and summary != self._last_said:
            rows.append(Text(f"  {summary}", style="green" if event.succeeded else "yellow"))
        elif not summary:
            rows.append(Text("  (no summary)", style="yellow"))

        # "stopped: X" under a line that already says X is a label with nothing
        # to label. It earns its place only when it says something new.
        if stopped and stopped != summary and stopped != self._last_said:
            rows.append(Text(f"  stopped: {stopped}", style="yellow"))
        return rows

    @property
    def _last_said(self) -> str:
        """The most recent thing the model was shown to have said."""
        for kind, data in reversed(self._entries):
            if kind == "said":
                return str(data).strip()
            if kind == "goal":
                # A new goal starts a new conversation; anything before it was
                # said about something else.
                return ""
        return ""

    def _banner_rows(self, subtitle: str) -> list[Text]:
        """The largest drawing that fits, and the name when none does.

        Measured against the pane rather than the terminal: this sits in the
        left half of a split, so a 64-column terminal gives it about 40. Two
        sizes rather than one, for the same reason the status bar carries
        several phrasings: a pane too narrow for the wordmark is usually still
        wide enough for a phone, and dropping straight to a line of text throws
        away more than it has to.
        """
        width = self.size.width or 80
        for art in (BANNER, BANNER_SMALL):
            if width < len(art.splitlines()[0]) + 2:
                continue
            rows = [Text(line, style="dim cyan") for line in art.splitlines()]
            rows.append(Text(f"  {subtitle}", style="dim"))
            return rows

        # No room for the drawing. The name always fits; the subtitle is
        # dropped rather than wrapped, because a two-line greeting in a pane
        # this narrow costs more than it says.
        name = Text(BANNER_NARROW, style="bold")
        if len(BANNER_NARROW) + len(subtitle) + 2 <= width:
            name.append(f"  {subtitle}", style="dim")
        return [name]

    def _action_row(self, event: ActionFinished) -> Text:
        """Verb, then what it was aimed at, then what it cost.

        Only the verb is padded. The target runs to its natural length and the
        cost follows it, so a row is as long as its content and never longer.

        A right-aligned cost column would scan better and was tried twice. It
        cannot be done reliably here: the padding has to be measured against
        the pane, and `RichLog` fixes a line's wrapping when the line is
        written, from a region it does not have until it has been rendered. Rows
        written during startup were padded to a width the pane did not have and
        wrapped at 80 columns regardless, which is far worse than ragged.
        """
        line = Text("  ")
        # A refusal never reached the device and an error did not finish, so in
        # both cases the verb is what is remarkable rather than the timing.
        # Colouring the first column means it survives any width; a note at the
        # end of the row does not.
        unusual = event.refused or bool(event.error)
        style = "yellow" if unusual else ("cyan" if event.verb in _ACTING else "dim")
        line.append(f"{event.verb:<12}", style=style)
        target = _target_of(event.args)
        if target:
            line.append(f"{target}  ")
        if event.refused:
            line.append("refused", style="yellow")
        elif event.error:
            # Yellow, not red. The agent hands these to the model and the next
            # turn usually fixes them, so a run full of red on its way to
            # succeeding reads as a disaster that did not happen.
            line.append(event.error, style="yellow")
        elif event.elapsed_ms:
            line.append(f"{event.elapsed_ms}ms", style="dim")
        return line


class Thinking(Static):
    """The one line that streams.

    Kept out of the transcript until it is complete, because a log line cannot
    be rewritten and a model's turn arrives in fragments.
    """

    def __init__(self) -> None:
        super().__init__("", id="thinking")
        self._buffer = ""

    def add(self, fragment: str) -> None:
        self._buffer += fragment
        self.update(Text(self._buffer.strip(), style="italic dim"))
        self.display = bool(self._buffer.strip())

    def take(self) -> str:
        text, self._buffer = self._buffer, ""
        self.update("")
        self.display = False
        return text


class ScreenPane(Vertical):
    """The phone, as the model reads it.

    The body is `Digest.render()` verbatim: the exact text the model was
    handed. Showing anything prettier would mean the person and the model are
    looking at different screens, and every disagreement about what went wrong
    would start with working out which one was right.

    Above it sits one row of chrome saying whether that text is still true.
    This pane is a readout, and a readout nobody can date is worth less than no
    readout at all: most actions hand back a delta rather than a whole screen,
    so what is displayed falls behind the phone constantly and by design.

    The currency line lives here rather than in the body deliberately. It was
    prepended into the digest text, which mixed chrome into the one string that
    is supposed to be exactly what the model saw.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.text = ""
        #: Actions since this screen was captured.
        self.stale_by = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="screen-currency")
        with VerticalScroll(id="screen-scroll"):
            yield Static("", id="screen-text")

    def on_mount(self) -> None:
        # Drawn once on mount so the placeholder and every later update go
        # through one path. An empty pane and a pane showing an empty screen
        # look identical, and only one of them means "nothing has been read".
        self._draw()

    @property
    def displayed_text(self) -> str:
        """What is in the body, placeholder included."""
        return self.text or "(nothing read yet)"

    def show(self, text: str) -> None:
        self.text = text
        self.stale_by = 0
        self._draw()

    def overtaken(self) -> None:
        """An action changed the phone without handing back a whole screen."""
        self.stale_by += 1
        self._draw()

    def _draw(self) -> None:
        self.query_one("#screen-text", Static).update(
            Text(self.displayed_text, style="" if self.text else "dim")
        )
        self.query_one("#screen-currency", Static).update(self._currency())

    def _where(self) -> str:
        """The app and screen this pane is showing, from the digest's own header.

        The digest opens with `screen: <bundle id> / "<title>"`, which is the
        only place either fact exists by the time it reaches this widget. The
        bundle id is trimmed to its last component, because "com.apple.Maps"
        spends eleven columns saying "Maps" in a strip that has about forty.
        """
        first = self.text.splitlines()[0] if self.text else ""
        match = re.match(r'screen:\s*(\S+)\s*/\s*"(.*?)"', first)
        if match is None:
            return ""
        app = match.group(1).rsplit(".", 1)[-1]
        title = match.group(2).strip()
        return f"{app} \u00b7 {title}" if title else app

    def _currency(self) -> Text:
        """One row: is this what the phone shows?

        The wording is the point. "behind" rather than "ago" because the unit
        is actions, not time; a run can sit on one snapshot for a minute and be
        perfectly current.

        Written to the room available, longest form first. Letting it truncate
        cut "ctrl+r to re-read" mid-phrase and left a dangling separator, which
        is the same failure the status bar had: the fix is to choose a shorter
        sentence, not to let one be sliced.
        """
        width = self.size.width or 40
        if not self.text:
            return Text(_fit(width, "waiting for the first screen", "waiting"), style="dim")
        where = self._where()
        if not self.stale_by:
            # Naming the screen rather than asserting " current", which said
            # only that the pane was not stale and left the reader to work out
            # what they were looking at from the body text.
            if not where:
                return Text(" on screen", style="dim")
            return Text(_fit(width, f" {where}", " on screen"), style="dim")
        plural = "" if self.stale_by == 1 else "s"
        behind = f"{self.stale_by} action{plural} behind"
        return Text(
            _fit(
                width,
                f" {where} \u00b7 {behind} \u00b7 ctrl+r to re-read"
                if where
                else f"{behind} \u00b7 ctrl+r to re-read",
                f"{behind} \u00b7 ctrl+r to re-read",
                behind,
                f"{self.stale_by} behind",
            ),
            style="yellow",
        )


class SlashMenu(OptionList):
    """The commands matching what has been typed after a slash.

    Sits above the input rather than below it. The input is docked to the
    bottom of the screen, so a menu underneath would have nowhere to go, and
    one that grows upward keeps the row you are typing in the same place.
    """

    def __init__(self) -> None:
        super().__init__(id="slash-menu")
        self.display = False
        self.commands: list[Command] = []

    def offer(self, commands: list[Command]) -> None:
        """Show these, or nothing if there are none."""
        self.commands = commands
        self.clear_options()
        if not commands:
            self.display = False
            return
        self.add_options([Option(_command_row(c), id=c.name) for c in commands])
        self.display = True
        self.highlighted = 0

    def hide(self) -> None:
        self.display = False
        self.commands = []
        self.clear_options()

    @property
    def chosen(self) -> Command | None:
        if not self.display or self.highlighted is None:
            return None
        if self.highlighted >= len(self.commands):
            return None
        return self.commands[self.highlighted]


#: Width of the help column in the command menu. A fixed column is safe here,
#: unlike in the transcript: these strings are written in this repository and
#: the longest is known, where a transcript row carries whatever an app chose
#: to call a button.
_HELP = 38


def _command_row(command: Command) -> Text:
    line = Text()
    line.append(f"/{command.name:<10}", style="bold")
    line.append(f"{command.help:<{_HELP}}", style="dim")
    if command.key:
        line.append(command.key, style="dim")
    return line


#: What the input asks for, which is not the same question in both modes.
#: "what should it do?" invites a sentence, and in manual mode a sentence is
#: the one thing that does not work: it takes device verbs. Asking the wrong
#: question is how someone ends up typing "hi" and being told off for it.
GOAL_PROMPT = "what should it do?"
MANUAL_PROMPT = "a device verb, or `help` to list them"


class GoalInput(Input):
    def __init__(self, *, manual: bool = False) -> None:
        super().__init__(placeholder=MANUAL_PROMPT if manual else GOAL_PROMPT, id="goal-input")


def _joined_width(segments: list[tuple[str, str]]) -> int:
    return sum(len(text) for text, _ in segments) + 3 * (len(segments) - 1)


def _fit(width: int, *options: str) -> str:
    """The longest of `options` that fits, with a leading space for the gutter.

    Never returns a truncation: a cut sentence reads as a rendering fault, and
    a shorter sentence that is whole does not.
    """
    for option in options:
        if len(option) + 1 <= width:
            return f" {option}"
    return f" {options[-1]}"


def _pad(text: str, width: int) -> str:
    """Fit a target into a column, ellipsising rather than wrapping."""
    if len(text) > width:
        return text[: max(1, width - 1)] + "\u2026"
    return f"{text:<{width}}"


def _target_of(args: object) -> str:
    """The one argument worth a column."""
    if not isinstance(args, dict):
        return ""
    for key in ("target", "url", "name", "direction"):
        value = args.get(key)
        if value:
            return str(value)
    return ""


class LogPane(SelectableLog):
    """What the machine was doing while it got ready.

    A separate pane from the transcript, and hidden by default, because device
    startup is dozens of lines on a cold simulator and the three lines saying
    what the agent did would be buried in them. Toggled with ctrl+l, and worth
    having: WebDriverAgent recovery during a run lands here too.

    Deliberately **not** a subclass of `Transcript`. It was one, and that made
    `query_one(Transcript)` ambiguous: Textual matches subclasses, so the query
    returned this hidden pane and every line the transcript was told to write
    went somewhere nobody could see. Two widgets that happen to be scrolling
    logs are not the same kind of thing.
    """

    def __init__(self) -> None:
        super().__init__(markup=True, wrap=True, id="log-pane")
