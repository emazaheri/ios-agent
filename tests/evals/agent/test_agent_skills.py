"""Does an agent that reads an app's notes beat one that does not?

The shape is ADR 0003's, because the question is ADR 0003's question wearing
different clothes: both ask what written-down knowledge is worth to an agent
that can see the screen. Two arms over the same tasks and the same harness,
differing only in whether `run_goal` is handed the loader, three trials each,
medians reported with the full per-trial list.

Which tasks, and why those:

* `set_quiet_hours` is the only one with discovery in it. It starts outside an
  app nobody has seen, the profile tab is the third of three unlabelled
  glyphs, and the app calls its notification screen Nudges. The oracle needs
  four actions; a first encounter does not know any of that.
* `enable_bold_text` is the control with no headroom, used for exactly this
  purpose in ADR 0003, where it was three actions in every run of every arm.
  A briefing that moves it has moved something other than route knowledge.
* `like_a_card` and `read_a_card_answer` run in the third-party app and are at
  their floors already. They start *inside* it, so they never call `open_app`
  and never receive a briefing at all. They are here so that the null on them
  is recorded rather than assumed, and so a briefing that somehow changes them
  shows up as the bug it would be.

What decides it. Actions against the oracle floor first, since that is the
governing metric from S2 on. Then turns, perception faults and cost, because a
briefing that saves an action and spends a turn has saved nothing, and
`skill_tokens` is on every run so the price is in the report rather than in an
argument about it.

Nothing here asserts an improvement. The arms are recorded and ADR 0012 reads
them; a threshold written before the measurement would be a guess dressed up
as a requirement, which is the reason `test_agent_baseline.py` gives for not
having one either.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path

import agent_driver
import pytest
from agent_driver import requires_a_model
from ios_agent import AgentSettings
from tasks import BY_NAME
from test_agent_evals import measure

pytestmark = [pytest.mark.agent, pytest.mark.model, requires_a_model]

SLICE = os.environ.get("IOS_AGENT_SLICE", "s10-app-skills")
REPORT = Path(f".artifacts/evals/agent-{SLICE}.json")

#: The task with discovery in it, then the control, then the two that cannot
#: receive a briefing. Ordered by how much each one can say.
TASKS = ("set_quiet_hours", "enable_bold_text", "like_a_card", "read_a_card_answer")

#: Same as ADR 0003's memory arms. Three is enough to see a median move
#: against a metric whose control arm ranged 6 to 13 there, and not enough to
#: make a small difference safe to believe: the ADR says the range, not just
#: the middle, for that reason.
TRIALS = 3

_arms: dict[str, dict[str, list[dict[str, float]]]] = {}


@pytest.mark.parametrize("briefed", [False, True], ids=["skill-off", "skill-on"])
@pytest.mark.parametrize("name", TASKS)
async def test_one_arm_of_one_task(name: str, briefed: bool) -> None:
    task = BY_NAME[name]
    result = await measure(task, agent_driver.driver(briefed=briefed), TRIALS)
    print("\n" + result.render())

    broken = result.unusable
    assert not broken, (
        f"{name}: {len(broken)}/{len(result.runs)} runs measured the provider, "
        f"not the agent: {broken[0].provider_error or broken[0].failure or 'never acted'}"
    )

    _arms.setdefault(name, {})["on" if briefed else "off"] = [
        {
            "actions": run.actions,
            "observations": run.observations,
            "turns": run.turns,
            "prompt_tokens": run.prompt_tokens,
            "skill_tokens": run.skill_tokens,
            "perception_faults": run.faults.get("perception", 0),
            "usd": round(run.usd, 4),
            "passed": run.passed,
        }
        for run in result.runs
    ]


def test_write_the_arms() -> None:
    """Both arms of every task, medians and the runs they came from.

    The per-trial lists are not decoration. ADR 0003's assertive arm had a
    median of 3 actions and a run of 0 inside it, and the 0 is the finding:
    the agent had stopped touching the device. A median alone would have read
    as a threefold improvement.
    """
    if not _arms:
        pytest.skip("no arms ran")
    missing = [name for name, arms in _arms.items() if len(arms) != 2]
    assert not missing, f"only one arm ran for {missing}; the comparison is the whole point"

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": AgentSettings().describe(),
        "trials": TRIALS,
        "tasks": {
            name: {
                arm: {
                    "actions": [r["actions"] for r in runs],
                    "median_actions": statistics.median(r["actions"] for r in runs),
                    "floor": BY_NAME[name].action_floor,
                    "turns": [r["turns"] for r in runs],
                    "perception_faults": sum(r["perception_faults"] for r in runs),
                    "skill_tokens": [r["skill_tokens"] for r in runs],
                    "prompt_tokens": [r["prompt_tokens"] for r in runs],
                    "usd": round(sum(float(r["usd"]) for r in runs), 4),
                    "passed": sum(1 for r in runs if r["passed"]),
                }
                for arm, runs in arms.items()
            }
            for name, arms in _arms.items()
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nWrote {REPORT}")
