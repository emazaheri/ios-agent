"""The skeleton's numbers. Tier 1: scripted device, real model, no hardware.

This is the baseline every later slice has to beat, and it is expected to look
bad. The loop has no plan, no verification step and no memory, so it should
sit near an observation overhead of 1.0: a model with nothing else to go on
looks before every move. Recording that honestly is the whole point. A
baseline that already contains half the ideas cannot show what any of them
bought.

Skipped when no model is reachable. Which model that is comes from
`AgentSettings`, so this runs against OpenAI or a local Ollama just as happily;
Anthropic is only the default. There is no offline substitute worth having:
the question is whether a real model chooses well, and a scripted one cannot
answer it. The loop's mechanics are covered without a network by
`tests/unit/test_agent_loop.py`.
"""

from __future__ import annotations

from pathlib import Path

import agent_driver
import pytest
from agent_driver import requires_a_model
from ios_agent import AgentSettings
from measure import TaskResult, write_report
from tasks import TASKS, Task
from test_agent_evals import measure

pytestmark = [pytest.mark.agent, pytest.mark.model, requires_a_model]

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

    # The one thing worth failing on. A run that died on an exhausted credit
    # balance or a rejected key measured the provider, not the agent, and
    # recording it as a failed task would put a number in the baseline that
    # looks like evidence and is not.
    broken = result.unusable
    assert not broken, (
        f"{task.name}: {len(broken)}/{len(result.runs)} runs measured the "
        f"infrastructure, not the agent: "
        f"{broken[0].provider_error or broken[0].failure or 'the run never acted'}"
    )


def test_write_the_baseline_report() -> None:
    if not _results:
        pytest.skip("no tasks ran")
    # A report is a claim. Writing one from runs that never reached a model
    # would put a number on disk that looks like a measurement and is not.
    unusable = sum(len(r.unusable) for r in _results)
    assert unusable == 0, f"{unusable} runs measured the infrastructure; refusing to record them"
    path = write_report(_results, REPORT, driver="skeleton", model=AgentSettings().describe())
    assert path.exists()
    print(f"\nWrote {path}")
