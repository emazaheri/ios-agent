"""Tier 2: the agent against a real iOS Simulator.

Every number this phase has produced came from a scripted in-process fake that
I wrote, which makes them a claim about my model of iOS rather than about iOS.
This project's own testing convention is blunt about what that is worth:

    no fake has ever caught a perception-geometry or device-lifecycle bug --
    every one came from a real run. Anything touching those needs hardware
    before it is believed.

So this exists to try to break the conclusions rather than to confirm them.
What the fake cannot have tested:

- **Timing.** A real snapshot takes about a second against the fake's
  microseconds, and the settle loop polls a fingerprint until it repeats. In
  the fake every action settles instantly.
- **Scale.** Real Settings emits hundreds of raw nodes that the digest compacts
  to a dozen. The synthetic trees start close to their final shape, so
  budgeting and truncation are barely exercised.
- **Geometry.** `_dedupe_colocated` takes semantics from a labelled row and
  geometry from the tighter switch inside it. The fake reproduces that split
  because it was built to; only a real screen proves it.
- **The verifier's central assumption.** S2 keys entirely on `screen_changed`,
  a fingerprint comparison. If a real device jitters its fingerprint between
  snapshots, a no-op reads as progress, the escalation never fires, and the one
  pillar this phase kept silently stops working.

Verification is done by this module reading the device directly after the run,
not by trusting what the agent said. Those checks cost actions, and they are
deliberately outside the agent's measured budget: they are the test's evidence,
not the agent's work.

The injected-failure tasks are left out. A real switch is not dead and a real
deep link is not silently ignored on this build, so running them here would be
measuring the fake again with extra steps.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent_driver import requires_a_model
from ios_agent import AgentSettings, SessionBackend, run_goal
from simulator_support import requires_simulator

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession

pytestmark = [
    pytest.mark.agent,
    pytest.mark.model,
    pytest.mark.simulator,
    requires_simulator,
    requires_a_model,
]

REPORT = Path(".artifacts/evals/agent-tier2-simulator.json")
SETTINGS_APP = "com.apple.Preferences"


def simulator_settings() -> Settings:
    cfg = Settings()
    # A real snapshot is orders of magnitude slower than the fake's, and
    # max_wait_s must exceed stable_samples snapshots or the settle loop can
    # never converge on a real device.
    cfg.wda.startup_timeout_s = 240.0
    cfg.stabilize.max_wait_s = 8.0
    cfg.policy.confirm_destructive = False  # a prompt would hang a headless run
    cfg.policy.loop_detection_window = 50  # tasks revisit panes deliberately
    return cfg


@pytest.fixture(scope="module")
def sim_settings() -> Settings:
    return simulator_settings()


@pytest.fixture(scope="module")
async def pool(sim_settings: Settings):
    made = DevicePool(sim_settings)
    yield made
    await made.release_all()


@pytest.fixture
async def session(pool: DevicePool, sim_settings: Settings) -> IosSession:
    """Settings, opened at its root screen.

    Terminating first is not optional. iOS 26 accepts `App-prefs:root` while a
    sub-pane is showing and leaves it there, so without this each task would
    inherit wherever the previous one stopped, which is the `STALE_START`
    injection arriving for real and uninvited.
    """
    lease = await pool.acquire(bundle_id=SETTINGS_APP)
    opened = IosSession(lease, sim_settings)
    await opened.terminate_app(SETTINGS_APP)
    await opened.launch_app(SETTINGS_APP, fresh=True)
    return opened


_measured: list[dict[str, object]] = []


async def _run(session: IosSession, goal: str) -> SessionBackend:
    backend = SessionBackend(session)
    outcome = await run_goal(session, goal, backend=backend)
    _measured.append(
        {
            "goal": goal,
            "actions": backend.stats.actions,
            "observations": backend.stats.observations,
            "refusals": backend.stats.refusals,
            "device_tokens": backend.stats.device_tokens,
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
            "claimed": outcome.succeeded,
            "summary": outcome.summary[:200],
        }
    )
    print(
        f"\n  {backend.stats.actions:>2} act  {backend.stats.observations} obs  "
        f"{backend.stats.device_tokens:>5} dev tok  claimed={outcome.succeeded}  {goal}"
    )
    return backend


async def test_it_reaches_a_pane_it_has_to_navigate_to(session: IosSession) -> None:
    """The simplest real thing: find a row and open it.

    Failing here would mean the digest, resolution or the tap geometry does not
    survive contact with real Settings, which would invalidate every tier 1
    number rather than just this one.
    """
    await _run(session, "Open the Accessibility settings.")

    screen = await session.observe()

    assert (screen.title or "").lower().startswith("accessibility"), (
        f"ended on {screen.title!r}, not Accessibility"
    )


async def test_it_changes_a_real_switch_and_the_device_agrees(session: IosSession) -> None:
    """A state change, checked against the device rather than the agent's word.

    The check navigates there itself. Reading the agent's summary would only
    confirm that it believes itself, which is exactly the failure the
    dead-switch task exists to catch.
    """
    await _run(session, "Turn on Bold Text in Settings.")

    # The test's own navigation, outside the agent's measured budget.
    await session.launch_app(SETTINGS_APP, fresh=True)
    await session.tap(target="Accessibility")
    await session.tap(target="Display & Text Size")
    screen = await session.observe()

    row = next((n for n in screen.nodes if n.label and "bold text" in n.label.lower()), None)
    assert row is not None, f"no Bold Text row on:\n{screen.render()}"
    assert row.value == "1", f"Bold Text reads {row.value!r}, so the agent did not set it"

    # Leave the simulator as it was found.
    await session.set_value("off", target="Bold Text")


def _raw_node_count(node: object) -> int:
    children = getattr(node, "children", None) or []
    return 1 + sum(_raw_node_count(c) for c in children)


async def test_a_real_screen_still_fits_the_budget(session: IosSession) -> None:
    """Real Settings is where the digest earns its keep.

    Compaction is measured against the *raw* accessibility tree, not against
    `Digest.total_nodes`. That field counts what survived pruning and before
    budget truncation, so on a screen that fits the budget it equals the
    element count and says nothing about how much was thrown away. A first
    version of this test asserted on it and failed on a perfectly good screen.
    """
    raw = await session.wda.source()
    screen = await session.observe()

    raw_nodes = _raw_node_count(raw)
    print(
        f"\n  real Settings: {raw_nodes} raw nodes -> {len(screen.nodes)} elements, "
        f"{screen.estimated_tokens()} tokens"
    )

    assert raw_nodes > len(screen.nodes) * 2, (
        f"only {raw_nodes} raw nodes for {len(screen.nodes)} elements; "
        "this screen is not realistic enough to be evidence"
    )
    assert screen.estimated_tokens() <= 1500 * 1.1, (
        f"{screen.estimated_tokens()} tokens over a 1500 budget"
    )


async def test_the_verifier_does_not_misfire_on_real_timing(session: IosSession) -> None:
    """The assumption S2 rests on, tested where it could actually break.

    Verification decides a no-op from `screen_changed`, a fingerprint
    comparison. On a real device snapshots are slow and animations are real, so
    if a settled screen still jitters its fingerprint, a genuine no-op would
    read as progress and the escalation would never fire.

    Setting a switch to the state it already holds is a true no-op: `set_value`
    is state-aware and does nothing. The device must report that honestly.
    """
    await session.tap(target="Accessibility")
    await session.tap(target="Display & Text Size")
    await session.set_value("off", target="Bold Text")

    repeat = await session.set_value("off", target="Bold Text")

    assert repeat.ok is True
    assert repeat.screen_changed is False, (
        "a real no-op reported a screen change, so the verifier would never escalate"
    )


def test_write_the_tier2_report() -> None:
    if not _measured:
        pytest.skip("nothing ran")
    import json
    import time

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "generated_at": time.time(),
                "tier": "2-simulator",
                "model": AgentSettings().describe(),
                "runs": _measured,
            },
            indent=2,
        )
    )
    print(f"\nWrote {REPORT}")
    assert os.path.getsize(REPORT) > 0
