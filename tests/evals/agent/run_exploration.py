"""Run ADR 0021's exploratory tasks and keep every report, one run at a time.

The baseline suite writes its report once, at the end, and refuses to write it
at all if any run measured the infrastructure instead of the agent. That is
right for a fixed-goal task and wrong for this one: the report is the result,
each costs a few minutes of model time, and an attempt lost five usable
reports to one unusable run. This writes each run to disk as it finishes,
marks an unusable one as such, and never drops it.

    uv run python tests/evals/agent/run_exploration.py --runs 3 --out .artifacts/evals/explore.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

import agent_driver  # noqa: E402
from measure import run_task  # noqa: E402
from screens import build_session  # noqa: E402
from tasks import BY_NAME  # noqa: E402
from test_agent_evals import eval_settings  # noqa: E402

TASKS = ("explore_signup", "explore_signup_no_spec")


async def main(args: argparse.Namespace) -> int:
    reason = agent_driver.why_unavailable()
    if reason is not None:
        print(f"no model available: {reason}")
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    record: dict = {"started_at": time.time(), "runs": []}
    for name in TASKS:
        task = BY_NAME[name]
        for attempt in range(1, args.runs + 1):
            model = task.model()
            session, _fake, _adapter = build_session(model, eval_settings(task))
            result = await run_task(task, model, session, agent_driver.drive)
            row = {"attempt": attempt, "unusable": result.did_nothing, **result.to_dict()}
            record["runs"].append(row)
            out.write_text(json.dumps(record, indent=2))
            caught = sum(result.planted.get(b, False) for b in result.expected)
            print(
                f"{name} #{attempt}: {caught}/{len(result.expected)} expected bugs, "
                f"{len(result.false_report_candidates)} complaint(s) to read, "
                f"{result.actions} actions, {result.turns} turns, ${result.usd:.3f}"
                + ("  UNUSABLE" if result.did_nothing else ""),
                flush=True,
            )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", default=".artifacts/evals/explore.json")
    sys.exit(asyncio.run(main(parser.parse_args())))
