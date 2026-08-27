"""The terminal app.

Three things here are decisions rather than plumbing.

**Events arrive through a queue, drained in batches.** One widget update per
batch, not per event. Streamed model text arrives at token rate, and a refresh
per token is how a terminal app ends up slower than the model it is displaying.

**Escape stops gracefully first.** `session.halt()` is read by the graph after
every node, so the run ends with a complete outcome and real numbers. Only a
second escape cancels the worker, and because that can tear off an HTTP call
mid-action the app then says so rather than pretending it knows where the phone
is.

**Nothing here calls `basicConfig`.** Device progress arrives through
`progress.device_progress`, which stops those records propagating; anything
else writing to stderr would paint straight through the canvas.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic, time
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer

from ios_tui.approval import ApprovalModal
from ios_tui.bus import EventSink, QueueSink, drain
from ios_tui.events import (
    ActionFinished,
    ApprovalAsked,
    DeviceReady,
    Event,
    Failed,
    GoalFinished,
    GoalStarted,
    ModelDelta,
    ModelTurn,
    Observed,
    Progress,
    ScreenUpdated,
    Stopping,
)
from ios_tui.runner import GoalRunner
from ios_tui.widgets import (
    GoalInput,
    LogPane,
    ScreenPane,
    StatsBar,
    StatusBar,
    Thinking,
    Transcript,
)


class IosAgentApp(App[int]):
    """One device, a transcript, and the numbers."""

    CSS_PATH = "theme.tcss"
    TITLE = "ios-agent"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "stop", "Stop", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+l", "toggle_log", "Log", show=True),
        Binding("ctrl+r", "reread", "Re-read screen", show=False),
        Binding("ctrl+s", "save", "Save trail", show=False),
    ]

    def __init__(
        self,
        runner_factory: Callable[[EventSink], GoalRunner],
        *,
        queue: asyncio.Queue[Event] | None = None,
        goal: str | None = None,
        approve: bool = False,
        max_steps: int | None = None,
        inline: bool = False,
        manual: bool = False,
    ) -> None:
        super().__init__()
        self._runner_factory = runner_factory
        self._queue: asyncio.Queue[Event] = queue or asyncio.Queue()
        self._first_goal = goal
        self._approve = approve
        self._max_steps = max_steps
        self.inline_mode = inline
        #: No model in the loop: typed commands go straight to the backend.
        self.manual_mode = manual

        self.runner: GoalRunner | None = None
        self._run_worker: Any = None
        self._stopping = False
        #: Set after a hard cancel. The device may have moved under an action
        #: that was torn off, so the screen on display is not to be trusted.
        self._stale = False
        self._last_progress_at = 0.0
        #: One backend for a whole manual session, not one per command. A
        #: fresh one each time would restart the counters, so the numbers on
        #: screen would show the last command's cost rather than the session's
        #: and a manual detour would never show up in the total.
        self._manual_backend: Any = None
        #: Device-startup lines, kept here rather than only in the widget.
        #: A `RichLog` that is not displayed has zero width and drops what it
        #: is told to write, so a pane hidden by default would be empty the
        #: first time it was opened, which is the only time it matters.
        self._startup: list[str] = []

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Horizontal(id="panes"):
            with Vertical(id="left"):
                yield Transcript()
                yield Thinking()
            yield ScreenPane(id="screen-pane")
        yield LogPane()
        yield StatsBar(id="stats-bar")
        yield GoalInput()
        yield Footer()

    def on_mount(self) -> None:
        if self.inline_mode:
            self.screen.add_class("inline")
        self.query_one(Thinking).display = False
        self.query_one("#log-pane").display = False
        self.set_interval(1.0, self._tick)
        self.consume_events()
        self.begin()

    @property
    def transcript(self) -> Transcript:
        """By id, never by type.

        `LogPane` used to subclass `Transcript`, and `query_one(Transcript)`
        matched both and returned the hidden one, so every line written here
        went somewhere nobody could see. The id is the identity.
        """
        return self.query_one("#transcript", Transcript)

    # -- the event pump ----------------------------------------------------

    @work(exclusive=True, group="events")
    async def consume_events(self) -> None:
        while True:
            for event in await drain(self._queue):
                try:
                    self._apply(event)
                except Exception as exc:
                    # A pane that cannot draw must not take down a run that is
                    # working. Say so and carry on.
                    self.transcript.note(f"display error: {exc!r}", "red")

    def _apply(self, event: Event) -> None:
        status = self.query_one(StatusBar)
        transcript = self.transcript

        match event:
            case Progress(text=text):
                self._last_progress_at = event.at
                self._startup.append(text)
                log = self.query_one("#log-pane", LogPane)
                if log.display:
                    log.write(text)
                status.state = "starting"
            case DeviceReady(lease=lease):
                device = dict(lease).get("device") or {}
                name = device.get("name", "?") if isinstance(device, dict) else "?"
                version = device.get("os_version", "?") if isinstance(device, dict) else "?"
                kind = device.get("kind", "?") if isinstance(device, dict) else "?"
                status.device = f"{name} · iOS {version} · {kind}"
                status.state = "ready"
            case GoalStarted(goal=goal, model=model):
                status.model = model
                status.state = "working"
                transcript.goal(goal)
            case ModelDelta(text=text):
                self.query_one(Thinking).add(text)
            case ModelTurn(text=text):
                self.query_one(Thinking).take()
                transcript.said(text)
            case Observed():
                transcript.observed(event)
                self.query_one(StatsBar).stats = event.stats
            case ActionFinished():
                transcript.acted(event)
                self.query_one(StatsBar).stats = event.stats
                if not event.screen_refreshed and not event.refused:
                    # The phone moved but handed back a delta, so the pane is
                    # now showing a screen it has left. Saying so beats
                    # displaying it as if it were current.
                    self.query_one(ScreenPane).overtaken()
            case ScreenUpdated(text=text):
                self.query_one(ScreenPane).show(text)
            case Stopping(reason=reason):
                status.state = "stopping"
                transcript.note(f"stopping: {reason}. Escape again to abort.", "yellow")
            case ApprovalAsked():
                transcript.note("waiting for you to approve an action", "yellow")
            case GoalFinished():
                transcript.finished(event)
                bar = self.query_one(StatsBar)
                bar.stats = event.stats
                bar.prompt_tokens = event.prompt_tokens
                bar.completion_tokens = event.completion_tokens
                bar.elapsed_s = event.elapsed_s
                status.state = "stale" if self._stale else "ready"
            case Failed(where=where, message=message):
                transcript.note(f"failed during {where}: {message}", "red")
                status.state = "failed"
            case _:
                return

    def _tick(self) -> None:
        """The only thing the app invents rather than being told.

        `acquire` is silent for minutes and the seam does not fake heartbeats,
        so the elapsed number is computed here, where it is presentation.
        """
        status = self.query_one(StatusBar)
        if status.state == "starting" and self._last_progress_at:
            status.waiting_for = monotonic() - self._last_progress_at

    # -- getting a device --------------------------------------------------

    @work(group="lifecycle")
    async def begin(self) -> None:
        loop = asyncio.get_running_loop()
        self._last_progress_at = monotonic()
        self.runner = self._runner_factory(QueueSink(self._queue, loop))
        assert self.runner is not None
        try:
            await self.runner.start()
        except Exception:
            # Already reported as a `Failed` event by the runner itself.
            return
        if self._first_goal:
            self.submit(self._first_goal)
        else:
            self.query_one(GoalInput).focus()

    # -- running a goal ----------------------------------------------------

    def on_input_submitted(self, event: GoalInput.Submitted) -> None:
        goal = event.value.strip()
        if not goal:
            return
        event.input.value = ""
        self.submit(goal)

    def submit(self, goal: str) -> None:
        if self.runner is None or self._run_worker is not None:
            return
        self._stopping = False
        self._run_worker = self.run_manual(goal) if self.manual_mode else self.run_one(goal)

    @work(group="run")
    async def run_manual(self, line: str) -> None:
        """One typed command, straight to the device.

        Approval arrives differently here and this is the one place that cannot
        reuse the agent's plumbing. In the agent path the tool layer turns
        `ActionRequiresApproval` into a graph `interrupt()`; by hand there is no
        graph, so the exception surfaces raw and has to be caught, asked about,
        and the call retried once consent is recorded. Same modal, same
        signature-scoped consent, different route to it.
        """
        assert self.runner is not None
        self.transcript.goal(line)
        try:
            await self._one_command(line)
        finally:
            # Without this the app accepts one command and then silently
            # ignores every one after it, because `submit` refuses to start
            # while a run is in flight.
            self._run_worker = None

    async def _one_command(self, line: str) -> None:
        from ios_mcp.errors import ActionRequiresApproval, IosAutomationError
        from ios_tui.manual import Unknown, parse

        assert self.runner is not None and self.runner.session is not None
        transcript = self.transcript
        try:
            command = parse(line)
        except Unknown as usage:
            # Say why the grammar appeared. Printing it alone reads as though
            # the command worked and produced a list.
            transcript.note(f"I do not understand {line.split(' ')[0]!r}. What I know:", "yellow")
            for row in str(usage).splitlines():
                transcript.note(row)
            return

        backend = self._backend_for_manual()
        try:
            await command.run(backend)
        except ActionRequiresApproval as needs:
            signature = str((needs.details or {}).get("signature", ""))
            verdict = (needs.details or {}).get("verdict") or {}
            allowed = await self.push_screen_wait(
                ApprovalModal(
                    {
                        "action": command.verb,
                        "signature": signature,
                        "reason": verdict.get("reason") or str(needs),
                        "goal": line,
                    }
                )
            )
            if not allowed:
                transcript.note("refused, so it did not run", "yellow")
                return
            backend.approve(signature)
            await command.run(backend)
        except IosAutomationError as exc:
            transcript.note(f"{command.verb} failed ({exc.code}): {exc}", "red")
            if exc.hint:
                transcript.note(f"hint: {exc.hint}")

    @work(group="run")
    async def run_one(self, goal: str) -> None:
        assert self.runner is not None
        try:
            await self.runner.run(
                goal,
                approve=self._ask if self._approve else None,
                max_steps=self._max_steps,
            )
        except asyncio.CancelledError:
            self._stale = True
            self.transcript.note(
                "aborted mid-action. The screen below may be out of date; "
                "press ctrl+r to re-read it (costs one observation).",
                "red",
            )
            self.query_one(StatusBar).state = "stale"
            raise
        except Exception:
            pass  # already reported as a `Failed` event
        finally:
            self._run_worker = None
            self._stopping = False

    def _backend_for_manual(self) -> Any:
        """The one backend a manual session accumulates its cost in."""
        if self._manual_backend is None:
            from ios_agent import SessionBackend

            from ios_tui.stream import EventBackend

            assert self.runner is not None and self.runner.session is not None
            loop = asyncio.get_running_loop()
            self._manual_backend = EventBackend(
                SessionBackend(self.runner.session), QueueSink(self._queue, loop)
            )
        return self._manual_backend

    async def _ask(self, request: dict[str, Any]) -> bool:
        """Ask in a modal, and wait.

        `push_screen_wait` may only be awaited from a worker. The run already
        executes in one, and this is called from inside it, so the constraint
        holds; asserting it here beats discovering it on a real phone with a
        Send button on screen.
        """
        from textual.worker import get_current_worker

        get_current_worker()  # raises outside a worker
        return await self.push_screen_wait(ApprovalModal(request))

    # -- stopping ----------------------------------------------------------

    def action_stop(self) -> None:
        if self.runner is None or self._run_worker is None:
            return
        if not self._stopping:
            self._stopping = True
            self.runner.stop()
            return
        # Second press. The graceful path did not get there in time, which
        # means an action is in flight; tearing that off is the only lever
        # left, and it is not free.
        self._run_worker.cancel()

    @work(group="run")
    async def action_reread(self) -> None:
        """One observation, to find out where the phone actually is."""
        if self.runner is None or self.runner.session is None or self._run_worker is not None:
            return

        backend = self._backend_for_manual()
        await backend.observe()
        self._stale = False
        self.query_one(StatusBar).state = "ready"

    def action_save(self) -> None:
        """Write the audit trail out.

        The trail rather than the transcript, because the transcript is what
        the front end drew and the trail is what the device was actually asked
        to do: sequence, target, resolution tier, whether the screen changed,
        how long it took. That is the artefact worth keeping, and `AuditTrail`
        already knows how to serialise itself.
        """
        if self.runner is None or self.runner.session is None:
            return
        session = self.runner.session
        path = session.settings.artifacts_dir / "tui" / f"session-{int(time()):d}.json"
        try:
            written = session.audit.write(path)
        except OSError as exc:
            self.transcript.note(f"could not save: {exc}", "red")
            return
        self.transcript.note(f"saved {len(session.audit.entries)} steps to {written}")

    def action_toggle_log(self) -> None:
        pane = self.query_one("#log-pane", LogPane)
        pane.display = not pane.display
        if pane.display:
            # Written now rather than as they arrived, because a hidden
            # `RichLog` has no width and silently discards them.
            pane.clear()
            for line in self._startup:
                pane.write(line)

    async def action_quit(self) -> None:
        if self.runner is not None:
            self.runner.stop("quitting")
        if self._run_worker is not None:
            self._run_worker.cancel()
        await self._release()
        self.exit(0)

    async def _release(self) -> None:
        """Give the device back, whatever else happened.

        Shielded because the quit path is itself a cancellation, and bounded
        because a release that hangs would leave a person unable to exit their
        own terminal. A runner left behind holds the device and the next run
        times out waiting for it, so the failure is worth naming out loud.
        """
        if self.runner is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.runner.close()), timeout=15)
        except (TimeoutError, asyncio.CancelledError):
            self.exit(
                1,
                message="Timed out releasing the device. A WebDriverAgent runner may still "
                "be holding it; `ios-agent doctor` will say.",
            )
