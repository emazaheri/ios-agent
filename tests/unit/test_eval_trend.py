"""The committed eval history, and the guard that reads it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval_trend import CHECKED, append, compare, flatten, load, main, render

FLOW_REPORT = {
    "schema_version": 1,
    "generated_at": 0.0,
    "totals": {
        "flows": 11,
        "passed": 11,
        "tokens": 8648,
        "seconds": 93.9,
        "actions": 13,
        "steps": 27,
        "tokens_per_step": 320.3,
        "resolution_tiers": {"exact": 2, "text-exact": 3},
        "faults": {},
        "runner_recoveries": 0,
    },
    "flows": [],
}

TASK_REPORT = {
    "schema_version": 1,
    "generated_at": 0.0,
    "driver": "oracle",
    "model": "n/a (no model in the loop)",
    "totals": {
        "tasks": 13,
        "runs": 13,
        "success_rate": 1.0,
        "unusable_runs": 0,
        "observations": 13,
        "floor": 13,
        "actions": 36,
        "device_tokens": 7613,
        "refusals": 0,
        "seconds": 0.3,
        "resolution_tiers": {"text-exact": 32},
        "faults": {"policy": 1},
        "runner_recoveries": 0,
    },
    "tasks": [],
}


def test_a_task_report_flattens_to_its_counts() -> None:
    record = flatten(TASK_REPORT, suite="agent-oracle")
    assert record["units"] == "13 runs"
    assert record["passed"] == 13
    assert record["device_tokens"] == 7613
    assert record["faults"] == {"policy": 1}
    assert record["suite"] == "agent-oracle"


def test_a_flow_report_flattens_too() -> None:
    """The two suites write different shapes, and the history holds both."""
    record = flatten(FLOW_REPORT, suite="golden-flows")
    assert record["units"] == "11 flows"
    assert record["passed"] == 11
    assert record["device_tokens"] == 8648, "a flow report calls its tokens 'tokens'"


def test_an_unreadable_schema_fails_loudly() -> None:
    """Silently mis-flattening an old report would poison the baseline."""
    with pytest.raises(ValueError, match="schema_version"):
        flatten({"schema_version": 99, "totals": {}, "tasks": []}, suite="x")


def test_a_report_that_is_neither_shape_is_rejected() -> None:
    with pytest.raises(ValueError, match="neither"):
        flatten({"schema_version": 1, "totals": {}}, suite="x")


def test_appending_never_rewrites_what_is_there(tmp_path: Path) -> None:
    """The history is a record, so an earlier line must be immutable."""
    history = tmp_path / "history.jsonl"
    first = flatten(TASK_REPORT, suite="agent-oracle")
    append(first, history)
    before = history.read_bytes()

    append(flatten(FLOW_REPORT, suite="golden-flows"), history)
    after = history.read_bytes()

    assert after.startswith(before)
    assert len(history.read_text().splitlines()) == 2


def test_the_history_holds_several_suites_apart(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    append(flatten(TASK_REPORT, suite="agent-oracle"), history)
    append(flatten(FLOW_REPORT, suite="golden-flows"), history)

    assert len(load(history)) == 2
    assert len(load(history, suite="agent-oracle")) == 1
    assert load(history, suite="agent-oracle")[0]["driver"] == "oracle"


def test_load_of_a_missing_history_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.jsonl") == []


def test_an_unchanged_run_reports_no_drift() -> None:
    record = flatten(TASK_REPORT, suite="agent-oracle")
    assert compare(record, record) == []


def test_every_moved_metric_is_named() -> None:
    baseline = flatten(TASK_REPORT, suite="agent-oracle")
    moved = json.loads(json.dumps(TASK_REPORT))
    moved["totals"]["device_tokens"] = 7999
    moved["totals"]["resolution_tiers"] = {"text-fuzzy": 32}

    drift = compare(flatten(moved, suite="agent-oracle"), baseline)
    assert any("device_tokens" in line for line in drift)
    assert any("resolution_tiers" in line for line in drift)
    assert len(drift) == 2


def test_time_is_never_guarded() -> None:
    """The one number the machine running the suite decides."""
    assert "seconds" not in CHECKED

    baseline = flatten(TASK_REPORT, suite="agent-oracle")
    slower = json.loads(json.dumps(TASK_REPORT))
    slower["totals"]["seconds"] = 99.0
    assert compare(flatten(slower, suite="agent-oracle"), baseline) == []


def test_rendering_an_empty_history_says_so() -> None:
    assert render([], 10) == "no runs recorded yet"


def test_rendering_shows_the_tiers_under_each_run() -> None:
    rows = [flatten(TASK_REPORT, suite="agent-oracle")]
    out = render(rows, 10)
    assert "text-exact" in out
    assert "7613" in out


# -- the command line -------------------------------------------------------


def _write(path: Path, report: dict) -> Path:
    path.write_text(json.dumps(report))
    return path


def test_check_passes_against_an_identical_run(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    report = _write(tmp_path / "agent.json", TASK_REPORT)
    assert main(["--history", str(history), "append", str(report), "--suite", "s"]) == 0
    assert main(["--history", str(history), "check", str(report), "--suite", "s"]) == 0


def test_check_fails_when_a_number_moves(tmp_path: Path, capsys) -> None:
    history = tmp_path / "history.jsonl"
    report = _write(tmp_path / "agent.json", TASK_REPORT)
    main(["--history", str(history), "append", str(report), "--suite", "s"])

    moved = json.loads(json.dumps(TASK_REPORT))
    moved["totals"]["actions"] = 41
    changed = _write(tmp_path / "moved.json", moved)

    assert main(["--history", str(history), "check", str(changed), "--suite", "s"]) == 1
    out = capsys.readouterr().out
    assert "actions: 36 -> 41" in out
    assert "append" in out, "a failure must say how to accept the new numbers"


def test_check_without_a_baseline_says_how_to_make_one(tmp_path: Path, capsys) -> None:
    history = tmp_path / "history.jsonl"
    report = _write(tmp_path / "agent.json", TASK_REPORT)

    assert main(["--history", str(history), "check", str(report), "--suite", "s"]) == 1
    assert "no baseline" in capsys.readouterr().out


def test_a_suite_is_only_compared_against_itself(tmp_path: Path) -> None:
    """A model-backed slice appended by hand must not become the free
    series' baseline."""
    history = tmp_path / "history.jsonl"
    report = _write(tmp_path / "agent.json", TASK_REPORT)
    main(["--history", str(history), "append", str(report), "--suite", "agent-oracle"])
    main(["--history", str(history), "append", str(report), "--suite", "agent-model"])

    moved = json.loads(json.dumps(TASK_REPORT))
    moved["totals"]["actions"] = 41
    main(
        [
            "--history",
            str(history),
            "append",
            str(_write(tmp_path / "m.json", moved)),
            "--suite",
            "agent-model",
        ]
    )

    assert main(["--history", str(history), "check", str(report), "--suite", "agent-oracle"]) == 0
