#!/usr/bin/env python3
"""Keep the eval numbers over time, and fail when they move unannounced.

The eval suites have always measured the right things and always thrown the
measurement away: reports land in `.artifacts/`, which is gitignored, under a
filename that is overwritten in place, with no commit attached. Every ceiling
in the suite is therefore a per-run one. A flow drifting from `exact` toward
`text-fuzzy`, or a digest quietly costing 8% more per screen, passes every
assertion right up until it breaks.

This appends one line per measured run to a committed history, and compares a
fresh report against the last line for its suite.

    python scripts/eval_trend.py append .artifacts/evals/agent.json --suite agent-oracle
    python scripts/eval_trend.py show --suite agent-oracle --last 10
    python scripts/eval_trend.py check .artifacts/evals/agent.json --suite agent-oracle

`check` is exact. Every metric it guards is a count over a scripted device with
no model, no network and no clock in it, so a tolerance band would only be a
licence to drift. `seconds` is recorded and shown but never checked, because it
is the one number the machine running the suite decides.

Stdlib only, and it imports nothing from `tests/`, so it runs anywhere the
repository is checked out.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Bumped when a record's shape changes in a way a reader must notice.
SCHEMA_VERSION = 4

#: Committed, unlike the reports themselves. It lives beside the suites that
#: produce it rather than at the repository root, where `evals/` would read as
#: a sibling of `tests/evals/` rather than part of it.
HISTORY = Path("tests/evals/history.jsonl")

#: Guarded on `check`, in the order they are reported. Every one is a count.
CHECKED = (
    "passed",
    "observations",
    "floor",
    "finds",
    "actions",
    "turns",
    "turn_floor",
    "device_tokens",
    "refusals",
    "runner_recoveries",
    "resolution_tiers",
    "faults",
)

#: Recorded and shown, never checked. The runner decides these.
UNCHECKED = ("seconds",)

#: Not a count and deliberately not in `CHECKED`. It identifies the system that
#: produced the counts, so it decides whether comparing them means anything at
#: all rather than being one more number to diff.
#:
#: Guarding it would also misfire once: `_baseline` substitutes `type(value)()`
#: for a key the baseline lacks, which for a string is `""`, so the first run
#: after this landed would report `prompt_sha: '' -> ...` on every suite and
#: exit non-zero for no reason.
IDENTITY = "prompt_sha"

#: Fixed, so adjacent lines in a diff line up column-wise.
_KEY_ORDER = (
    "schema_version",
    "at",
    "sha",
    "suite",
    "driver",
    "model",
    "model_served",
    "prompt_sha",
    "units",
    "passed",
    "observations",
    "floor",
    "finds",
    "actions",
    "turns",
    "turn_floor",
    "device_tokens",
    "tokens_per_step",
    "refusals",
    "runner_recoveries",
    "seconds",
    "resolution_tiers",
    "faults",
    "note",
)


def git_sha() -> str:
    """The commit these numbers describe, or `unknown` outside a checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def flatten(report: dict[str, Any], *, suite: str, note: str | None = None) -> dict[str, Any]:
    """One history record from either report shape.

    Both writers emit `totals`; the agent suite counts tasks and the golden
    flows count flows, so `units` records which was measured rather than
    pretending the two are the same number.
    """
    version = report.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"report is schema_version {version!r}, this script reads {SCHEMA_VERSION}. "
            "Regenerate the report, or teach flatten() the older shape."
        )
    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("report has no totals block")

    if "tasks" in report:
        units, count = "runs", totals.get("runs", 0)
        passed = round(totals.get("success_rate", 0.0) * count)
    elif "flows" in report:
        units, count = "flows", totals.get("flows", 0)
        passed = totals.get("passed", 0)
    else:
        raise ValueError("report is neither a flow report nor a task report")

    record = {
        "schema_version": SCHEMA_VERSION,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha": git_sha(),
        "suite": suite,
        "driver": report.get("driver", "flows"),
        "model": report.get("model"),
        # What the provider says it actually ran, against `model` above, which
        # is what was asked for. `claude-opus-5` is an alias and not a version,
        # so a provider moving underneath it is invisible in `model` alone.
        # Absent from a report with no model in the loop, and None from a
        # provider that does not say.
        "model_served": report.get("model_served"),
        # The operator prompt that produced these counts. A prompt is an input
        # to the system, so two runs under different prompts are two systems and
        # their counts are not comparable. See `IDENTITY`.
        "prompt_sha": report.get("prompt_sha"),
        "units": f"{count} {units}",
        "passed": passed,
        "observations": totals.get("observations", 0),
        "floor": totals.get("floor", 0),
        # Apart from `observations` on purpose: a find returns no refs and is
        # not a screen, so counting it as one would move the floor every task
        # is asserted against. Zero in the guarded series, and checked for it.
        "finds": totals.get("finds", 0),
        "actions": totals.get("actions", 0),
        # Zero in both guarded series, and checked for it: the agent-oracle
        # suite has no model, so a `turns` that ever moved would mean one had
        # been let into the series CI runs for free. `turn_floor` is the real
        # guard here, a count over a fixed route derived from the batch rule.
        "turns": totals.get("turns", 0),
        "turn_floor": totals.get("turn_floor", 0),
        "device_tokens": totals.get("device_tokens", totals.get("tokens", 0)),
        "tokens_per_step": totals.get("tokens_per_step", 0.0),
        "refusals": totals.get("refusals", 0),
        "runner_recoveries": totals.get("runner_recoveries", 0),
        "seconds": totals.get("seconds", 0.0),
        "resolution_tiers": totals.get("resolution_tiers", {}),
        "faults": totals.get("faults", {}),
        "note": note,
    }
    return {key: record[key] for key in _KEY_ORDER}


