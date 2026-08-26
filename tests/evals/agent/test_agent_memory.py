"""Does remembering a failure make the second encounter cheaper?

Nothing else in the eval suite measures a repeat. Every task starts from a
fresh device and a fresh agent, which is the right way to measure planning or
verification and the wrong way to measure memory: memory only pays the second
time, and a suite of first encounters would show it costing tokens and buying
nothing.

The bet is narrow and comes from the S2 numbers rather than from the plan. The
agent is at the oracle's action floor on eight of ten tasks, so remembering a
route recovers nothing. The whole remaining gap in the set is one task, the
dead-switch injection, where roughly six actions go on discovering that a
control is a lie. That discovery is what a second encounter should not have to
repeat.

Two arms, same harness, same tasks:

- **off**: memory disabled, so encounter two is a fresh discovery. The control.
- **on**: notes carried from encounter one into encounter two.

`enable_bold_text` is in the set as the second control. It already runs at the
floor, so memory cannot help it, and the thing worth checking is that a
briefing does not make it worse.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agent_driver import requires_a_model
from ios_agent import AgentSettings, Memory, SessionBackend, run_goal
from measure import RunResult, run_task
from screens import build_session
from tasks import BY_NAME, Task
from test_agent_evals import eval_settings

pytestmark = [pytest.mark.agent, pytest.mark.model, requires_a_model]

REPORT = Path(".artifacts/evals/agent-s4-memory.json")

#: The one task with measured headroom, and one that has none. Keeping the
#: second is what turns "memory helped" into "memory helped and cost nothing
#: elsewhere", which is the claim actually worth making.
TASKS = ("enable_airplane_mode", "enable_bold_text")
ENCOUNTERS = 2
REPEATS = 3


async def _encounter(task: Task, memory: Memory | None) -> RunResult:
    """One meeting with a task, on a device that remembers nothing itself."""
    model = task.model()
    session, _, _ = build_session(model, eval_settings(task))

    async def drive(t: Task, s: object, meter: object) -> None:
        backend = SessionBackend(session, memory=memory)
        outcome = await run_goal(session, t.goal, backend=backend, memory=memory)
        meter.observations = backend.stats.observations  # type: ignore[attr-defined]
        meter.actions = backend.stats.actions  # type: ignore[attr-defined]
        meter.device_tokens = backend.stats.device_tokens  # type: ignore[attr-defined]
        meter.refusals = backend.stats.refusals  # type: ignore[attr-defined]
        meter.charge_model(outcome.prompt_tokens, outcome.completion_tokens)  # type: ignore[attr-defined]
        meter.last_screen = backend.last_screen  # type: ignore[attr-defined]

    return await run_task(task, model, session, drive)


async def _arm(task: Task, *, remembering: bool) -> list[list[RunResult]]:
    """`REPEATS` independent trials, each of `ENCOUNTERS` meetings."""
    trials: list[list[RunResult]] = []
    for _ in range(REPEATS):
        # Fresh memory per trial. Carrying it across trials would let trial
        # three start from three trials of learning and make the arms
        # incomparable.
        memory = (
            Memory(assertive=os.environ.get("IOS_AGENT_MEMORY_ASSERTIVE") == "1")
            if remembering
            else None
        )
        trials.append([await _encounter(task, memory) for _ in range(ENCOUNTERS)])
    return trials


def _median(values: list[int]) -> int:
    return sorted(values)[len(values) // 2]


_summary: dict[str, dict[str, list[int]]] = {}


@pytest.mark.parametrize("name", TASKS)
@pytest.mark.parametrize("remembering", [False, True], ids=["memory-off", "memory-on"])
async def test_the_second_encounter(name: str, remembering: bool) -> None:
    task = BY_NAME[name]
    trials = await _arm(task, remembering=remembering)

    first = [t[0].actions for t in trials]
    second = [t[-1].actions for t in trials]
    arm = "on" if remembering else "off"
    _summary.setdefault(name, {})[f"{arm}-first"] = first
    _summary.setdefault(name, {})[f"{arm}-second"] = second

    print(
        f"\n  {name:22} memory {arm:3}  "
        f"first {_median(first):>2} {first}  second {_median(second):>2} {second}"
    )

    # Not a threshold. The slice exists to produce the comparison, and a bar
    # invented before the first measurement is a guess wearing a requirement's
    # clothes. Success is asserted because a cheaper run that fails is not
    # cheaper, it is broken.
    assert all(r.passed for t in trials for r in t), (
        f"{name}: {[r.failure for t in trials for r in t if not r.passed]}"
    )


def test_write_the_memory_report() -> None:
    if not _summary:
        pytest.skip("no arms ran")
    import json
    import time

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "generated_at": time.time(),
                "model": AgentSettings().describe(),
                "encounters": ENCOUNTERS,
                "repeats": REPEATS,
                "actions": _summary,
            },
            indent=2,
        )
    )
    print(f"\nWrote {REPORT}")
    assert REPORT.exists()
    assert os.path.getsize(REPORT) > 0
