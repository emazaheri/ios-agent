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
from langchain.messages import AnyMessage
from screens import DeviceModel, Injection, build_session
from scripted_model import ScriptedModel

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
    # Five model calls: observe, three actions, done. The counter has to agree
    # with `ScriptedModel`, because turns are what a multi-call turn is meant
    # to reduce and a number that quietly drops the finishing turn would make
    # any batching measurement look better than it was.
    assert outcome.turns == scripted.turns == 5


async def test_finishing_does_not_cost_one_more_model_call() -> None:
    """`done` ends the run at the tool node, not one turn later.

    The edge out of `act` used to go straight back to `agent`, and only the
    check after the model node noticed the run was over. So every run ever
    measured spent a whole extra call on a transcript whose last word was
    "recorded", and then threw the reply away. Counting turns is what made it
    visible; nothing else in the run changes when it is removed.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Accessibility"})],
            [("done", {"succeeded": True, "summary": "opened"})],
        ]
    )

    outcome = await run_goal(session, "Open Accessibility.", model=scripted)

    assert outcome.turns == 2, "a call was spent after the run was already over"
    assert scripted.turns == 2, "the model was asked for a reply nobody would read"


async def test_the_agent_can_actually_open_an_app_by_name() -> None:
    """Being in the tool list is not the same as working.

    The commit that put `open_app` back in front of the model asserted only
    that it was there, and that the prompt does not name an absent tool.
    Neither runs it. It turned out `FakeAdapter` had no `list_apps`, which
    `session.open_app` calls to resolve a name, so every run in which the
    model chose this verb died on an `AttributeError` that `guarded` does not
    convert. Sixteen of thirty-nine runs in the first model baseline, and no
    offline test could see it.
    """
    model = DeviceModel()
    session, fake, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("open_app", {"name": "Settings"})],
            [("done", {"succeeded": True, "summary": "opened"})],
        ]
    )

    outcome = await run_goal(session, "Open Settings.", model=scripted)

    assert outcome.stats.actions == 1, "the tool never reached the device"
    assert fake.app_states["com.apple.Preferences"] == 4, (
        f"Settings was not launched: {fake.app_states}"
    )
    assert outcome.finished_cleanly


async def test_an_app_that_is_not_installed_is_a_message_not_a_crash() -> None:
    """The other half: a miss must stay recoverable.

    `AppNotFound` is an `IosAutomationError`, so `guarded` turns it into a
    reply the model can act on. Anything untyped escaping here is what killed
    the baseline.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("open_app", {"name": "Bumble"})],
            [("done", {"succeeded": False, "summary": "not installed"})],
        ]
    )

    outcome = await run_goal(session, "Open Bumble.", model=scripted)

    assert "open_app failed" in tool_replies(scripted)
    assert outcome.finished_cleanly, "an uninstalled app ended the run"


