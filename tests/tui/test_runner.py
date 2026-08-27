"""What survives between goals, and what a stop does.

The interesting assertions here are about state that is deliberately *not*
carried. A front end that keeps one session alive across many goals inherits
every piece of session-scoped bookkeeping, and some of that bookkeeping is
scoped to a goal instead. Getting it wrong is quiet: the second goal simply
refuses to touch the device and reports that it finished.

Every test drives `GoalRunner.run`, with the model injected the way `run_goal`
takes one, so what is under test is the real path rather than a copy of it.
"""

from __future__ import annotations

from ios_tui.bus import ListSink
from ios_tui.events import GoalFinished, GoalStarted, Stopping
from ios_tui.runner import GoalRunner, tuned
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings

BOLD_TEXT = [
    [("observe", {})],
    [("tap", {"target": "Accessibility"})],
    [("tap", {"target": "Display & Text Size"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on"})],
]

#: The same goal asked a second time, from where the first one left the phone.
#: A second goal does not start from the home screen, because nothing reset the
#: device: the session is still on Display & Text Size. That is the point of
#: keeping one session across goals, and it is why the route is shorter.
BOLD_TEXT_AGAIN = [
    [("observe", {})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on again"})],
]

#: Reaches the switch, sets it, then sets it again three more times. Each
#: repeat changes nothing, which is exactly what the verifier counts: after
#: `Verifier.max_attempts` no-ops on one attempt key, the next attempt at that
#: key is refused before the device is touched.
BOLD_TEXT_UNTIL_EXHAUSTED = [
    [("observe", {})],
    [("tap", {"target": "Accessibility"})],
    [("tap", {"target": "Display & Text Size"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    # Three no-ops fill the ledger; the fifth attempt is the one refused, since
    # `check` runs before the action rather than after it.
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on"})],
]


def _runner(sink: ListSink, script: list) -> tuple[GoalRunner, DeviceModel]:
    """A runner over the scripted phone, with the pool bypassed entirely."""
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    runner = GoalRunner(sink, settings(), model=ScriptedModel(script))
    runner.session = session
    return runner, model


async def test_it_drives_a_goal_and_says_what_happened() -> None:
    sink = ListSink()
    runner, model = _runner(sink, BOLD_TEXT)

    outcome = await runner.run("Turn on Bold Text.")

    assert model.switches["bold_text"] is True
    assert outcome.succeeded is True
    assert [e.goal for e in sink.of_type(GoalStarted)] == ["Turn on Bold Text."]

    finished = sink.of_type(GoalFinished)
    assert len(finished) == 1
    assert finished[0].stats.actions == outcome.stats.actions == 3
    assert finished[0].stats.observations == 1
    assert finished[0].elapsed_s >= 0.0


async def test_a_halt_does_not_outlive_the_goal_that_caused_it() -> None:
    """Stopping one goal must not kill the session.

    `session.halt()` sets a flag the policy gate reads before every action, so
    without a `resume()` at the top of each run the first stop would make every
    later goal fail before touching the device, and report that it finished.
    """
    sink = ListSink()
    runner, model = _runner(sink, BOLD_TEXT)
    session = runner.session
    assert session is not None

    runner.stop()
    assert session.halted is True
    assert [e.reason for e in sink.of_type(Stopping)] == ["you asked it to stop"]

    outcome = await runner.run("Turn on Bold Text.")

    assert session.halted is False, "resume() did not run at the top of the goal"
    assert outcome.succeeded is True
    assert model.switches["bold_text"] is True


async def test_a_verifier_exhausted_by_one_goal_does_not_block_the_next() -> None:
    """The verifier is scoped to a goal, not to the session.

    Its ledger counts repeats of one attempt key that changed nothing, and
    after `max_attempts` of them it refuses that key outright. Within a goal
    that is exactly right: it is what stops an agent hammering a dead control.

    Carried across goals it is wrong, and quietly so. The next goal to touch
    that same control is refused before the device is touched, and the run
    reports success having done nothing. A person asking a second time is new
    intent, not the same agent still stuck.
    """
    sink = ListSink()
    runner, _ = _runner(sink, BOLD_TEXT_UNTIL_EXHAUSTED)

    first = await runner.run("Turn on Bold Text.")
    assert first.stats.refusals >= 1, (
        "the first goal was meant to exhaust the verifier on this key; "
        "if it did not, this test proves nothing about the second"
    )

    # A fresh prefix, because a real provider issues new call ids per call and
    # the idempotency cache is session-scoped: reusing them would make this
    # goal replay from the cache instead of touching the device.
    runner._model = ScriptedModel(BOLD_TEXT_AGAIN, prefix="second")
    second = await runner.run("Turn on Bold Text.")

    assert second.stats.refusals == 0, (
        "the second goal was refused on a ledger the first goal filled up"
    )
    assert second.stats.actions == 1, "the action never reached the device"


async def test_each_goal_reports_its_own_cost_rather_than_the_session_total() -> None:
    """The natural-looking wrong answer is one backend for the whole session.

    It produces a quiet bug: every goal after the first reports the running
    total, so the numbers this project is judged on drift upward for reasons
    that have nothing to do with the agent.
    """
    sink = ListSink()
    runner, model = _runner(sink, BOLD_TEXT)

    first = await runner.run("Turn on Bold Text.")
    model.switches["bold_text"] = False
    runner._model = ScriptedModel(BOLD_TEXT_AGAIN, prefix="second")
    second = await runner.run("Turn on Bold Text.")

    # Three actions then one, not three then four. A shared backend would
    # report the second goal as having cost everything the session has cost.
    assert first.stats.actions == 3
    assert second.stats.actions == 1
    assert second.stats.observations == 1
    assert [e.stats.actions for e in sink.of_type(GoalFinished)] == [3, 1]


async def test_the_screen_carries_across_goals_even_though_the_backend_does_not() -> None:
    """A new goal should not blank the pane showing where the phone is."""
    sink = ListSink()
    runner, _ = _runner(sink, BOLD_TEXT)

    await runner.run("Turn on Bold Text.")
    carried = runner.last_screen

    assert carried, "nothing was carried from the first goal"
    runner._model = ScriptedModel([[("done", {"succeeded": True, "summary": "nothing to do"})]])
    await runner.run("Do nothing.")
    assert runner.last_screen == carried


def test_tuned_only_ever_raises_a_ceiling() -> None:
    """A phone needs the headroom and a simulator is not charged for it."""
    cfg = tuned(settings())
    assert cfg.stabilize.max_wait_s >= 20.0
    assert cfg.wda.startup_timeout_s >= 300.0

    already_higher = settings()
    already_higher.stabilize.max_wait_s = 45.0
    assert tuned(already_higher).stabilize.max_wait_s == 45.0
