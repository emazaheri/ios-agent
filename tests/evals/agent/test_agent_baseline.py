"""The skeleton's numbers. Tier 1: scripted device, real model, no hardware.

This is the baseline every later slice has to beat, and it is expected to look
bad. The loop has no plan, no verification step and no memory, so it should
sit near an observation overhead of 1.0: a model with nothing else to go on
looks before every move. Recording that honestly is the whole point. A
baseline that already contains half the ideas cannot show what any of them
bought.

Skipped without credentials. There is no offline substitute worth having here:
the question is whether a real model chooses well, and a scripted one cannot
answer it. The loop's mechanics are covered without a network by
`tests/unit/test_agent_loop.py`.
"""

from __future__ import annotations

from pathlib import Path

import agent_driver
import pytest
from agent_driver import requires_credentials
from measure import TaskResult, write_report
from tasks import TASKS, Task
from test_agent_evals import measure

pytestmark = [pytest.mark.agent, pytest.mark.model, requires_credentials]

REPORT = Path(".artifacts/evals/agent-baseline.json")

#: A model is not deterministic and `temperature` is rejected outright on
#: Claude Opus 5, so variance is controlled by repeating the run and reporting
#: a median with its range, not by pinning sampling.
RUNS_PER_TASK = 3

_results: list[TaskResult] = []


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
async def test_the_skeleton_on_one_task(task: Task) -> None:
    """Record what the skeleton costs. Nothing is asserted about quality.

    Deliberately not a pass/fail gate. Slice 1 exists to produce a number, and
    a threshold invented before the first measurement would be a guess dressed
    up as a requirement. The gates arrive in slice 2, against these numbers.
    """
    result = await measure(task, agent_driver.drive, RUNS_PER_TASK)
    _results.append(result)
    print("\n" + result.render())


def test_write_the_baseline_report() -> None:
    if not _results:
        pytest.skip("no tasks ran")
    path = write_report(_results, REPORT, driver="skeleton")
    assert path.exists()
    print(f"\nWrote {path}")
