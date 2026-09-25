"""The committed eval history, and the guard that reads it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval_trend import (
    CHECKED,
    IDENTITY,
    SCHEMA_VERSION,
    append,
    compare,
    flatten,
    load,
    main,
    render,
)

FLOW_REPORT = {
    "schema_version": SCHEMA_VERSION,
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
    "schema_version": SCHEMA_VERSION,
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
        flatten({"schema_version": SCHEMA_VERSION, "totals": {}}, suite="x")


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


def test_the_turn_counts_are_guarded() -> None:
    """Both are counts on a fixed route, so ADR 0009 puts them in the exact guard.

    `turn_floor` is the real one: it is derived from the batch rule, so
    widening what terminates a sequence moves it and CI says so. `turns` is
    zero in both guarded series and checked for it, because the only way it
    could move is a model finding its way into the suite that runs for free.
    """
    assert {"turns", "turn_floor"} <= set(CHECKED)

    baseline = flatten(TASK_REPORT, suite="agent-oracle")
    batched = json.loads(json.dumps(TASK_REPORT))
    batched["totals"]["turn_floor"] = 39
    drift = compare(flatten(batched, suite="agent-oracle"), baseline)
    assert any("turn_floor" in line for line in drift)


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


def test_a_metric_added_later_does_not_read_as_a_change() -> None:
    """A record written before `finds` existed is not evidence that it moved.

    Without this, the run that introduces a guarded metric fails the check it
    just joined, and the message says a count changed when nothing did.
    """
    old = {"passed": 39, "finds": None}
    del old["finds"]
    assert compare({"passed": 39, "finds": 0}, old) == []
    assert compare({"passed": 39, "finds": 2}, old) == ["finds: 0 -> 2"]


def test_a_histogram_added_later_reads_as_empty_rather_than_missing() -> None:
    assert compare({"faults": {}}, {}) == []
    assert compare({"faults": {"model": 1}}, {}) == ["faults: {} -> {'model': 1}"]


# -- provenance: which prompt and which model produced the numbers ----------


#: A slice that had a model in the loop, so it carries both provenance fields.
MODEL_REPORT = {
    **TASK_REPORT,
    "driver": "s11-planted-instruction",
    "model": "openai:gpt-5.6-sol",
    "model_served": "gpt-5.6-sol-2026-08-01",
    "prompt_sha": "a1b2c3d4",
}


def test_provenance_survives_a_round_trip_in_key_order() -> None:
    """Recorded at all, and beside `model`, which is what it qualifies.

    `flatten` ends in a projection over `_KEY_ORDER`, so a key added to the
    record and not to that tuple is dropped without a word. This is the test
    that notices.
    """
    record = flatten(MODEL_REPORT, suite="agent-model")

    assert record["prompt_sha"] == "a1b2c3d4"
    assert record["model_served"] == "gpt-5.6-sol-2026-08-01"
    keys = list(record)
    assert keys.index("model") < keys.index("model_served") < keys.index("prompt_sha")


def test_a_suite_with_no_model_records_no_provenance() -> None:
    """An oracle run never read the operator prompt.

    Recording a hash of it there would attribute numbers to an input that had
    no part in producing them.
    """
    record = flatten(TASK_REPORT, suite="agent-oracle")

    assert record["prompt_sha"] is None
    assert record["model_served"] is None


def test_the_prompt_is_never_guarded_as_a_count() -> None:
    """It decides whether diffing means anything; it is not a thing to diff.

    Guarding it would also misfire once. `_baseline` substitutes `type(value)()`
    for a key the baseline lacks, which for a string is the empty one, so every
    suite would report `prompt_sha: '' -> ...` on the first run after this
    landed and exit non-zero for no reason.
    """
    assert IDENTITY not in CHECKED
    assert compare({**MODEL_REPORT, "prompt_sha": "deadbeef"}, MODEL_REPORT) == []


def test_a_changed_prompt_refuses_the_comparison(tmp_path: Path, capsys) -> None:
    """The counts are not compared, rather than compared and blamed on nothing.

    Both the prompt and a number move here. A run that reported the number and
    stayed quiet about the prompt would be the drift this file exists to stop.
    """
    history = tmp_path / "history.jsonl"
    report = _write(tmp_path / "agent.json", MODEL_REPORT)
    main(["--history", str(history), "append", str(report), "--suite", "s"])

    edited = json.loads(json.dumps(MODEL_REPORT))
    edited["prompt_sha"] = "99999999"
    edited["totals"]["actions"] = 41
    changed = _write(tmp_path / "changed.json", edited)

    assert main(["--history", str(history), "check", str(changed), "--suite", "s"]) == 1
    out = capsys.readouterr().out
    assert "a1b2c3d4 -> 99999999" in out
    assert "not comparable" in out
    assert "actions: 36 -> 41" not in out, "counts were diffed across two prompts"


def test_an_unchanged_prompt_compares_as_before(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    report = _write(tmp_path / "agent.json", MODEL_REPORT)
    main(["--history", str(history), "append", str(report), "--suite", "s"])

    assert main(["--history", str(history), "check", str(report), "--suite", "s"]) == 0


def test_a_baseline_from_before_this_landed_still_compares(tmp_path: Path, capsys) -> None:
    """Every row committed so far has no `prompt_sha`.

    Refusing those would restart all three series on the day the field arrived,
    so they compare, and the comparison says what it is assuming.
    """
    history = tmp_path / "history.jsonl"
    legacy = _write(tmp_path / "legacy.json", TASK_REPORT)
    main(["--history", str(history), "append", str(legacy), "--suite", "s"])

    report = _write(tmp_path / "agent.json", MODEL_REPORT)
    assert main(["--history", str(history), "check", str(report), "--suite", "s"]) == 0
    assert "predates prompt recording" in capsys.readouterr().out
