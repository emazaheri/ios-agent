"""Tier 3: the agent against a physical iPhone.

The last claim this project could not make. Everything below the agent has been
driven against real hardware since the phase before this one, and the agent had
only ever met a scripted fake and a simulator. CLAUDE.md's own standard is that
nothing stays unverified against hardware, and this is what closes it for the
agent.

A simulator is not a substitute here, and the numbers say why. A snapshot costs
under a second on a simulator and about 3.7 seconds on a device, so
`stabilize.max_wait_s` has to exceed `stable_samples` snapshots or a real
device times out on every action. That difference has already caused bugs in
this repository that no fake and no simulator caught.

## What this changes on the phone, and how it is put back

One switch: Bold Text, in Accessibility -> Display & Text Size. Its state is
read before the run and restored in a `finally`, so a failure mid-run still
leaves the phone as it was found. Nothing else is touched, and no approver is
supplied, so the policy gate refuses anything destructive outright.

Opt-in twice over: the `device` marker, and `IOS_MCP_ALLOW_DEVICE=1`. Hardware
being present is not consent to change settings on it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from agent_driver import requires_a_model
from device_support import requires_device
from ios_agent import AgentSettings, SessionBackend, run_goal

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession

pytestmark = [
    pytest.mark.agent,
    pytest.mark.model,
    pytest.mark.device,
    requires_device,
    requires_a_model,
]

REPORT = Path(".artifacts/evals/agent-tier3-device.json")
SETTINGS_APP = "com.apple.Preferences"
GOAL = "Turn on Bold Text in Settings."


def device_settings() -> Settings:
    cfg = Settings()
    # A device snapshot costs about 3.7s against well under a second on a
    # simulator, so this ceiling must exceed `stable_samples` snapshots or the
    # settle loop can never converge and every action reports an unsettled
    # screen. The 6s default that predates this measurement did exactly that.
    cfg.stabilize.max_wait_s = 20.0
    # Launching WebDriverAgent over Wi-Fi goes through xcodebuild, which is slow.
    cfg.wda.startup_timeout_s = 300.0
    cfg.policy.confirm_destructive = True  # nothing destructive is expected
    cfg.policy.loop_detection_window = 50
    return cfg


async def _read_bold_text(session: IosSession) -> str | None:
    """Navigate to the switch and read it, independently of the agent.

    Independence is the point. An agent that says it worked is evidence of
    nothing, which is precisely what the dead-switch eval task exists to prove.
    """
    await session.launch_app(SETTINGS_APP, fresh=True)
    await session.tap(target="Accessibility")
    await session.tap(target="Display & Text Size")
    screen = await session.observe()
    row = next((n for n in screen.nodes if n.label and "bold text" in n.label.lower()), None)
    return None if row is None else row.value


@pytest.fixture(scope="module")
async def phone():
    """Lease the physical device, named explicitly.

    `acquire()` with no device asks `_best_default`, which ranks simulators
    above phones on purpose: acting on someone's real device should take
    intent, not just happen to be what the pool picked. Tier 3 supplies that
    intent by resolving the phone's UDID first.

    The `kind` assertion stays anyway. It is what caught the first version of
    this fixture quietly leasing the booted simulator and calling it hardware,
    which would have produced a passing tier-3 report that proved nothing.
    """
    from ios_mcp.devices.discovery import list_real_devices

    cfg = device_settings()
    phones = [d for d in await list_real_devices(cfg) if d.ready]
    assert phones, "no ready physical device, though the skip gate said otherwise"

    pool = DevicePool(cfg)
    try:
        lease = await pool.acquire(phones[0].udid, bundle_id=SETTINGS_APP)
        assert lease.device.kind == "device", (
            f"acquired a {lease.device.kind}, not a phone; tier 3 must not run on a simulator"
        )
        print(f"\n  leased {lease.device.name} (iOS {lease.device.os_version})")
        yield IosSession(lease, cfg)
    finally:
        await pool.release_all()


async def test_the_agent_drives_a_real_iphone(phone: IosSession) -> None:
    """One goal, on hardware, verified against the device and then undone."""
    started_state = await _read_bold_text(phone)
    assert started_state is not None, "could not find the Bold Text row to begin with"

    if started_state == "1":
        await phone.set_value("off", target="Bold Text")
    await phone.terminate_app(SETTINGS_APP)
    await phone.launch_app(SETTINGS_APP, fresh=True)

    backend = SessionBackend(phone)
    began = time.monotonic()
    try:
        outcome = await run_goal(phone, GOAL, backend=backend)
        elapsed = time.monotonic() - began

        after = await _read_bold_text(phone)

        print(
            f"\n  {backend.stats.actions} actions, {backend.stats.observations} observation(s), "
            f"{backend.stats.device_tokens} device tokens, {elapsed:.1f}s"
        )
        print(f"  agent claimed: {outcome.succeeded}  |  device says: Bold Text = {after}")

        _write_report(backend, outcome, elapsed, after)

        assert after == "1", (
            f"the agent claimed {outcome.succeeded} but Bold Text reads {after!r} on the device"
        )
        assert outcome.succeeded is True, "the switch moved but the agent did not notice"
        assert backend.stats.observations == 1, (
            f"{backend.stats.observations} observations on hardware, where each costs ~3.7s; "
            "the folded-in screens should have been enough"
        )
    finally:
        # Put the phone back however this ended.
        restore = "on" if started_state == "1" else "off"
        await phone.launch_app(SETTINGS_APP, fresh=True)
        await phone.tap(target="Accessibility")
        await phone.tap(target="Display & Text Size")
        await phone.set_value(restore, target="Bold Text")
        print(f"  restored Bold Text to {restore}")


async def test_a_real_no_op_still_reports_honestly(phone: IosSession) -> None:
    """The assumption S2's verifier rests on, on the hardware that could break it.

    Verification decides a no-op from `screen_changed`, a fingerprint
    comparison, and fingerprints round positions to 4px so animation jitter does
    not register. A physical device has real animations and slow snapshots. If a
    settled screen still moved its fingerprint here, a genuine no-op would read
    as progress, the escalation would never fire, and the one pillar this phase
    kept would be quietly dead on hardware while every other test stayed green.

    Setting a switch to the state it already holds is a true no-op, because
    `set_value` is state-aware.
    """
    await phone.launch_app(SETTINGS_APP, fresh=True)
    await phone.tap(target="Accessibility")
    await phone.tap(target="Display & Text Size")
    current = await _read_bold_text(phone)
    wanted = "on" if current == "1" else "off"

    repeat = await phone.set_value(wanted, target="Bold Text")

    assert repeat.ok is True
    assert repeat.screen_changed is False, (
        "a real no-op reported a screen change on hardware, so the verifier "
        "would never escalate and the S2 result does not hold on a device"
    )


async def test_the_digest_holds_its_budget_on_a_real_phone(phone: IosSession) -> None:
    """Real Settings on a real phone, against the same 1500-token budget."""

    def raw_nodes(node: Any) -> int:
        return 1 + sum(raw_nodes(c) for c in (getattr(node, "children", None) or []))

    raw = await phone.wda.source()
    screen = await phone.observe()

    total = raw_nodes(raw)
    print(
        f"\n  device Settings: {total} raw nodes -> {len(screen.nodes)} elements, "
        f"{screen.estimated_tokens()} tokens"
    )

    assert total > len(screen.nodes) * 2, "not a realistic screen"
    assert screen.estimated_tokens() <= 1500 * 1.1


def _write_report(backend: Any, outcome: Any, elapsed: float, device_state: str | None) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "generated_at": time.time(),
                "tier": "3-physical-device",
                "model": AgentSettings().describe(),
                "goal": GOAL,
                "actions": backend.stats.actions,
                "observations": backend.stats.observations,
                "refusals": backend.stats.refusals,
                "device_tokens": backend.stats.device_tokens,
                "prompt_tokens": outcome.prompt_tokens,
                "completion_tokens": outcome.completion_tokens,
                "seconds": round(elapsed, 1),
                "agent_claimed": outcome.succeeded,
                "device_state_after": device_state,
                "summary": outcome.summary[:300],
            },
            indent=2,
        )
    )
