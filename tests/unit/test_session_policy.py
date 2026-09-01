"""Policy enforcement inside a live session."""

from __future__ import annotations

import pytest
from fake_device import make_session
from trees import form_screen, node, settings_screen

from ios_mcp.errors import (
    ActionRejectedByPolicy,
    ActionRequiresApproval,
    AppNotAllowed,
    SessionHalted,
)


async def test_a_destructive_tap_is_refused_until_approved() -> None:
    session, fake, _ = make_session(form_screen())
    await session.observe()

    with pytest.raises(ActionRequiresApproval) as exc_info:
        await session.tap(target="Send")

    assert fake.taps() == [], "the device must not be touched before approval"
    assert "signature" in exc_info.value.details


async def test_approving_the_signature_lets_the_action_through() -> None:
    session, fake, _ = make_session(form_screen())
    await session.observe()

    with pytest.raises(ActionRequiresApproval) as exc_info:
        await session.tap(target="Send")
    session.approve(exc_info.value.details["signature"])

    result = await session.tap(target="Send")

    assert result.ok
    assert len(fake.taps()) == 1


async def test_approval_does_not_generalise_to_other_destructive_actions() -> None:
    tree = node(
        "Application",
        h=852,
        children=[
            node("Button", label="Send", name="send_btn", y=100),
            node("Button", label="Delete", name="del_btn", y=200),
        ],
    )
    session, _, _ = make_session(tree)
    await session.observe()

    with pytest.raises(ActionRequiresApproval) as exc_info:
        await session.tap(target="Send")
    session.approve(exc_info.value.details["signature"])
    await session.tap(target="Send")

    with pytest.raises(ActionRequiresApproval):
        await session.tap(target="Delete")


async def test_an_approval_handler_is_consulted_and_can_allow() -> None:
    asked: list[str] = []

    async def approve(action, verdict, target):  # type: ignore[no-untyped-def]
        asked.append(target.label if target else action)
        return True

    session, fake, _ = make_session(form_screen())
    session.on_approval = approve
    await session.observe()

    result = await session.tap(target="Send")

    assert asked == ["Send"]
    assert result.ok
    assert len(fake.taps()) == 1


async def test_an_approval_handler_can_decline() -> None:
    async def decline(action, verdict, target):  # type: ignore[no-untyped-def]
        return False

    session, fake, _ = make_session(form_screen())
    session.on_approval = decline
    await session.observe()

    with pytest.raises(ActionRejectedByPolicy) as exc_info:
        await session.tap(target="Send")

    assert fake.taps() == []
    assert "Do not retry" in (exc_info.value.hint or "")


async def test_a_declined_action_is_asked_again_next_time() -> None:
    """A refusal must not be cached as consent."""
    answers = [False, True]

    async def handler(action, verdict, target):  # type: ignore[no-untyped-def]
        return answers.pop(0)

    session, fake, _ = make_session(form_screen())
    session.on_approval = handler
    await session.observe()

    with pytest.raises(ActionRejectedByPolicy):
        await session.tap(target="Send")
    await session.tap(target="Send")

    assert len(fake.taps()) == 1


async def test_ordinary_actions_never_prompt() -> None:
    asked: list[str] = []

    async def handler(action, verdict, target):  # type: ignore[no-untyped-def]
        asked.append(action)
        return True

    session, _, _ = make_session(settings_screen())
    session.on_approval = handler
    await session.observe()

    await session.tap(target="Wi-Fi")

    assert asked == []


async def test_launching_a_blocked_app_is_refused() -> None:
    session, _, _ = make_session(settings_screen())
    with pytest.raises(AppNotAllowed):
        await session.launch_app("com.apple.Passbook")


async def test_typing_a_secret_keeps_it_out_of_the_result_and_the_audit(monkeypatch) -> None:
    monkeypatch.setenv("IOS_MCP_SECRET_TEST_PASSWORD", "hunter2")
    session, fake, _ = make_session(form_screen())
    await session.observe()

    result = await session.type_secret("test-password", target="To:")

    assert "hunter2" in fake.typed(), "the device still receives the real value"
    assert "hunter2" not in str(result.to_dict())
    assert "hunter2" not in str(session.audit.to_dict())


