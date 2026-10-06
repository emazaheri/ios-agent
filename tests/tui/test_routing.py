"""Routing (ADR 0015) through the front end.

`run_goal` builds the small model from `route_model` only when it builds the
large one too. `ios-agent` streams by default, so it always injects the large
one, and `IOS_AGENT_ROUTE_MODEL` was silently ignored there. These pin both
halves: that a streamed run brings its small model, and that a routed run says
where it ran.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from ios_agent import AgentSettings
from ios_tui import cli
from ios_tui.bus import ListSink
from ios_tui.events import GoalFinished, GoalStarted
from ios_tui.printer import Printer
from ios_tui.runner import GoalRunner
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings

#: A tap on a row that is not there fails, which is trouble, so the second
#: turn is the large model's.
ESCALATES = (
    [[("tap", {"target": "A Row That Is Not There"})]],
    [[("done", {"succeeded": False, "summary": "no such row"})]],
)

STAYS = (
    [
        [("tap", {"target": "Accessibility"})],
        [("done", {"succeeded": True, "summary": "opened"})],
    ],
    [],
)


def _agent(**overrides: Any) -> AgentSettings:
    return AgentSettings(**{"route_model": "mini", **overrides})


async def _routed(script: tuple[list, list]) -> tuple[ListSink, ScriptedModel, ScriptedModel]:
    small_script, large_script = script
    small, large = ScriptedModel(small_script), ScriptedModel(large_script)
    session, _, _ = build_session(DeviceModel(), settings())
    sink = ListSink()
    runner = GoalRunner(sink, settings(), _agent(), model=large, route=small)
    runner.session = session
    await runner.run("Open something.")
    return sink, small, large


async def test_a_routed_run_reaches_the_small_model_and_says_where_it_moved() -> None:
    sink, small, large = await _routed(ESCALATES)

    assert small.turns == 1 and large.turns == 1, "the route never reached run_goal"
    (started,) = sink.of_type(GoalStarted)
    assert started.model.endswith(" route=mini")
    (finished,) = sink.of_type(GoalFinished)
    assert finished.routed_from == "mini"
    assert finished.large_model == _agent().model
    assert finished.escalated_at_turn == 1


async def test_a_routed_run_that_never_escalates_says_that_too() -> None:
    sink, small, large = await _routed(STAYS)

    assert small.turns == 2 and large.turns == 0
    (finished,) = sink.of_type(GoalFinished)
    assert finished.routed_from == "mini"
    assert finished.escalated_at_turn is None


async def test_an_injected_model_without_a_route_is_not_routed() -> None:
    """What `run_goal` does, and why the CLI has to pass the route itself."""
    session, _, _ = build_session(DeviceModel(), settings())
    sink = ListSink()
    model = ScriptedModel(STAYS[0])
    runner = GoalRunner(sink, settings(), _agent(), model=model)
    runner.session = session

    await runner.run("Open something.")

    (started,) = sink.of_type(GoalStarted)
    assert "route=" not in started.model
    (finished,) = sink.of_type(GoalFinished)
    assert finished.routed_from is None


# -- the CLI brings both models when it streams --------------------------------


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The model name every streaming factory was built for."""
    names: list[str] = []

    def fake(agent: AgentSettings, _sink: object) -> object:
        names.append(agent.model)
        return object()

    monkeypatch.setattr("ios_tui.stream.streaming_chat_model", fake)
    return names


def test_streaming_with_a_route_model_builds_the_small_one_too(built: list[str]) -> None:
    model, route = cli.streamed_models(_agent(), ListSink(), stream=True)

    assert model is not None and route is not None
    assert built == [_agent().model, "mini"]


def test_streaming_without_a_route_model_builds_one(built: list[str]) -> None:
    model, route = cli.streamed_models(_agent(route_model=None), ListSink(), stream=True)

    assert model is not None and route is None
    assert built == [_agent().model]


def test_not_streaming_leaves_both_to_run_goal(built: list[str]) -> None:
    """`run_goal` routes by itself when it builds the large model."""
    assert cli.streamed_models(_agent(), ListSink(), stream=False) == (None, None)
    assert built == []


# -- the plain printer -------------------------------------------------------


def _printed(event: GoalFinished) -> str:
    out = io.StringIO()
    Printer(out).emit(event)
    return out.getvalue()


def test_the_printer_splits_tokens_by_model_and_names_the_turn() -> None:
    text = _printed(
        GoalFinished(
            routed_from="mini",
            large_model="big",
            escalated_at_turn=3,
            tokens_by_model={"mini": (100, 10), "big": (900, 90)},
        )
    )
    assert "mini: 100 in / 10 out" in text
    assert "big: 900 in / 90 out" in text
    assert "turn 3" in text


def test_the_printer_says_nothing_about_routing_on_a_plain_run() -> None:
    assert "routed" not in _printed(GoalFinished(tokens_by_model={"big": (1, 1)}))
