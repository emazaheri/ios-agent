"""Run the golden flows and assert their cost stays in budget."""

from __future__ import annotations

from pathlib import Path

import pytest
from flows import FLOWS
from harness import EvalResult, run_flow, write_report
from simulator_support import requires_simulator

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession

pytestmark = [pytest.mark.eval, pytest.mark.simulator, requires_simulator]

#: Cost ceiling per charged tool call. A screen that cannot be described
#: inside this is one the digest has failed to compact.
MAX_TOKENS_PER_STEP = 900
#: A flow slower than this is not usable interactively.
MAX_SECONDS_PER_FLOW = 120.0


@pytest.fixture(scope="module")
def eval_settings() -> Settings:
    cfg = Settings()
    cfg.wda.startup_timeout_s = 240.0
    cfg.stabilize.max_wait_s = 8.0
    cfg.policy.confirm_destructive = False  # keep the suite non-interactive
    cfg.policy.loop_detection_window = 50  # flows revisit screens deliberately
    return cfg


@pytest.fixture(scope="module")
async def eval_pool(eval_settings: Settings):
    pool = DevicePool(eval_settings)
    yield pool
    await pool.release_all()


@pytest.fixture
async def fresh_session(eval_pool: DevicePool, eval_settings: Settings) -> IosSession:
    """A session whose app starts at its root screen.

    Terminating first is not optional: iOS 26 accepts `App-prefs:root` while
    Settings is showing a sub-pane and leaves it there, so without this each
    flow would inherit wherever the previous one stopped.
    """
    lease = await eval_pool.acquire()
    session = IosSession(lease, eval_settings)
    await session.terminate_app("com.apple.Preferences")
    await session.launch_app("com.apple.Preferences", fresh=True)
    return session


_results: list[EvalResult] = []


@pytest.mark.parametrize("flow_name", list(FLOWS))
async def test_golden_flow(flow_name: str, fresh_session: IosSession) -> None:
    result = await run_flow(flow_name, fresh_session, FLOWS[flow_name])
    _results.append(result)
    print("\n" + result.render())

    assert result.passed, result.failure

    if result.steps:
        assert result.tokens_per_step <= MAX_TOKENS_PER_STEP, (
            f"{result.tokens_per_step:.0f} tokens per step; the digest is not compacting enough"
        )
    assert result.seconds <= MAX_SECONDS_PER_FLOW, f"{result.seconds:.0f}s is too slow"


def test_write_the_report() -> None:
    """Persist the run so the numbers can be diffed against a previous commit."""
    if not _results:
        pytest.skip("no flows ran")
    path = write_report(_results, Path(".artifacts/evals/latest.json"))
    assert path.exists()
    print(f"\nWrote {path}")