async def test_a_secret_is_not_run_through_the_destructive_text_rules(monkeypatch) -> None:
    """A password containing 'delete' must not trigger an approval prompt."""
    monkeypatch.setenv("IOS_MCP_SECRET_ODD", "please-delete-me")
    session, _, _ = make_session(form_screen())
    await session.observe()
    result = await session.type_secret("odd", target="To:")
    assert result.ok


async def test_typing_destructive_text_does_prompt() -> None:
    session, _, _ = make_session(form_screen())
    await session.observe()
    with pytest.raises(ActionRequiresApproval):
        await session.type_text("delete my account", target="Body")


async def test_repeated_failures_halt_the_session() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()

    for _ in range(session.settings.policy.max_consecutive_failures):
        with pytest.raises(Exception):  # noqa: B017 - ElementNotFound
            await session.tap(target="Nothing Like This Exists")

    assert session.halted
    with pytest.raises(SessionHalted):
        await session.tap(target="Wi-Fi")


async def test_resume_clears_a_halt() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()
    session.halt("testing")
    assert session.halted
    session.resume()
    result = await session.tap(target="Wi-Fi")
    assert result.ok


async def test_repeatedly_observing_one_screen_is_not_a_loop() -> None:
    """Reading the same screen carefully is not thrashing; halting there would
    stop sessions that are behaving correctly."""
    session, _, _ = make_session(settings_screen())
    for _ in range(session.settings.policy.loop_detection_window + 2):
        await session.observe()

    assert not session.looping
    assert not session.halted


async def test_an_agent_whose_actions_change_nothing_is_halted() -> None:
    """Tapping repeatedly with no effect burns budget until something stops it."""
    session, _, _ = make_session(settings_screen())
    await session.observe()

    for _ in range(session.settings.policy.loop_detection_window + 1):
        if session.halted:
            break
        await session.tap(target="Wi-Fi")

    assert session.looping
    assert session.halted


async def test_successful_actions_are_recorded_in_the_audit_trail() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()
    await session.tap(target="Wi-Fi")

    summary = session.audit.summary()
    assert summary["steps"] == 1
    assert summary["failures"] == 0
    assert summary["resolution_tiers"] == {"text-exact": 1}


async def test_failed_actions_are_recorded_too() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()
    with pytest.raises(Exception):  # noqa: B017
        await session.tap(target="Nonexistent Thing")

    assert session.audit.summary()["failures"] == 1


async def test_every_action_reaches_the_audit_trail() -> None:
    """Scrolls, swipes, launches and alerts bypass _act entirely.

    Recording only in _act left the layer whose whole purpose is forensics
    blind to most of what a session did.
    """
    session, _, _ = make_session(settings_screen())
    await session.observe()

    await session.tap(target="Wi-Fi")
    await session.scroll("down")
    await session.swipe("left")
    await session.launch_app("com.apple.Preferences")
    await session.open_url("App-prefs:root")
    await session.wait_for("Wi-Fi", timeout_s=0.2)

    actions = [e.action for e in session.audit.entries]
    assert "tap" in actions
    assert "scroll" in actions
    assert "swipe" in actions
    assert any(a.startswith("launch_app") for a in actions)
    assert "open_url" in actions
    assert "wait_for" in actions
    assert session.audit.summary()["failures"] == 0


async def test_audit_entries_carry_what_the_action_did() -> None:
    """A trail that records only names cannot explain what happened."""
    session, _, _ = make_session(settings_screen())
    await session.observe()
    await session.scroll("down", until="Bluetooth")

    entry = next(e for e in session.audit.entries if e.action == "scroll")
    assert entry.args["direction"] == "down"
    assert entry.args["until"] == "Bluetooth"
    assert entry.elapsed_ms is not None
    assert entry.fingerprint