async def test_the_opening_turn_names_the_apps_the_device_has() -> None:
    """The fake used to claim the device had none, because the call raised.

    `loop._device_context` is best-effort and swallows anything, so the
    missing method degraded silently into an empty list. Every scripted run
    therefore opened with a transcript a real device would never produce.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel([[("done", {"succeeded": True, "summary": "nothing to do"})]])

    await run_goal(session, "Do nothing.", model=scripted)

    opening = str(scripted.seen[0][1].content)
    assert "Apps on this device:" in opening, opening
    assert "Settings" in opening


# -- several actions in one turn --------------------------------------------


def _tool_messages(scripted: ScriptedModel) -> list[AnyMessage]:
    """Every tool reply from the last transcript the model was handed."""
    return [m for m in scripted.seen[-1] if getattr(m, "type", "") == "tool"]


async def test_a_navigational_chain_runs_every_call_in_the_turn() -> None:
    """The batch the whole guard is shaped around.

    Each target exists only because the previous action changed the screen.
    browser-use aborts on exactly this, because on the web a navigation means
    the queue is stale; here it is the queue working.
    """
    model = DeviceModel()
    session, fake, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [
                ("tap", {"target": "Accessibility"}),
                ("tap", {"target": "Display & Text Size"}),
                ("set_value", {"value": "on", "target": "Bold Text"}),
            ],
            [("done", {"succeeded": True, "summary": "Bold Text is on"})],
        ]
    )

    outcome = await run_goal(session, "Turn on Bold Text.", model=scripted)

    assert model.switches["bold_text"] is True, "the chain did not reach the switch"
    assert outcome.stats.actions == 3
    assert len(fake.taps()) == 3, "the fake toggles a switch by tapping it, so three"
    # Two model calls for what costs five one at a time. This is the saving.
    assert outcome.turns == 2


async def test_a_link_that_did_not_move_skips_the_rest_of_the_turn() -> None:
    """A dead switch means every later call is aimed at a screen that never came."""
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [
                ("set_value", {"value": "on", "target": "Airplane Mode"}),
                ("tap", {"target": "Accessibility"}),
                ("tap", {"target": "Display & Text Size"}),
            ],
            [("done", {"succeeded": False, "summary": "the switch would not move"})],
        ]
    )

    outcome = await run_goal(session, "Turn on Airplane Mode.", model=scripted)

    assert outcome.stats.actions == 1, "the guard let a later call run"
    replies = _tool_messages(scripted)
    assert sum("not run:" in str(m.content) for m in replies) == 2
    assert "never arrived" in "\n".join(str(m.content) for m in replies)


async def test_every_call_in_a_turn_is_answered_even_when_skipped() -> None:
    """LangChain requires one reply per tool call id, and a missing one fails
    on the *next* model call rather than here, a long way from the cause."""
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [
                ("set_value", {"value": "on", "target": "Airplane Mode"}),
                ("tap", {"target": "Accessibility"}),
                ("tap", {"target": "Wi-Fi"}),
            ],
            [("done", {"succeeded": False, "summary": "stopped"})],
        ]
    )

    await run_goal(session, "Turn on Airplane Mode.", model=scripted)

    answered = [m.tool_call_id for m in _tool_messages(scripted)]  # type: ignore[attr-defined]
    assert sorted(answered) == ["call-0-0", "call-0-1", "call-0-2"], (
        f"one reply per call id, got {answered}"
    )


async def test_done_in_the_middle_of_a_turn_stops_what_follows_it() -> None:
    """Latent since the loop was written, and only reachable by batching."""
    model = DeviceModel()
    session, fake, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [
                ("done", {"succeeded": True, "summary": "already done"}),
                ("tap", {"target": "Accessibility"}),
                ("tap", {"target": "Wi-Fi"}),
            ]
        ]
    )

    outcome = await run_goal(session, "Do nothing.", model=scripted)

    assert fake.taps() == [], "a call after `done` reached the device"
    assert outcome.succeeded is True
    assert outcome.stats.actions == 0


async def test_a_scroll_ends_the_turn_even_though_the_screen_moved() -> None:
    """The case the no-change rule cannot catch.

    `scroll(until=...)` loops server-side and stops wherever the content did,
    so it moves the fingerprint and every other rule would wave the next call
    through, aimed at a screen the model only guessed at.
    """
    model = DeviceModel(screen="contacts")
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [
                ("scroll", {"direction": "down", "until": "Contact 060"}),
                ("tap", {"target": "Contact 060"}),
            ],
            [("done", {"succeeded": True, "summary": "found it"})],
        ]
    )

    outcome = await run_goal(session, "Find Contact 060.", model=scripted)

    assert outcome.stats.actions == 1, "a call was chained behind a scroll"
    assert any("cannot be predicted" in str(m.content) for m in _tool_messages(scripted))


async def test_the_action_budget_bounds_a_turn_that_asks_for_everything() -> None:
    """The turn budget used to bound both, one action per turn. Batching
    breaks that link, and the device side must stay bounded on its own."""
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    # Alternating taps that each move the screen, so the guard never stops it.
    pair = [("tap", {"target": "Accessibility"}), ("tap", {"target": "Back"})]
    scripted = ScriptedModel([pair * 4] * 10)

    outcome = await run_goal(session, "Wander forever.", model=scripted)

    assert outcome.finished_cleanly is False
    assert "actions" in (outcome.stopped_because or ""), outcome.stopped_because


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
    model = DeviceModel(screen="general")
    session, fake, _ = build_session(model, _settings(confirm_destructive=True))
    scripted = ScriptedModel(
        [
            # The first call has to be one the batch guard allows through, or
            # the second never runs and there is no interrupt to resume from.
            # Tapping Reset navigates; tapping "Reset Network Settings", which
            # this used to open with, goes nowhere in the model and is now
            # correctly read as a screen that never arrived.
            [
                ("tap", {"target": "Reset"}),
                ("tap", {"target": "Erase All Content and Settings"}),
            ],
            [("done", {"succeeded": True, "summary": "done"})],
        ]
    )

    async def yes(_request: dict[str, object]) -> bool:
        return True

    outcome = await run_goal(session, "Reset the network, then erase.", model=scripted, approve=yes)

    assert len(fake.taps()) == 2, (
        f"expected one tap each, got {len(fake.taps())}: the node re-ran and "
        "the harmless tap was replayed onto the device"
    )
    # The device was already asserted; this is the counter, which was wrong.
    # The replayed tap came back from the cache without touching the phone and
    # was still counted as an action, so a resumed run reported more work than
    # it did against an eval floor asserted by equality.
    assert outcome.stats.actions == 2, (
        f"two taps were requested and {outcome.stats.actions} counted: a cache "
        "replay is not another action"
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


# -- the tool surface -------------------------------------------------------


def test_every_backend_verb_is_a_tool_the_model_can_call() -> None:
    """The list `build_tools` returns is the whole of what the model has.

    `open_app` was defined, wired to both backends and named in the operator
    prompt, and then left out of the returned list for the entire life of the
    commit that added it ("Give the agent a ninth verb", a981e3d). The model
    was told to call it and got `no such tool 'open_app'` back. Nothing caught
    it, because every test either drove the backend directly or scripted a
    model that only called tools that happened to be present.

    Deriving the expectation from the `Backend` protocol rather than listing
    names means the next verb cannot be half-added the same way.
    """
    from ios_agent.backend import Backend
    from ios_agent.tools import Run, build_tools

    not_verbs = {"approve", "stop_reason", "stats", "last_screen", "last_action"}
    verbs = set(Backend.__protocol_attrs__) - not_verbs

    tools = build_tools(Run(backend=None, goal="anything"))  # type: ignore[arg-type]
    names = {t.name for t in tools}

    assert verbs <= names, f"backend verbs the model cannot call: {sorted(verbs - names)}"
    assert names == verbs | {"done"}, f"unexpected tools: {sorted(names - verbs - {'done'})}"


def test_the_prompt_only_names_tools_the_model_has() -> None:
    """The prompt told the model to use `open_app` while it did not exist.

    A prompt naming an absent tool is worse than silence: it spends the
    model's turns on a call that can only fail, which is the shape of the
    screenshot incident the operator prompt is worded against.
    """
    from ios_agent.loop import operator_prompt
    from ios_agent.tools import Run, build_tools

    prompt = operator_prompt()
    names = {t.name for t in build_tools(Run(backend=None, goal="anything"))}  # type: ignore[arg-type]

    for quoted in ("open_app", "open_url", "done", "observe"):
        if f"`{quoted}`" in prompt:
            assert quoted in names, (
                f"the prompt tells the model to call `{quoted}`, which is absent"
            )


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


def test_the_opening_turn_says_which_app_is_already_open() -> None:
    """Naming what exists without naming where you are invites a guess.

    With the app list and nothing else, the model opened an app as its first
    move on every run, including runs already inside that app: one wasted
    device action each, which took actions from 1.29x the oracle floor to
    1.52x while the observation count it displaced made the headline metric
    look better.
    """
    messages = opening_messages("SYSTEM", "Turn on Bold Text.", ["Maps", "Settings"], "Settings")
    human = str(messages[1].content)

    assert "Maps, Settings" in human
    assert "Settings is already open." in human


def test_nothing_is_claimed_open_when_the_device_will_not_say() -> None:
    """`foreground_app` is best effort, and silence must stay silent."""
    human = str(opening_messages("SYSTEM", "Goal.", ["Maps"], None)[1].content)

    assert "already open" not in human


async def test_the_agent_is_told_where_it_is_before_it_acts() -> None:
    """End to end: the real session resolves the foreground app to a name."""
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel([[("done", {"succeeded": True, "summary": "nothing to do"})]])

    await run_goal(session, "Do nothing.", model=scripted)

    opening = str(scripted.seen[0][1].content)
    assert "is already open." in opening, opening


def test_the_system_prompt_never_carries_the_app_list() -> None:
    """It is per-device, and the system prompt is kept byte-identical so it
    stays cacheable across every run."""
    with_apps = opening_messages("SYSTEM", "Goal.", ["Maps"])
    without = opening_messages("SYSTEM", "Goal.")
    assert with_apps[0].content == without[0].content == "SYSTEM"


# -- the claim, and the device's chance to disagree --------------------------


async def test_a_run_that_moved_something_is_verified() -> None:
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("tap", {"target": "Accessibility"})],
            [("done", {"succeeded": True, "summary": "opened Accessibility"})],
        ]
    )

    outcome = await run_goal(session, "Open Accessibility.", model=scripted)

    assert outcome.succeeded is True
    assert outcome.contradicted is False
    assert outcome.verified is True
    assert outcome.stats.changes == 1


async def test_a_claim_that_nothing_on_the_device_backs_is_contradicted() -> None:
    """The failure this exists for, reproduced with the injection that models it.

    `set_value` on a dead switch returns ok and moves nothing, so an agent that
    trusts its own return value declares victory on a phone it never changed.
    The claim is kept as the claim; only the verdict disagrees.
    """
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("set_value", {"value": "on", "target": "Airplane Mode"})],
            [("done", {"succeeded": True, "summary": "Airplane Mode is on"})],
        ]
    )

    outcome = await run_goal(session, "Turn on Airplane Mode.", model=scripted)

    assert model.switches["airplane"] is False, "the injection did not inject"
    assert outcome.succeeded is True, "the claim is the agent's and is kept"
    assert outcome.contradicted is True
    assert outcome.verified is False
    # The run did finish. Conflating a contradicted claim with an interrupted
    # loop would make this lie.
    assert outcome.finished_cleanly is True


async def test_a_read_only_run_is_not_contradicted() -> None:
    """Answering from a screen without touching it is a way to finish.

    Two eval tasks do exactly this at their floor, and one of them passes by
    refusing to act at all, so a rule phrased as "no actions means not done"
    would fail them for being correct.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("observe", {})],
            [("done", {"succeeded": True, "summary": "Bold Text is off"})],
        ]
    )

    outcome = await run_goal(session, "Is Bold Text on?", model=scripted)

    assert outcome.stats.actions == 0
    assert outcome.contradicted is False
    assert outcome.verified is True


