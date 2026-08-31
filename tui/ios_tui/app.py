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
from textual.command import Provider
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer

from ios_mcp.errors import DeviceUnavailable
from ios_tui.approval import ApprovalModal
from ios_tui.bus import EventSink, QueueSink, drain
from ios_tui.commands import AppCommands, Command, matching
from ios_tui.devices import DevicePicker
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
    StatsSnapshot,
    Stopping,
)
from ios_tui.runner import GoalRunner
from ios_tui.widgets import (
    GoalInput,
    LogPane,
    ScreenPane,
    SlashMenu,
    StatsBar,
    StatusBar,
    Thinking,
    Transcript,
)

#: The checks a simulator needs, in the order someone would fix them.
#:
#: Shown first when nothing can be driven, because the simulator is the path
#: that needs no Apple Developer account and is what a first run will use.
#: Without this the list led with a stopped RemoteXPC tunnel, which matters
#: only for driving a physical phone over USB and was not what was blocking.
_SIMULATOR_FIRST = ("xcode", "simctl", "wda-bundle")


def _worst_first(checks: list[Any]) -> list[Any]:
    """Blocking failures first, then whatever the simulator path needs."""

    def rank(check: Any) -> tuple[int, int]:
        blocking = 0 if check.status == "fail" else 1
        relevance = (
            _SIMULATOR_FIRST.index(check.name) if check.name in _SIMULATOR_FIRST else len(
                _SIMULATOR_FIRST
            )
        )
        return (blocking, relevance)

    return sorted(checks, key=rank)


