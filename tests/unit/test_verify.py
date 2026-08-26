"""Verification: judging an action from the screen it already returned.

The constraint that matters most is negative. Verification must not re-read the
screen, because the S1 baseline measured observations already at the oracle's
floor, so there is nothing to save there and an extra snapshot would spend the
project's scarcest resource to re-learn what the action already reported. The
last test in this module asserts that against a real session.
"""

from __future__ import annotations

from ios_agent.backend import SessionBackend
from ios_agent.verify import DEFAULT_MAX_ATTEMPTS, Judgement, Verifier, attempt_key
from screens import DeviceModel, Injection, build_session

from ios_mcp.actions.result import ActionResult, DigestDelta
from ios_mcp.config import Settings

SET_AIRPLANE = attempt_key("set_value", "Airplane Mode", "on")


def _fast_settings() -> Settings:
    """Settle timings shrunk so the suite does not spend its life sleeping."""
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    cfg.policy.loop_detection_window = 50
    cfg.policy.confirm_destructive = False
    return cfg


def _result(*, changed: bool, delta: DigestDelta | None = None) -> ActionResult:
    return ActionResult(
        action="set_value",
        ok=True,
        screen_changed=changed,
        elapsed_ms=10,
        delta=delta if delta is not None else DigestDelta(),
    )


def test_a_screen_that_moved_says_nothing() -> None:
    """Commentary on a working action is noise the agent pays for."""
    verdict = Verifier().record(SET_AIRPLANE, _result(changed=True))

    assert verdict.judgement is Judgement.PROGRESSED
    assert verdict.note is None


def test_the_first_no_op_is_named_without_alarm() -> None:
    """One no-op can be a slow transition, so it is reported, not escalated."""
    verdict = Verifier().record(SET_AIRPLANE, _result(changed=False))

    assert verdict.judgement is Judgement.NO_OP
    assert verdict.note is not None
    assert "may not have done anything" in verdict.note


def test_repeating_a_no_op_is_called_out() -> None:
    verifier = Verifier()
    verifier.record(SET_AIRPLANE, _result(changed=False))

    verdict = verifier.record(SET_AIRPLANE, _result(changed=False))

    assert verdict.judgement is Judgement.REPEATED_NO_OP
    assert verdict.note is not None
    assert "2 attempts" in verdict.note


def test_the_attempt_is_refused_once_the_budget_is_spent() -> None:
    """Advice the model can ignore leaves the failure mode intact.

    The measured behaviour this exists for was 22 retries of one action. A
    suggestion would not have bounded that; refusing does.
    """
    verifier = Verifier()
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        assert verifier.check(SET_AIRPLANE) is None
        verifier.record(SET_AIRPLANE, _result(changed=False))

    refusal = verifier.check(SET_AIRPLANE)

    assert refusal is not None
    assert refusal.judgement is Judgement.EXHAUSTED
    assert refusal.allows_acting is False
    assert "will not work" in (refusal.note or "")


def test_one_success_clears_the_record() -> None:
    """A control that moved once may legitimately be asked again."""
    verifier = Verifier()
    verifier.record(SET_AIRPLANE, _result(changed=False))
    verifier.record(SET_AIRPLANE, _result(changed=False))

    verifier.record(SET_AIRPLANE, _result(changed=True))

    assert verifier.record(SET_AIRPLANE, _result(changed=False)).judgement is Judgement.NO_OP


def test_opposite_requests_are_counted_apart() -> None:
    """`on` and `off` on one switch are different intents.

    Counting them together would refuse a correction after a mistake, which is
    the one retry that is always legitimate.
    """
    verifier = Verifier()
    off = attempt_key("set_value", "Airplane Mode", "off")
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        verifier.record(SET_AIRPLANE, _result(changed=False))

    assert verifier.check(SET_AIRPLANE) is not None
    assert verifier.check(off) is None


def test_a_delta_counts_as_movement_even_without_a_fingerprint_change() -> None:
    """Fingerprints round positions to 4px, so a delta is the finer signal."""
    from ios_mcp.perception.digest import DigestNode
    from ios_mcp.wda.models import Rect

    node = DigestNode(
        ref="e1",
        role="switch",
        label="Airplane Mode",
        value="1",
        identifier=None,
        rect=Rect(x=0, y=0, width=10, height=10),
        enabled=True,
        actionable=True,
        scrollable=False,
        selected=False,
        depth=1,
    )
    moved = DigestDelta(changed=[(node, node)])

    verdict = Verifier().record(SET_AIRPLANE, _result(changed=False, delta=moved))

    assert verdict.judgement is Judgement.PROGRESSED


async def test_a_refused_attempt_never_reaches_the_device() -> None:
    """The point of refusing rather than advising, asserted end to end."""
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, fake, _ = build_session(model, _fast_settings())
    backend = SessionBackend(session)
    await backend.observe()

    replies = [await backend.set_value("on", "Airplane Mode", idem_key=f"k{i}") for i in range(6)]

    assert backend.stats.actions == DEFAULT_MAX_ATTEMPTS, "the device kept being touched"
    assert backend.stats.refusals == 6 - DEFAULT_MAX_ATTEMPTS
    assert len(fake.taps()) == DEFAULT_MAX_ATTEMPTS
    assert "Not run." in replies[-1]
    assert model.switches["airplane"] is False


async def test_verification_never_re_reads_the_screen() -> None:
    """The constraint the whole slice is built under.

    Observations were already at the oracle's floor before this pillar existed,
    so a verification step that snapshots to check its own work would spend the
    scarcest resource in the system to learn what it had just been told.
    """
    model = DeviceModel(injections=frozenset({Injection.DEAD_SWITCH}))
    session, _, _ = build_session(model, _fast_settings())
    backend = SessionBackend(session)
    await backend.observe()
    before = backend.stats.observations

    for i in range(5):
        await backend.set_value("on", "Airplane Mode", idem_key=f"k{i}")

    assert backend.stats.observations == before, "verification cost an observation"


async def test_a_working_control_is_never_refused() -> None:
    """The false positive that would matter: blocking a device that complies."""
    model = DeviceModel()
    session, _, _ = build_session(model, _fast_settings())
    backend = SessionBackend(session)
    await backend.observe()

    for i in range(6):
        wanted = "on" if i % 2 == 0 else "off"
        await backend.set_value(wanted, "Airplane Mode", idem_key=f"k{i}")

    assert backend.stats.refusals == 0
    assert backend.stats.actions == 6
