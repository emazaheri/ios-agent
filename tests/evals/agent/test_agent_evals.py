"""Run the task set and record what it cost.

Tier 1: a scripted device in-process, no simulator and no phone. Fast enough
to run on every commit, which is the only way a regression gets noticed while
it is still one slice old.

Today the driver is the oracle, so this measures the floor. When the agent
exists it becomes a second driver over the same tasks and the same metrics,
and the two columns are the argument.
"""

from __future__ import annotations

from pathlib import Path

import oracle
import pytest
from measure import Driver, TaskResult, run_task, write_report
from screens import build_session
from tasks import TASKS, Task

from ios_mcp.config import Settings

pytestmark = pytest.mark.agent

REPORT = Path(".artifacts/evals/agent.json")

#: The oracle is deterministic, so one run per task says everything. A model
#: driver will need several, which is why the harness reduces to a median.
ORACLE_RUNS = 1


def eval_settings(task: Task) -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    # Tasks revisit screens deliberately: recovering from a wrong turn means
    # going back through one. Loop detection would read that as thrashing.
    cfg.policy.loop_detection_window = 50
    # The gate stays on only where being refused is the thing under test.
    # Everywhere else an approval prompt would hang a headless run, and
    # `on_approval` is deliberately left unset so a refusal raises with a
    # signature rather than silently proceeding.
    cfg.policy.confirm_destructive = task.must_be_blocked
    return cfg


async def measure(task: Task, driver: Driver, runs: int) -> TaskResult:
    result = TaskResult(task=task.name)
    for _ in range(runs):
        model = task.model()
        session, _fake, _adapter = build_session(model, eval_settings(task))
        result.runs.append(await run_task(task, model, session, driver))
    return result


_results: list[TaskResult] = []


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
async def test_the_oracle_reaches_the_goal(task: Task) -> None:
    """Every task must be solvable by someone who knows the route.

    A task the oracle cannot finish is a broken task or a missing tool, not a
    hard one, and measuring an agent against it would be measuring noise.
    """
    result = await measure(task, oracle.drive, ORACLE_RUNS)
    _results.append(result)
    print("\n" + result.render())

    failures = [run.failure for run in result.runs if not run.passed]
    assert result.success_rate == 1.0, f"{task.name}: {failures}"


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
async def test_the_oracle_spends_no_more_than_its_floor(task: Task) -> None:
    """The floor recorded on the task is the floor the oracle actually pays.

    Keeping these equal is what stops `floor` drifting into an aspiration. If
    a tool change makes a route cost an extra observation, this fails and the
    number in `tasks.py` gets updated deliberately rather than by accident.
    """
    result = await measure(task, oracle.drive, ORACLE_RUNS)
    observations = [run.observations for run in result.runs]
    assert observations == [task.floor] * ORACLE_RUNS, (
        f"{task.name}: oracle spent {observations}, floor says {task.floor}"
    )


def test_write_the_report() -> None:
    """Persist the floor so a later slice can be diffed against it."""
    if not _results:
        pytest.skip("no tasks ran")
    path = write_report(_results, REPORT, driver="oracle")
    assert path.exists()
    print(f"\nWrote {path}")


def test_every_task_is_reachable_from_the_report() -> None:
    """A guard against a task being added and quietly never measured."""
    assert {r.task for r in _results} == {t.name for t in TASKS}
