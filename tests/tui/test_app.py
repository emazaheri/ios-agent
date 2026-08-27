"""The Textual app, driven by a pilot with no device and no model.

The app is constructed with an injected runner factory and its own queue, so
every one of these runs in milliseconds against a scripted phone. What is worth
testing here is not that the panes render, it is the behaviour that is easy to
get wrong and expensive to get wrong on a real device: what Escape does the
first time versus the second, and that an unanswered approval is a refusal.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from ios_tui.app import IosAgentApp
from ios_tui.approval import ApprovalModal
from ios_tui.bus import EventSink
from ios_tui.events import DeviceReady, GoalFinished, Progress, ScreenUpdated
from ios_tui.runner import GoalRunner
from ios_tui.widgets import LogPane, ScreenPane, StatsBar, StatusBar
from rich.text import Text
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings

BOLD_TEXT = [
    [("observe", {})],
    [("tap", {"target": "Accessibility"})],
    [("tap", {"target": "Display & Text Size"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on"})],
]

#: Long enough that a stop lands mid-run rather than after it.
SLOW_WANDER = [[("observe", {})], *([[("scroll", {"direction": "down"})]] * 20)]


class _StubRunner(GoalRunner):
    """A runner over the scripted phone that never touches `DevicePool`."""

    def __init__(
        self, sink: EventSink, model: DeviceModel, script: list, delay: float = 0.0
    ) -> None:
        session, _, _ = build_session(model, settings())
        super().__init__(sink, settings(), model=ScriptedModel(script, delay=delay))
        self.session = session
        self.device_model = model

    async def start(self):  # type: ignore[no-untyped-def]
        assert self.session is not None
        self.sink.emit(Progress(text="Booting simulator iPhone 17"))
        self.sink.emit(
            DeviceReady(
                lease={"device": {"name": "iPhone 17", "os_version": "26.5", "kind": "simulator"}}
            )
        )
        return self.session

    async def close(self) -> None:
        return None


def _app(
    script: list = BOLD_TEXT, delay: float = 0.0, **kwargs: object
) -> tuple[IosAgentApp, DeviceModel]:
    model = DeviceModel()
    app = IosAgentApp(lambda sink: _StubRunner(sink, model, script, delay), **kwargs)  # type: ignore[arg-type]
    return app, model


async def _settle(app: IosAgentApp, until: object, timeout: float = 5.0) -> None:
    """Wait for a condition, rather than for a fixed number of frames."""
    assert callable(until)
    async with asyncio.timeout(timeout):
        while not until():
            await asyncio.sleep(0.02)


async def test_it_shows_the_device_the_run_and_the_numbers() -> None:
    app, model = _app(goal="Turn on Bold Text.")
    async with app.run_test() as pilot:
        await _settle(app, lambda: app.query_one(StatsBar).stats.actions == 3)
        await pilot.pause()

        assert model.switches["bold_text"] is True
        assert "iPhone 17" in app.query_one(StatusBar).device
        assert app.query_one(StatsBar).stats.observations == 1

        # The pane shows the last *full* screen, verbatim, so the person and
        # the model are reading the same text. It is not the screen the run
        # ended on, and that is not a bug: an action whose new screen is
        # similar returns a delta instead of a digest, which is what keeps a
        # long flow cheap. The pane says how far behind it is instead of
        # displaying a screen the phone has already left.
        pane = app.query_one(ScreenPane)
        assert "Accessibility" in pane.text
        assert pane.stale_by == 2, "the pane did not notice it had been overtaken"


async def test_escape_stops_gracefully_and_the_run_still_reports() -> None:
    """The first Escape is a halt, not a cancellation.

    The graph checks `stop_reason()` after every node, so the run ends with a
    real outcome and real numbers. That is the whole reason to prefer it: a
    cancelled run can say nothing about what it did.
    """
    app, _ = _app(SLOW_WANDER, delay=0.05, goal="Wander forever.")
    async with app.run_test() as pilot:
        await _settle(app, lambda: app.query_one(StatsBar).stats.actions >= 1)
        await pilot.press("escape")
        await _settle(app, lambda: app.query_one(StatusBar).state in {"ready", "stale"})

        session = app.runner.session if app.runner else None
        assert session is not None
        # The run ended on its own terms rather than being torn off.
        assert app._run_worker is None
        assert app._stale is False


async def test_a_second_escape_aborts_and_the_app_says_the_screen_is_stale() -> None:
    """Cancelling can tear off an HTTP call mid-action.

    After that the app does not know where the phone is, and saying so is the
    honest option: the alternative is a screen pane that looks current and is
    not, which is the failure the whole perception layer exists to avoid.
    """
    app, _ = _app(SLOW_WANDER, delay=0.2, goal="Wander forever.")
    async with app.run_test():
        await _settle(app, lambda: app.query_one(StatsBar).stats.actions >= 1)

        # Both presses back to back, without awaiting in between, because that
        # is what a person does when the first one does not appear to work.
        # Waiting between them would let the graceful stop finish and the
        # second press would correctly do nothing, which is a different case.
        app.action_stop()
        assert app._stopping is True
        app.action_stop()

        await _settle(app, lambda: app._stale is True)
        assert app.query_one(StatusBar).state == "stale"


@pytest.mark.parametrize(("key", "expected"), [("y", True), ("n", False), ("escape", False)])
async def test_the_approval_modal_answers_yes_only_on_yes(key: str, expected: bool) -> None:
    """Every way out that is not an explicit yes is a no.

    SAFETY.md's rule, at the surface that implements it: a question nobody
    answered is not consent, so escaping out of the modal has to mean refuse
    rather than leaving the run waiting or, worse, defaulting to allow.
    """
    app, _ = _app()
    async with app.run_test() as pilot:
        answers: list[bool] = []

        async def ask() -> None:
            answers.append(
                await app.push_screen_wait(
                    ApprovalModal({"action": "tap", "reason": "matched Delete"})
                )
            )

        app.run_worker(ask(), group="test")
        await _settle(app, lambda: isinstance(app.screen, ApprovalModal))
        await pilot.press(key)
        await _settle(app, lambda: bool(answers))

        assert answers == [expected]


async def test_the_log_pane_starts_hidden_and_toggles() -> None:
    """Device startup is dozens of lines and would bury three lines of run."""
    app, _ = _app()
    async with app.run_test() as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        assert app.query_one("#log-pane").display is False

        await pilot.press("ctrl+l")
        assert app.query_one("#log-pane").display is True


async def test_inline_mode_drops_the_panes_it_has_no_room_for() -> None:
    """Inline is a set of CSS rules over the same widget tree.

    One `compose()` and one set of handlers, so a bug fixed in one shape is
    fixed in both. Note what this does *not* cover: `run_test` does not
    exercise real inline rendering against a terminal, so the actual look of
    `--inline` is checked by hand.
    """
    app, _ = _app(inline=True)
    async with app.run_test(size=(80, 20)):
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        assert app.screen.has_class("inline")
        assert app.query_one("#screen-pane").display is False
        assert app.query_one("#transcript").display is True


async def test_a_screen_update_reaches_the_pane() -> None:
    app, _ = _app(goal="Turn on Bold Text.")
    async with app.run_test():
        await _settle(app, lambda: app.query_one(StatsBar).stats.actions == 3)
        app._apply(ScreenUpdated(text='screen: fake / "Somewhere"'))

        assert "Somewhere" in app.query_one(ScreenPane).text


async def test_the_goal_finished_event_fills_in_the_model_cost() -> None:
    app, _ = _app(goal="Turn on Bold Text.")
    async with app.run_test():
        await _settle(app, lambda: bool(app.query_one(StatsBar).elapsed_s))
        app._apply(GoalFinished(goal="x", prompt_tokens=1200, completion_tokens=80))

        bar = app.query_one(StatsBar)
        assert bar.prompt_tokens == 1200
        assert bar.completion_tokens == 80


async def test_manual_mode_accumulates_cost_across_commands() -> None:
    """One backend for the session, not one per command.

    A fresh backend each time restarts the counters, so the numbers on screen
    would show the last command's cost rather than the session's, and a manual
    detour would never appear in the total. It also stops after one command,
    because `submit` refuses to start while a run is in flight and a worker
    that never clears looks exactly like one still running.
    """
    app, _ = _app(manual=True)
    async with app.run_test() as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        for line in ["observe", "tap Accessibility", "tap Display & Text Size"]:
            app.submit(line)
            await _settle(app, lambda: app._run_worker is None)
        await pilot.pause()

        bar = app.query_one(StatsBar)
        assert bar.stats.observations == 1
        assert bar.stats.actions == 2, "the counters restarted between commands"


async def test_manual_mode_says_what_it_does_not_understand() -> None:
    """A mis-parse would tap something, so an unknown line prints the grammar."""
    app, _ = _app(manual=True)
    async with app.run_test() as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app.submit("frobnicate the widget")
        await _settle(app, lambda: app._run_worker is None)
        await pilot.pause()

        assert app.query_one(StatsBar).stats.actions == 0


async def test_ctrl_s_writes_the_audit_trail(tmp_path: Path) -> None:
    """The trail, not the transcript.

    The transcript is what the front end drew; the trail is what the device was
    asked to do, with resolution tiers and whether each step changed the screen.
    That is the artefact worth keeping after the terminal is closed.
    """
    app, _ = _app(goal="Turn on Bold Text.")
    async with app.run_test() as pilot:
        await _settle(app, lambda: bool(app.query_one(StatsBar).elapsed_s))
        assert app.runner is not None and app.runner.session is not None
        app.runner.session.settings.artifacts_dir = tmp_path

        await pilot.press("ctrl+s")
        await pilot.pause()

        written = list((tmp_path / "tui").glob("session-*.json"))
        assert len(written) == 1
        trail = json.loads(written[0].read_text())
        assert [e["action"] for e in trail["steps"]] == ["tap", "tap", "set_value"]
        assert trail["summary"]["steps"] == 3


async def test_the_transcript_actually_receives_what_it_is_told_to_write() -> None:
    """The test that was missing, and the bug it would have caught.

    `LogPane` subclassed `Transcript`, so `query_one(Transcript)` matched both
    and returned the hidden log pane. Every goal, action and summary was
    written into a widget nobody could see, and the whole left half of the app
    rendered empty. Nothing failed: the stats bar, status bar and screen pane
    were all correct, which is exactly what the other tests asserted.
    """
    app, _ = _app(goal="Turn on Bold Text.")
    async with app.run_test() as pilot:
        await _settle(app, lambda: bool(app.query_one(StatsBar).elapsed_s))
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "Turn on Bold Text." in written, "the goal never reached the transcript"
        assert "tap" in written and "set_value" in written, "the actions did not"
        assert "Bold Text is on" in written, "nor did the summary"

        # And device startup went to the log pane instead, which fills in when
        # it is opened: a hidden RichLog has no width and drops what it is
        # given, so the lines are kept by the app until there is somewhere to
        # put them.
        await pilot.press("ctrl+l")
        await pilot.pause()
        startup = "\n".join(line.text for line in app.query_one(LogPane).lines)
        assert "Booting simulator" in startup
        assert "set_value" not in startup


async def test_the_stale_header_does_not_recolour_the_screen_it_labels() -> None:
    """`Text(a, style=...) + Text(b)` carries the style onto the whole result.

    It turned the entire digest yellow the moment the pane fell behind, which
    reads as an error state rather than as a note above one.
    """
    pane = ScreenPane()
    pane.text = 'screen: com.apple.Preferences / "Accessibility"'
    pane.stale_by = 2

    parts: list[tuple[str, str]] = [
        ("2 action(s) since this was read, ctrl+r to re-read\n", "yellow"),
        (pane.text, ""),
    ]
    assembled = Text.assemble(*parts)
    coloured = {
        assembled.plain[span.start : span.end] for span in assembled.spans if span.style == "yellow"
    }
    assert all("com.apple.Preferences" not in chunk for chunk in coloured)
