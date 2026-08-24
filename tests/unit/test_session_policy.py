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


async def test_a_looping_agent_is_halted() -> None:
    """Bouncing between the same screens burns budget until something stops it."""
    session, _, _ = make_session(settings_screen())
    window = session.settings.policy.loop_detection_window

    for _ in range(window + 1):
        await session.observe()

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
