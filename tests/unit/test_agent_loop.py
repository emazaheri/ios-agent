"""The loop's mechanics, driven by a scripted model.

A model in the loop makes a test slow, expensive and non-deterministic, and
none of those buy anything when the thing under test is the wiring: does a
tool call reach the device, does an idempotency key get attached, does the
graph stop when the session says to stop. Those deserve an ordinary unit test.

So the model is scripted. What is real is everything else: the LangGraph graph,
the tool definitions, `SessionBackend`, `IosSession`, the policy gate, and a
device whose screens respond to taps.

What is *not* tested here is whether a real model chooses well. That is what
`tests/evals/agent` measures, and it needs a real model to mean anything.
"""

from __future__ import annotations

import pytest
from ios_agent.backend import SessionBackend
from ios_agent.loop import run_goal
from langchain.messages import AIMessage, AnyMessage
from screens import DeviceModel, Injection, build_session

from ios_mcp.config import Settings


def _settings(*, confirm_destructive: bool = False) -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    cfg.policy.loop_detection_window = 50
    cfg.policy.confirm_destructive = confirm_destructive
    return cfg


class ScriptedModel:
    """Replays a fixed list of tool calls, one per turn.

    Records every message list it was handed, so a test can assert on what the
    agent was actually shown rather than on what it was meant to be shown.
    """

    def __init__(self, script: list[list[tuple[str, dict[str, object]]]]) -> None:
        self.script = script
        self.turns = 0
        self.seen: list[list[AnyMessage]] = []

    def __call__(self, _tools: list[object]):
        async def call(messages: list[AnyMessage]) -> AIMessage:
            self.seen.append(list(messages))
            if self.turns >= len(self.script):
                return AIMessage(content="out of script")
            calls = [
                {"name": name, "args": args, "id": f"call-{self.turns}-{i}", "type": "tool_call"}
                for i, (name, args) in enumerate(self.script[self.turns])
            ]
            self.turns += 1
            return AIMessage(content="", tool_calls=calls)  # type: ignore[arg-type]

        return call


def tool_replies(scripted: ScriptedModel) -> str:
    """Every tool reply the agent was shown, joined.

    Asserting on the last message alone is wrong once `done` has run: its
    reply is the last one, and the interesting result is the turn before.
    """
    return "\n".join(str(m.content) for m in scripted.seen[-1] if getattr(m, "type", "") == "tool")


async def test_the_loop_drives_a_task_to_completion() -> None:
    """End to end through the real graph, real tools and a real device model."""
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("observe", {})],
            [("tap", {"target": "Accessibility"})],
            [("tap", {"target": "Display & Text Size"})],
            [("set_value", {"value": "on", "target": "Bold Text"})],
            [("done", {"succeeded": True, "summary": "Bold Text is on"})],
        ]
    )

    outcome = await run_goal(session, "Turn on Bold Text.", model=scripted)

    assert model.switches["bold_text"] is True
    assert outcome.succeeded is True
    assert outcome.finished_cleanly
    assert outcome.stats.observations == 1
    assert outcome.stats.actions == 3


async def test_an_action_hands_back_the_screen_it_produced() -> None:
    """The lever the whole design rests on, asserted rather than assumed.

    If a tap returned only "ok", the agent would have to spend an observation
    to learn what happened, and the observation-overhead argument would
    collapse. The next turn must be able to see the new screen already.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel([[("tap", {"target": "Accessibility"})], []])

    await run_goal(session, "Open Accessibility.", model=scripted)

    assert "Display & Text Size" in tool_replies(scripted), (
        "the tap did not return its resulting screen"
    )


async def test_every_action_carries_an_idempotency_key() -> None:
    """A resumed graph must not tap Send twice.

    Agent frameworks re-run the node an interrupt was raised from. `IosSession`
    has had act-once semantics since its first action for that reason, and this
    asserts the agent actually uses them: replaying the same call is a cache
    hit that never reaches the device.
    """
    model = DeviceModel()
    session, fake, _ = build_session(model, _settings())
    backend = SessionBackend(session)

    await backend.tap("Accessibility", idem_key="step-1")
    taps_after_first = len(fake.taps())
    replay = await backend.tap("Accessibility", idem_key="step-1")

    assert len(fake.taps()) == taps_after_first, "the replay reached the device"
    assert "Replayed from the idempotency cache" in replay


async def test_the_loop_stops_when_the_session_halts() -> None:
    """Halting lives in the policy layer and the agent obeys it.

    An agent that keeps driving a halted session is worse than one that gives
    up, so this is a stop condition rather than something to reason about.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel([[("observe", {})]] + [[("tap", {"target": "Wi-Fi"})]] * 5)

    session.halt("stopped by the user")
    outcome = await run_goal(session, "Open Wi-Fi.", model=scripted)

    assert outcome.finished_cleanly is False
    assert "halted" in (outcome.stopped_because or "")
    assert outcome.stats.actions == 0


async def test_a_failed_resolution_becomes_a_message_not_a_crash() -> None:
    """A wrong label is recoverable, and the agent is told what it could pick.

    Raising here would end the run on a mistake the model could fix in one
    turn, and resolution failures already carry candidate elements.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Nonexistent Row"})],
            [("done", {"succeeded": False, "summary": "no such element"})],
        ]
    )

    outcome = await run_goal(session, "Tap something that is not there.", model=scripted)

    assert "element_not_found" in tool_replies(scripted)
    assert outcome.succeeded is False
    assert outcome.finished_cleanly


async def test_a_blocked_action_reaches_the_agent_as_a_refusal() -> None:
    """Going direct does not bypass the gate.

    `PolicyGate` is constructed inside `IosSession`, so the agent passes
    through it on the same path the MCP server does. Skipping MCP must not
    skip approval.
    """
    model = DeviceModel(screen="reset")
    session, _, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Erase All Content and Settings"})],
            [("done", {"succeeded": False, "summary": "refused"})],
        ]
    )

    await run_goal(session, "Erase the device.", model=scripted)

    assert "action_requires_approval" in tool_replies(scripted)


async def test_the_step_budget_ends_a_run_that_is_going_nowhere() -> None:
    """A model that has lost the thread should not spend a whole budget."""
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel([[("set_value", {"value": "on", "target": "Airplane Mode"})]] * 20)

    outcome = await run_goal(session, "Turn on Airplane Mode.", model=scripted, max_steps=3)

    assert outcome.finished_cleanly is False
    assert "gave up after 3 turns" in (outcome.stopped_because or "")
    assert model.switches["airplane"] is False


async def test_an_unknown_tool_is_reported_rather_than_raised() -> None:
    """One wasted message beats a lost run."""
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel([[("teleport", {"to": "Mars"})], []])

    await run_goal(session, "Do something impossible.", model=scripted)

    reply = tool_replies(scripted)
    assert "no such tool 'teleport'" in reply
    assert "observe" in reply, "the model should be told what it could have called"


def test_the_prompt_ships_with_the_package() -> None:
    """A prompt that does not get packaged fails only once it is installed."""
    from ios_agent.loop import operator_prompt

    prompt = operator_prompt()
    assert "Every action returns the screen it produced." in prompt
    assert "call `done`" in prompt


@pytest.mark.parametrize("bad", ["", "   "])
def test_the_prompt_is_not_empty(bad: str) -> None:
    from ios_agent.loop import operator_prompt

    assert operator_prompt().strip() != bad