class IosAgentApp(App[int]):
    """One device, a transcript, and the numbers."""

    CSS_PATH = "theme.tcss"
    TITLE = "ios-agent"
    #: `ctrl+p` offers the same list `/` does, from the same registry.
    COMMANDS: ClassVar[set[type[Provider] | Callable[[], type[Provider]]]] = {AppCommands}

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "stop", "Stop", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+l", "toggle_log", "Log", show=True),
        # Not ctrl+d, which would have been the obvious mnemonic: `Input`
        # binds it to `delete_right`, so with the goal box focused (which is
        # always) the binding never fires. `/device` is the discoverable form;
        # this is the shortcut.
        Binding("ctrl+o", "device", "Device", show=True),
        Binding("ctrl+r", "reread", "Re-read screen", show=False),
        Binding("ctrl+s", "save", "Save trail", show=False),
        # Only while the slash menu is open; `check_action` disables them
        # otherwise so the arrow keys stay free for the input.
        Binding("down", "menu_down", "Next command", show=False),
        Binding("up", "menu_up", "Previous command", show=False),
        # Priority, because tab is Textual's focus-next and would otherwise
        # move focus out of the input before this is ever consulted.
        # `check_action` still hands it back when the menu is closed.
        Binding("tab", "menu_complete", "Complete", show=False, priority=True),
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
        pick: bool = False,
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
        #: Ask which device before acquiring one.
        self.pick = pick

        self.runner: GoalRunner | None = None
        self._run_worker: Any = None
        #: Whether a goal or command is in flight. Separate from
        #: `_run_worker`, which is the handle used to cancel one: the flag is
        #: raised before the worker starts so a fast worker cannot finish
        #: between the two.
        self._busy = False
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
        yield SlashMenu()
        yield StatsBar(id="stats-bar")
        yield GoalInput(manual=self.manual_mode)
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
                if not self.is_running:
                    # Quitting. Events keep arriving after the screen is gone,
                    # a `Failed` from releasing the device most of all, and
                    # drawing one then is both impossible and pointless.
                    return
                try:
                    self._apply(event)
                except Exception as exc:
                    self._complain(f"display error: {exc!r}")

    def _complain(self, message: str) -> None:
        """Report a drawing failure without assuming anything can be drawn.

        The obvious handler writes to the transcript, and the transcript is a
        widget: during teardown it is the very thing that has gone, so the
        handler raised the same error it was catching and took the worker down
        with a traceback. An error path that depends on the thing that failed
        is not an error path.
        """
        try:
            self.transcript.note(message, "red")
        except Exception:
            self.log.warning(message)

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

        if not await self._check_the_machine():
            return
        if not self._preflight():
            return

        if self.pick and not await self._choose_device():
            self.exit(0)
            return

        try:
            await self.runner.start()
        except DeviceUnavailable as exc:
            # A named device that does not resolve is a question, not a dead
            # end: the answer is on the list this screen shows, and the list
            # also carries the blockers explaining anything unusable.
            self.transcript.note(str(exc), "yellow")
            if not await self._choose_device():
                self.exit(1)
                return
            try:
                await self.runner.start()
            except Exception:
                self._no_device()
                return
        except Exception:
            # The failure itself is already a `Failed` event from the runner.
            # What it does not say is what to do next, and the app stays up
            # with a live input, so it has to.
            self._no_device()
            return
        if self._first_goal:
            self.submit(self._first_goal)
        else:
            self.query_one(GoalInput).focus()

    async def _check_the_machine(self) -> bool:
        """Is this Mac set up to drive anything at all?

        Without this the first run on a machine with no Xcode, or no
        WebDriverAgent build, spends its time acquiring a device and then fails
        with whatever the first missing tool happened to raise: one error, from
        one layer, describing one symptom of a setup that was never done.

        `run_doctor` already knows every requirement and carries a remedy for
        each, so this shows the ones that are not met rather than inventing a
        second, worse set of checks. About a second, against the minute a boot
        takes.

        Blocks on `fail` and on nothing being drivable. A warning does not
        stop anything: an expiring provisioning profile and a stopped tunnel
        are both real and neither prevents a simulator run.
        """
        from ios_mcp.devices.doctor import run_doctor

        assert self.runner is not None
        status = self.query_one(StatusBar)
        status.state = "starting"
        self._last_progress_at = monotonic()

        try:
            report = await run_doctor(self.runner.settings)
        except Exception as exc:
            # The check failing is not the same as the machine being unusable,
            # so this reports and continues rather than refusing to start on a
            # diagnostic that could not run.
            self.transcript.note(f"could not check the toolchain: {exc}", "yellow")
            return True

        blocking = [c for c in report.checks if c.status == "fail"]
        if not blocking and (report.can_use_simulator or report.can_use_real_device):
            for check in report.checks:
                if check.status == "warn" and check.remedy:
                    self.transcript.note(f"{check.name}: {check.detail}", "yellow")
            return True

        status.state = "failed"
        self.transcript.note("this Mac is not set up to drive a device yet.", "red")
        for check in _worst_first(blocking or [c for c in report.checks if c.status != "ok"]):
            self.transcript.note(f"{check.name}: {check.detail}", "red")
            if check.remedy:
                self.transcript.note(f"  {check.remedy}")
        self.transcript.note("`ios-agent doctor` prints this in full.")
        return False

    def _preflight(self) -> bool:
        """Check the model before spending a minute acquiring a device.

        Left until the first model turn, a missing key surfaces after a cold
        simulator has booted and WebDriverAgent has started: the cheapest check
        in the system running last, behind the most expensive setup.

        A warning does not stop anything. It means this project could not find
        a credential it knows the name of, which is the ordinary case for
        Bedrock, Vertex and an Anthropic CLI profile, and refusing to start on
        that would lock out every one of them.

        Skipped entirely in manual mode, which drives the device by hand and is
        meant to work with no provider configured at all.
        """
        if self.manual_mode:
            return True

        from ios_agent import probe_provider

        assert self.runner is not None
        probe = probe_provider(self.runner.agent)
        status = self.query_one(StatusBar)
        status.model = self.runner.agent.describe()

        if probe.status == "warn":
            # One line, not two. This fires on every start for anyone using
            # Bedrock, Vertex or a CLI profile, and a warning seen constantly
            # is a warning nobody reads.
            self.transcript.note(f"{probe.detail}; {probe.remedy}", "yellow")
        elif probe.status == "fail":
            status.state = "failed"
            self.transcript.note(f"no model: {probe.detail}", "red")
            if probe.remedy:
                self.transcript.note(probe.remedy)
            self.transcript.note("no device was acquired, so nothing was started.")
            return False
        return True

    async def _choose_device(self) -> bool:
        """Ask which device. False means the person declined to pick one."""
        assert self.runner is not None
        chosen = await self.push_screen_wait(DevicePicker(self.runner.settings))
        if chosen is None:
            return False
        self.runner.device = chosen
        self._last_progress_at = monotonic()
        return True

    @work(group="lifecycle")
    async def action_device(self) -> None:
        """Switch device, at any point, without leaving the app.

        Refused while a goal is running rather than interrupting one. Stopping
        mid-action and then taking the device away leaves the phone in a state
        nothing recorded, and `esc` already exists for stopping deliberately.
        """
        if self.runner is None or self._busy:
            self.transcript.note("finish or stop the current run first (esc)", "yellow")
            return

        current = self.runner.device
        chosen = await self.push_screen_wait(DevicePicker(self.runner.settings))
        if chosen is None or chosen == current:
            return

        status = self.query_one(StatusBar)
        status.state = "starting"
        self._last_progress_at = monotonic()
        self.transcript.note("switching device")
        try:
            await self.runner.switch(chosen)
        except Exception:
            # Reported as a `Failed` event by the runner. The old device is
            # already released at that point, so there is nothing to fall back
            # to and saying so is all that is left.
            self.transcript.note("no device attached. ctrl+d to choose another.", "red")
            return

        # Everything on screen described the device just released.
        self._manual_backend = None
        self._stale = False
        self.query_one(ScreenPane).show("")
        bar = self.query_one(StatsBar)
        bar.stats = StatsSnapshot()
        bar.prompt_tokens = bar.completion_tokens = 0
        bar.elapsed_s = 0.0
        self.query_one(GoalInput).focus()

    # -- running a goal ----------------------------------------------------

    # -- commands ----------------------------------------------------------

    def commands(self) -> list[Command]:
        """Everything the front end can be told to do.

        Built here rather than declared as a constant because each one closes
        over the app. One list feeds both the `/` menu and the command palette,
        so neither can offer something the other does not.
        """
        return [
            Command("device", "switch to another phone or simulator", self.action_device, "ctrl+o"),
            Command("screen", "read the screen again", self.action_reread, "ctrl+r"),
            Command("log", "show or hide the device startup log", self.action_toggle_log, "ctrl+l"),
            Command("save", "write the audit trail to .artifacts", self.action_save, "ctrl+s"),
            Command("stop", "stop the running goal", self.action_stop, "esc"),
            Command("quit", "release the device and exit", self._quit_later, "ctrl+q"),
        ]

    def _quit_later(self) -> None:
        """Quitting is async, because it releases the device before exiting."""
        self.run_worker(self.action_quit(), group="lifecycle")

    def on_input_changed(self, event: GoalInput.Changed) -> None:
        """A leading slash turns the box into a command line."""
        menu = self.query_one(SlashMenu)
        typed = event.value
        if not typed.startswith("/"):
            menu.hide()
            return
        menu.offer(matching(self.commands(), typed[1:]))

    def on_input_submitted(self, event: GoalInput.Submitted) -> None:
        typed = event.value.strip()
        if not typed:
            return
        menu = self.query_one(SlashMenu)

        if typed.startswith("/"):
            # The highlighted row, not the typed text: the menu is filtered by
            # what was typed, so its selection is the more specific answer and
            # is what the person is looking at.
            chosen = menu.chosen
            event.input.value = ""
            menu.hide()
            if chosen is None:
                self.transcript.note(
                    f"no command matching {typed!r}. Type / to see them.", "yellow"
                )
                return
            chosen.run()
            return

        event.input.value = ""
        menu.hide()
        self.submit(typed)

    def submit(self, goal: str) -> None:
        """Start one goal, or one typed command.

        The busy flag is raised *before* the worker starts, not from its
        return value. A worker that finishes quickly, which `help` and any
        parse error do, clears the flag in its own `finally` before the
        assignment here runs, and the stale handle then sits there forever
        refusing every later submission. The app looked frozen: it accepted
        one line and silently ignored the rest.
        """
        if self.runner is None or self._busy:
            return
        self._stopping = False
        self._busy = True
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
        if self.runner is None:
            return
        self.transcript.goal(line)
        try:
            await self._one_command(line)
        finally:
            # Every path out lowers the flag, including the early returns in
            # `_one_command` for `help`, a typo and a missing device.
            self._run_worker = None
            self._busy = False

    async def _one_command(self, line: str) -> None:
        from ios_mcp.errors import ActionRequiresApproval, IosAutomationError
        from ios_tui.manual import USAGE, Help, Unknown, parse

        transcript = self.transcript

        # Parsed before the device is looked for, so `help` and a typo still
        # answer when there is no device. Those are the two things a person
        # types when they are stuck, which is exactly the state a failed
        # acquire leaves them in.
        try:
            command = parse(line)
        except Help:
            for row in USAGE.splitlines():
                transcript.note(row)
            return
        except Unknown as unknown:
            # One line. The full grammar is what `help` is for, and dumping ten
            # rows for a typo buries the one thing that was wrong.
            transcript.note(str(unknown), "yellow")
            return

        if not self._has_device():
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
            # Inside the `try`, so the `finally` below still lowers the busy
            # flag. Returning above it would leave the app looking busy
            # forever, which is the bug this flag exists to prevent.
            if not self._has_device():
                return
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
            self._busy = False
            self._stopping = False

    def _no_device(self) -> None:
        """Say what to do next. The app is still running and still accepts
        input, so an error with no way forward is a dead end on a live app.

        The status is set here rather than left to the `Failed` event, so the
        header is honest about there being no device whether or not the
        failure happened to be reported as one.
        """
        self.query_one(StatusBar).state = "failed"
        self.transcript.note("Type /device or press ctrl+o to choose another.")
        self.query_one(GoalInput).focus()

    def _has_device(self) -> bool:
        """Whether there is something to act on, said rather than asserted.

        This was an `assert`, which is for invariants a programmer controls.
        A failed acquire is not that: it leaves the app running with a live
        input and no session, and the first thing typed took the whole app down
        with a traceback over the transcript that explained what had gone wrong.
        """
        if self.runner is not None and self.runner.session is not None:
            return True
        self.transcript.note(
            "no device is attached. Type /device or press ctrl+o to choose one.", "yellow"
        )
        return False

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
        if self.runner is None or self.runner.session is None or self._busy:
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

    # -- the slash menu ----------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Arrow keys and tab belong to the menu only while it is showing.

        Left always bound, they would swallow keys the input needs and Textual
        would show them in the footer as though they always did something.
        """
        if action in {"menu_down", "menu_up", "menu_complete"}:
            return self.query_one(SlashMenu).display
        return True

    def action_menu_down(self) -> None:
        self.query_one(SlashMenu).action_cursor_down()

    def action_menu_up(self) -> None:
        self.query_one(SlashMenu).action_cursor_up()

    def action_menu_complete(self) -> None:
        """Fill the typed name in, rather than running it.

        Completing and running are different intentions, and a tab that runs
        something is a tab that cannot be used to look before leaping.
        """
        chosen = self.query_one(SlashMenu).chosen
        if chosen is None:
            return
        box = self.query_one(GoalInput)
        box.value = f"/{chosen.name}"
        box.cursor_position = len(box.value)

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
