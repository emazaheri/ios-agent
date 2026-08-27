"""The panes. Each one owns a piece of state and how it is drawn, nothing else.

The layout answers a question a coding agent does not have: *where is the phone
right now?* A transcript alone cannot say. So the screen is split, with the
conversation on the left and the device's current digest on the right, and the
numbers this project is judged on pinned along the bottom where they can be
watched climbing rather than read afterwards.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Input, RichLog, Static

from ios_tui.events import ActionFinished, GoalFinished, Observed, StatsSnapshot

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

    stats: reactive[StatsSnapshot] = reactive(StatsSnapshot)
    prompt_tokens: reactive[int] = reactive(0)
    completion_tokens: reactive[int] = reactive(0)
    elapsed_s: reactive[float] = reactive(0.0)

    def render(self) -> Text:
        s = self.stats
        line = Text(style="dim")
        line.append(f" {s.actions} actions · {s.observations} obs")
        if s.actions:
            line.append(f" ({s.observation_overhead:.2f}/action)")
        line.append(f" · {s.device_tokens} device tok")
        if s.refusals:
            line.append(f" · {s.refusals} refused", style="yellow")
        if self.prompt_tokens or self.completion_tokens:
            line.append(f" · model {self.prompt_tokens}/{self.completion_tokens}")
        if self.elapsed_s:
            line.append(f" · {self.elapsed_s:.1f}s")
        return line


class Transcript(RichLog):
    """What was said and done, in order.

    Streamed model text does not come here token by token. A `RichLog` line is
    committed once written, so appending per delta produces one line per token;
    the live fragment lives in `Thinking` below and is promoted here when the
    turn completes.
    """

    def __init__(self, id: str = "transcript") -> None:
        super().__init__(markup=True, wrap=True, id=id)

    def goal(self, text: str) -> None:
        self.write(Text(f"\n> {text}", style="bold"))

    def said(self, text: str) -> None:
        if text.strip():
            self.write(Text(text.strip(), style="italic"))

    def acted(self, event: ActionFinished) -> None:
        line = Text("  ")
        # A refusal never reached the device, so it is the verb that is
        # remarkable rather than the timing. Marking it in the first column
        # means it survives any width; a note tacked on the end does not, and
        # at 64 columns it vanished entirely while the counter still said nine.
        style = "yellow" if event.refused else ("cyan" if event.verb in _ACTING else "dim")
        line.append(f"{event.verb:<12}", style=style)
        line.append(_pad(_target_of(event.args), self._target_width()))
        if event.refused:
            line.append(f"{'refused':>{_RIGHT}}", style="yellow")
        else:
            line.append(f"{event.elapsed_ms:>{_RIGHT - 2}}ms", style="dim")
        self.write(line)

    def observed(self, event: Observed) -> None:
        target = _pad("", self._target_width())
        tokens = f"{event.stats.device_tokens:>{_RIGHT - 4}} tok"
        self.write(Text(f"  {'observe':<12}{target}{tokens}", "dim"))

    def _target_width(self) -> int:
        """How much room the middle column actually has.

        Fixed at 38 it was wider than the whole pane on a narrow terminal, so
        every action wrapped and the tail of a refusal appeared on its own line
        as an orange fragment reading "reached the device".

        The two extra columns are the vertical scrollbar, which appears once
        the transcript overflows and is not deducted from `size.width`. Without
        them the right-hand column loses its last characters exactly when there
        is enough history to be worth reading.
        """
        return max(6, (self.size.width or 80) - 2 - 12 - _RIGHT - 2)

    def note(self, text: str, style: str = "dim") -> None:
        self.write(Text(f"  {text}", style=style))

    def finished(self, event: GoalFinished) -> None:
        style = "green" if event.succeeded else "yellow"
        self.write(Text(f"  {event.summary or '(no summary)'}", style=style))
        if event.stopped_because:
            self.write(Text(f"  stopped: {event.stopped_because}", style="yellow"))


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


class ScreenPane(VerticalScroll):
    """Where the phone is, in the agent's own words.

    This is `Digest.render()` verbatim: the exact text the model was handed.
    Showing anything prettier would mean the person and the model are looking
    at different screens, and every disagreement about what went wrong would
    start with working out which one was right.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.text = ""
        #: Actions since this screen was captured. Not zero for long: most
        #: actions hand back a delta rather than a whole screen, which is what
        #: keeps a long flow cheap and what makes this counter necessary.
        self.stale_by = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="screen-text")

    def on_mount(self) -> None:
        # Drawn once on mount so the placeholder and every later update go
        # through one path. An empty pane and a pane showing an empty screen
        # look identical, and only one of them means "nothing has been read".
        self._draw()

    @property
    def displayed_text(self) -> str:
        """What is on screen, placeholder included."""
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
        # Assembled rather than concatenated. `Text(a, style=...) + Text(b)`
        # carries the first operand's style onto the whole result, which turned
        # the entire digest yellow whenever the header was present.
        parts: list[tuple[str, str]] = []
        if self.stale_by:
            parts.append(
                (
                    f"{self.stale_by} action(s) since this was read, ctrl+r to re-read\n",
                    "yellow",
                )
            )
        parts.append((self.displayed_text, "" if self.text else "dim"))
        self.query_one("#screen-text", Static).update(Text.assemble(*parts))


class GoalInput(Input):
    def __init__(self) -> None:
        super().__init__(placeholder="what should it do?", id="goal-input")


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


class LogPane(RichLog):
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