# -- what the trail can now answer ------------------------------------------


async def test_a_declined_action_reaches_the_trail() -> None:
    """A record of what a session did that omits what a human stopped is not
    a record of what the session did."""
    session, fake, _ = make_session(form_screen())
    await session.observe()

    with pytest.raises(ActionRequiresApproval):
        await session.tap(target="Send")

    entry = session.audit.failures[-1]
    assert entry.action == "tap"
    assert entry.code == "action_requires_approval"
    assert fake.taps() == []


async def test_a_refusal_never_counts_toward_the_halt() -> None:
    """Halting because the operator said no would be a safety bug."""
    session, _, _ = make_session(form_screen())
    await session.observe()

    for _ in range(session.settings.policy.max_consecutive_failures + 2):
        with pytest.raises(ActionRequiresApproval):
            await session.tap(target="Send")

    assert session.gate.consecutive_failures == 0
    assert not session.halted


async def test_a_blocked_launch_is_recorded_with_its_code() -> None:
    session, _, _ = make_session(settings_screen())
    with pytest.raises(AppNotAllowed):
        await session.launch_app("com.apple.Passbook")

    entry = session.audit.failures[-1]
    assert entry.code == "app_not_allowed"
    assert session.audit.summary()["faults"] == {"policy": 1}


async def test_a_wait_that_times_out_is_not_recorded_as_a_success() -> None:
    """_finish used to hardcode ok=True, so an unmet wait looked like one, and
    worse, reset the consecutive-failure counter on its way past."""
    session, _, _ = make_session(settings_screen())
    await session.observe()

    result = await session.wait_for("Nothing Like This", timeout_s=0.1)

    assert not result.ok
    entry = next(e for e in session.audit.entries if e.action == "wait_for")
    assert not entry.ok
    assert entry.code == "timeout"
    assert session.audit.summary()["faults"] == {"model": 1}


async def test_a_wait_that_times_out_does_not_clear_earlier_failures() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()

    with pytest.raises(Exception):  # noqa: B017
        await session.tap(target="Nonexistent Thing")
    before = session.gate.consecutive_failures
    await session.wait_for("Nothing Like This", timeout_s=0.1)

    assert session.gate.consecutive_failures == before


async def test_a_resolution_failure_records_what_it_could_see() -> None:
    """The candidates are the discriminator between a digest that showed
    nothing and a model that named the wrong thing."""
    session, _, _ = make_session(settings_screen())
    await session.observe()

    with pytest.raises(Exception):  # noqa: B017
        await session.tap(target="Nonexistent Thing")

    entry = session.audit.failures[-1]
    assert entry.details is not None
    assert entry.details["closest"]
    assert "visible" not in entry.details, "the whole screen must not enter the trail"
    assert session.audit.summary()["faults"] == {"model": 1}


async def test_details_are_redacted_before_they_are_stored() -> None:
    """The trail is written straight to disk by the front end, so redacting at
    the server boundary would be too late."""
    screen = node(
        "Application",
        label="Mail",
        h=852,
        children=[
            node("Button", label="someone@example.com", y=120, h=44),
            node("Button", label="Compose", y=200, h=44),
        ],
    )
    session, _, _ = make_session(screen)
    await session.observe()

    with pytest.raises(Exception):  # noqa: B017
        await session.tap(target="Nonexistent Thing")

    stored = str(session.audit.failures[-1].details)
    assert "someone@example.com" not in stored
    assert "[redacted]" in stored


async def test_a_near_miss_records_how_near_it_was() -> None:
    """Attribution splits on whether there were candidates at all. The score
    is what a finer split would need, and it costs nothing to record now."""
    session, _, _ = make_session(settings_screen())
    await session.observe()

    with pytest.raises(Exception):  # noqa: B017
        await session.tap(target="Wireless")

    details = session.audit.failures[-1].details
    assert details is not None
    assert 0.0 < details["best_score"] < 1.0