def append(record: dict[str, Any], path: Path) -> None:
    """Add one line. Existing bytes are never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load(path: Path, *, suite: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if suite is None or r.get("suite") == suite]


def compare(new: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Every guarded metric that moved, as `name: old -> new`."""
    return [
        f"{key}: {_baseline(baseline, key, new.get(key))!r} -> {new.get(key)!r}"
        for key in CHECKED
        if new.get(key) != _baseline(baseline, key, new.get(key))
    ]


def _baseline(baseline: dict[str, Any], key: str, new_value: Any) -> Any:
    """What the baseline said, for a key it may predate.

    A metric added after a record was written is absent from it, and absent is
    not the same claim as moved: comparing 0 against `None` would fail every
    guarded suite once, on the run that introduced the metric, and the failure
    would say the count changed when nothing did. An older record is read as
    the empty value of whatever the new one carries -- 0 for a count, {} for a
    histogram -- which is exactly what a suite that never did the thing would
    have recorded.
    """
    if key in baseline:
        return baseline[key]
    return type(new_value)() if new_value is not None else None


def render(rows: list[dict[str, Any]], last: int) -> str:
    """A fixed-width table of the most recent runs, newest last."""
    if not rows:
        return "no runs recorded yet"
    shown = rows[-last:] if last > 0 else rows
    # `turns` and `turn_floor` are both here because which one is filled
    # says which driver produced the row: a model run has turns and no
    # ceiling, the oracle the reverse. A column of zeroes is therefore
    # information rather than clutter.
    columns = (
        "at",
        "sha",
        "units",
        "passed",
        "actions",
        "turns",
        "turn_floor",
        "device_tokens",
        "seconds",
    )
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in shown)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    lines = [header, "-" * len(header)]
    for row in shown:
        lines.append("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))
        tiers = row.get("resolution_tiers") or {}
        faults = row.get("faults") or {}
        detail = f"    tiers {tiers or '{}'}"
        if faults:
            detail += f"  faults {faults}"
        if row.get("note"):
            detail += f"  ({row['note']})"
        lines.append(detail)
    return "\n".join(lines)


def _read(report_path: Path) -> dict[str, Any]:
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def _cmd_append(args: argparse.Namespace) -> int:
    record = flatten(_read(args.report), suite=args.suite, note=args.note)
    append(record, args.history)
    print(f"recorded {args.suite} at {record['sha']} in {args.history}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    print(render(load(args.history, suite=args.suite), args.last))
    return 0


def comparable(record: dict[str, Any], baseline: dict[str, Any]) -> str | None:
    """Why these two runs cannot be diffed, or None when they can.

    A different operator prompt is a different system, and diffing counts across
    two systems is the drift this file exists to prevent: the number moved, and
    nothing says which of the two changes moved it. So a changed `prompt_sha`
    stops the comparison rather than appearing inside it.

    A baseline with no `prompt_sha` at all is every row recorded before this
    landed. Refusing those would restart all three series on the day the field
    arrived, so they compare, and say so.
    """
    mine, theirs = record.get(IDENTITY), baseline.get(IDENTITY)
    if theirs is None or mine is None:
        return None
    if mine != theirs:
        return f"the operator prompt changed: {theirs} -> {mine}"
    return None


def _cmd_check(args: argparse.Namespace) -> int:
    record = flatten(_read(args.report), suite=args.suite)
    history = load(args.history, suite=args.suite)
    if not history:
        print(
            f"no baseline for {args.suite!r} in {args.history}. "
            f"Record one with:\n  python {Path(__file__).name} append "
            f"{args.report} --suite {args.suite}"
        )
        return 1
    baseline = history[-1]
    incomparable = comparable(record, baseline)
    if incomparable is not None:
        print(f"{args.suite}: {incomparable}")
        print(
            "\nThese counts are not comparable, so they were not compared. "
            "Record a new baseline for the new prompt:\n  python "
            f"scripts/eval_trend.py append {args.report} --suite {args.suite}"
        )
        return 1
    if baseline.get(IDENTITY) is None and record.get(IDENTITY) is not None:
        print(
            f"{args.suite}: the baseline predates prompt recording, so this "
            "comparison assumes the prompt did not change."
        )
    drift = compare(record, baseline)
    if not drift:
        print(f"{args.suite}: unchanged against {baseline['sha']}")
        return 0
    print(f"{args.suite} moved against {baseline['sha']} ({baseline['at']}):")
    for line in drift:
        print(f"  {line}")
    print(
        "\nIf that is the price of a deliberate change, record it in the same "
        "commit:\n  python scripts/eval_trend.py append "
        f"{args.report} --suite {args.suite}"
    )
    return 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", type=Path, default=HISTORY)
    sub = parser.add_subparsers(dest="command", required=True)

    p_append = sub.add_parser("append", help="record a report in the history")
    p_append.add_argument("report", type=Path)
    p_append.add_argument("--suite", required=True)
    p_append.add_argument("--note", default=None)
    p_append.set_defaults(func=_cmd_append)

    p_show = sub.add_parser("show", help="print the recorded runs")
    p_show.add_argument("--suite", default=None)
    p_show.add_argument("--last", type=int, default=10)
    p_show.set_defaults(func=_cmd_show)

    p_check = sub.add_parser("check", help="compare a report against the last recorded run")
    p_check.add_argument("report", type=Path)
    p_check.add_argument("--suite", required=True)
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args(list(argv) if argv is not None else None)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
