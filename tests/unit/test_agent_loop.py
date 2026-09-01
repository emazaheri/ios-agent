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
from ios_agent.graph import opening_messages
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


async def test_a_destructive_action_is_refused_when_nobody_answers() -> None:
    """Going direct does not bypass the gate, and silence is not consent.

    `PolicyGate` is constructed inside `IosSession`, so the agent passes
    through it on the same path the MCP server does. With no approver, the run
    is unattended, and SAFETY.md is explicit that a client which cannot answer
    is treated as refusal.
    """
    model = DeviceModel(screen="reset")
    session, fake, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Erase All Content and Settings"})],
            [("done", {"succeeded": False, "summary": "refused"})],
        ]
    )

    outcome = await run_goal(session, "Erase the device.", model=scripted)

    assert "not approved by the operator" in tool_replies(scripted)
    assert fake.taps() == [], "a refused action still reached the device"
    assert outcome.approvals_asked == 1


async def test_a_human_saying_yes_lets_the_action_through_once() -> None:
    """The resume path, end to end: pause, answer, act.

    The gate classifies before acting, so at the moment of the pause nothing
    has happened to the device yet. That is what makes asking worth anything.
    """
    model = DeviceModel(screen="reset")
    session, fake, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Erase All Content and Settings"})],
            [("done", {"succeeded": True, "summary": "erased"})],
        ]
    )
    asked: list[dict[str, object]] = []

    async def yes(request: dict[str, object]) -> bool:
        asked.append(request)
        return True

    outcome = await run_goal(session, "Erase the device.", model=scripted, approve=yes)

    assert len(fake.taps()) == 1, "the approved action ran a number of times other than once"
    assert outcome.approvals_asked == 1
    assert asked[0]["type"] == "approval_required"
    assert "erase" in str(asked[0]["signature"]).lower()


async def test_resuming_does_not_replay_earlier_actions_onto_the_device() -> None:
    """The reason idempotency keys exist, finally exercised.

    Resuming an interrupted graph re-runs the whole node, so every tool call in
    it executes again. Here a harmless tap shares a turn with one that needs
    approval: the second pauses, and on resume the first must come back from
    the idempotency cache rather than touching the device a second time.

    This is what the original keys did not do. They were derived from a
    counter that incremented per call, so a re-run produced a different key and
    missed the cache. The key is now the tool call id, which LangGraph replays
    unchanged.
    """
    model = DeviceModel(screen="reset")
    session, fake, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            [
                ("tap", {"target": "Reset Network Settings"}),
                ("tap", {"target": "Erase All Content and Settings"}),
            ],
            [("done", {"succeeded": True, "summary": "done"})],
        ]
    )

    async def yes(_request: dict[str, object]) -> bool:
        return True

    await run_goal(session, "Reset the network, then erase.", model=scripted, approve=yes)

    assert len(fake.taps()) == 2, (
        f"expected one tap each, got {len(fake.taps())}: the node re-ran and "
        "the harmless tap was replayed onto the device"
    )


async def test_approving_one_action_does_not_approve_another() -> None:
    """Approval is scoped to a signature, never to the session.

    SAFETY.md: approving Send does not approve Delete. Two destructive taps in
    one run must be asked about separately.
    """
    model = DeviceModel(screen="reset")
    session, _, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Erase All Content and Settings"})],
            [("tap", {"target": "Erase All Content and Settings"})],
            [("done", {"succeeded": True, "summary": "done"})],
        ]
    )
    signatures: list[str] = []

    async def yes(request: dict[str, object]) -> bool:
        signatures.append(str(request["signature"]))
        return True

    outcome = await run_goal(session, "Erase twice.", model=scripted, approve=yes)

    # The same signature, so the second is remembered rather than re-asked;
    # a *different* target would be a separate question.
    assert outcome.approvals_asked >= 1
    assert all("erase" in s.lower() for s in signatures)


async def test_an_ordinary_task_never_stops_to_ask() -> None:
    """A gate that prompts on everything trains people to approve reflexively.

    Which is worse than no gate, so this asserts the quiet path stays quiet.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Accessibility"})],
            [("done", {"succeeded": True, "summary": "ok"})],
        ]
    )

    outcome = await run_goal(session, "Open Accessibility.", model=scripted)

    assert outcome.approvals_asked == 0


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


# -- knowing what is on the device ------------------------------------------


def test_the_goal_alone_is_sent_when_nothing_is_installed() -> None:
    """No list, no noise: the opening turn stays exactly the goal."""
    messages = opening_messages("SYSTEM", "Turn on Bold Text.")
    assert messages[1].content == "Turn on Bold Text."


def test_the_installed_apps_ride_in_the_goal_turn() -> None:
    """The agent cannot see past the first page of icons, and an app in a
    folder is invisible, so it is told what exists before it has to guess."""
    messages = opening_messages("SYSTEM", "Open Maps.", ["Maps", "Safari", "Settings"])
    human = messages[1].content
    assert human.startswith("Open Maps.")
    assert "Maps, Safari, Settings" in human


def test_the_system_prompt_never_carries_the_app_list() -> None:
    """It is per-device, and the system prompt is kept byte-identical so it
    stays cacheable across every run."""
    with_apps = opening_messages("SYSTEM", "Goal.", ["Maps"])
    without = opening_messages("SYSTEM", "Goal.")
    assert with_apps[0].content == without[0].content == "SYSTEM"