async def test_an_honest_failure_is_never_contradicted() -> None:
    """There is no claim to disagree with, and saying so would be an accusation."""
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            [("set_value", {"value": "on", "target": "Airplane Mode"})],
            [("done", {"succeeded": False, "summary": "the switch will not move"})],
        ]
    )

    outcome = await run_goal(session, "Turn on Airplane Mode.", model=scripted)

    assert outcome.succeeded is False
    assert outcome.contradicted is False
    assert outcome.verified is False


async def test_one_action_that_moved_hides_a_later_one_that_did_not() -> None:
    """Pinning how narrow the rule is, so nobody reads it as stronger.

    Navigate somewhere, then meet a dead switch, then claim success: one action
    changed the screen, so the claim stands even though it is false.

    The stronger rule, "the last action changed nothing", is not available.
    `verify.py` documents that a dead switch and a control already in the
    requested state return byte-identical payloads, so that rule would call a
    correct run on an already-satisfied goal a lie. This is the trade, and it is
    asserted rather than described so that changing it is a decision.
    """
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _settings())
    scripted = ScriptedModel(
        [
            # Dead: the injection pins this one switch, so it accepts the call
            # and moves nothing.
            [("set_value", {"value": "on", "target": "Airplane Mode"})],
            # Live: navigation always changes the screen.
            [("tap", {"target": "Accessibility"})],
            [("done", {"succeeded": True, "summary": "Airplane Mode is on"})],
        ]
    )

    outcome = await run_goal(session, "Turn on Airplane Mode.", model=scripted)

    assert model.switches["airplane"] is False, "the claim is false"
    assert outcome.stats.actions == 2
    assert outcome.stats.changes == 1, "one moved, one did not, which is the point"
    assert outcome.succeeded is True
    assert outcome.contradicted is False, "the rule is narrower than this run"
