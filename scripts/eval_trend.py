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
SCHEMA_VERSION = 1

#: Committed, unlike the reports themselves. It lives beside the suites that
#: produce it rather than at the repository root, where `evals/` would read as
#: a sibling of `tests/evals/` rather than part of it.
HISTORY = Path("tests/evals/history.jsonl")

#: Guarded on `check`, in the order they are reported. Every one is a count.
CHECKED = (
    "passed",
    "observations",
    "floor",
    "actions",
    "device_tokens",
    "refusals",
    "runner_recoveries",
    "resolution_tiers",
    "faults",
)

#: Recorded and shown, never checked. The runner decides these.
UNCHECKED = ("seconds",)

#: Fixed, so adjacent lines in a diff line up column-wise.
_KEY_ORDER = (
    "schema_version",
    "at",
    "sha",
    "suite",
    "driver",
    "model",
    "units",
    "passed",
    "observations",
    "floor",
    "actions",
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
        "units": f"{count} {units}",
        "passed": passed,
        "observations": totals.get("observations", 0),
        "floor": totals.get("floor", 0),
        "actions": totals.get("actions", 0),
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
        f"{key}: {baseline.get(key)!r} -> {new.get(key)!r}"
        for key in CHECKED
        if new.get(key) != baseline.get(key)
    ]


def render(rows: list[dict[str, Any]], last: int) -> str:
    """A fixed-width table of the most recent runs, newest last."""
    if not rows:
        return "no runs recorded yet"
    shown = rows[-last:] if last > 0 else rows
    columns = ("at", "sha", "units", "passed", "actions", "device_tokens", "seconds")
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
    drift = compare(record, history[-1])
    if not drift:
        print(f"{args.suite}: unchanged against {history[-1]['sha']}")
        return 0
    print(f"{args.suite} moved against {history[-1]['sha']} ({history[-1]['at']}):")
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
