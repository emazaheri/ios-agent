"""The rule the front end is held to, asserted rather than argued.

Everything else in this project justified itself with a number. A terminal app
cannot, so it gets the one claim it *can* be held to: **watching a run must not
change it.** Same actions, same observations, same device tokens, and the same
sequence of things done to the device.

Asserted by equality, in the spirit of the agent eval oracles, so a wrapper
that quietly adds an observation fails here rather than drifting.
"""

from __future__ import annotations

from dataclasses import asdict

from ios_agent import SessionBackend, run_goal
from ios_agent.verify import Verifier
from ios_tui.bus import ListSink
from ios_tui.events import ActionFinished, ActionStarted, Failed, Observed, ScreenUpdated
from ios_tui.stream import EventBackend
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings

#: One route through the scripted phone, exercising an observation, two taps
#: and a switch: the shape every agent eval task is made of.
SCRIPT = [
    [("observe", {})],
    [("tap", {"target": "Accessibility"})],
    [("tap", {"target": "Display & Text Size"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on"})],
]


async def _run(*, watched: bool) -> tuple[dict[str, int], list[tuple[str, str, bool]], bool]:
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    inner = SessionBackend(session, Verifier())
    backend = EventBackend(inner, ListSink()) if watched else inner

    outcome = await run_goal(
        session, "Turn on Bold Text.", model=ScriptedModel(SCRIPT), backend=backend
    )

    trail = [(e.action, str(e.target or ""), e.ok) for e in session.audit.entries]
    return asdict(outcome.stats), trail, model.switches["bold_text"]


async def test_watching_a_run_does_not_change_what_it_costs() -> None:
    bare_stats, bare_trail, bare_effect = await _run(watched=False)
    watched_stats, watched_trail, watched_effect = await _run(watched=True)

    assert watched_stats == bare_stats, (
        "the front end changed what the run cost:\n"
        f"  bare    {bare_stats}\n  watched {watched_stats}"
    )
    assert watched_trail == bare_trail, (
        "the front end changed what happened to the device:\n"
        f"  bare    {bare_trail}\n  watched {watched_trail}"
    )
    # A guard against both sides being equal because neither did anything.
    assert bare_effect is True and watched_effect is True
    assert bare_stats["actions"] == 3 and bare_stats["observations"] == 1


async def test_the_wrapper_reports_every_call_it_passed_through() -> None:
    """Equality above would also hold for a wrapper that emitted nothing."""
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    sink = ListSink()
    backend = EventBackend(SessionBackend(session, Verifier()), sink)

    await run_goal(session, "Turn on Bold Text.", model=ScriptedModel(SCRIPT), backend=backend)

    assert [e.verb for e in sink.of_type(ActionStarted)] == ["tap", "tap", "set_value"]
    assert [e.verb for e in sink.of_type(ActionFinished)] == ["tap", "tap", "set_value"]
    assert len(sink.of_type(Observed)) == 1
    assert sink.of_type(ScreenUpdated), "the screen changed and nothing said so"


async def test_the_stats_on_an_event_are_a_snapshot_not_a_live_alias() -> None:
    """A transcript row must show what that step cost, not the session total.

    `BackendStats` is mutable and shared, so holding it would make every row
    re-render as the current numbers the moment anything else moved.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    sink = ListSink()
    backend = EventBackend(SessionBackend(session, Verifier()), sink)

    await run_goal(session, "Turn on Bold Text.", model=ScriptedModel(SCRIPT), backend=backend)

    finished = sink.of_type(ActionFinished)
    assert [e.stats.actions for e in finished] == [1, 2, 3], (
        "each row should carry the count as of that action"
    )
    assert backend.stats.actions == 3


async def test_an_action_that_raises_is_reported_as_an_action_not_a_dead_run() -> None:
    """A run that recovered three times looked like a run that died three times.

    `ElementAmbiguous` and a stale ref are handed to the model by
    `ios_agent.tools.guarded`, which says so plainly: raising would end the
    graph on a mistake fixable in one turn. The run they came from went on to
    succeed. Reporting them as failed runs told someone their session had
    collapsed while it was working.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    sink = ListSink()
    backend = EventBackend(SessionBackend(session, Verifier()), sink)

    # Nothing on this screen is called that, so resolution raises.
    await run_goal(
        session,
        "Tap something that is not there.",
        model=ScriptedModel([[("tap", {"target": "Nonexistent Control"})], []]),
        backend=backend,
    )

    assert not sink.of_type(Failed), "a recoverable error was reported as a failed run"

    errored = [e for e in sink.of_type(ActionFinished) if e.error]
    assert len(errored) == 1
    assert errored[0].verb == "tap"
    assert "Nonexistent Control" in errored[0].error


async def test_an_action_that_raises_is_not_counted_as_an_action() -> None:
    """It never reached the device, so the numbers must not say it did.

    This is the same rule the refusal counter follows, and it is why the run
    in the screenshot reported four actions for seven attempts.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    backend = EventBackend(SessionBackend(session, Verifier()), ListSink())

    await run_goal(
        session,
        "Tap something that is not there.",
        model=ScriptedModel([[("tap", {"target": "Nonexistent Control"})], []]),
        backend=backend,
    )

    assert backend.stats.actions == 0
