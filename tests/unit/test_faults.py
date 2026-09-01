"""Attributing a failure to the device, perception, the model, or policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ios_mcp.errors import ErrorCode
from ios_mcp.policy.faults import BY_CODE, Fault, attribute, classify


@dataclass
class Entry:
    """The shape ``classify`` reads, standing in for an AuditEntry."""

    ok: bool = False
    action: str = "tap"
    code: str | None = None
    details: dict[str, Any] | None = None


def test_every_error_code_is_classified() -> None:
    """A new code must not be able to slip in unattributed.

    This is the whole guard. Without it the histogram silently starts
    under-counting the moment the taxonomy grows.
    """
    assert set(BY_CODE) == set(ErrorCode)


def test_every_fault_class_is_reachable() -> None:
    """A class no code produces is dead weight in the report."""
    produced = set(BY_CODE.values())
    assert produced == set(Fault)


def test_a_successful_entry_has_nobody_to_blame() -> None:
    assert classify(Entry(ok=True, code=None)) is None


def test_an_entry_without_a_code_is_unknown() -> None:
    """Trails written before the code was recorded still have to parse."""
    assert classify(Entry(code=None)) is Fault.UNKNOWN


def test_an_unrecognised_code_is_unknown() -> None:
    assert classify(Entry(code="something_invented")) is Fault.UNKNOWN


def test_a_dead_runner_is_the_device() -> None:
    assert classify(Entry(code=ErrorCode.RUNNER_CRASHED.value)) is Fault.DEVICE


def test_a_declined_action_is_policy_not_a_malfunction() -> None:
    assert classify(Entry(code=ErrorCode.ACTION_REQUIRES_APPROVAL.value)) is Fault.POLICY


# -- the two splits ---------------------------------------------------------


def test_a_miss_on_a_readable_screen_is_the_model() -> None:
    """``closest`` lists every node carrying prose, so its presence means the
    screen was readable and the name simply did not match anything on it."""
    entry = Entry(
        code=ErrorCode.ELEMENT_NOT_FOUND.value,
        details={"closest": ["e3: button 'Wi-Fi'"]},
    )
    assert classify(entry) is Fault.MODEL


def test_a_miss_with_nothing_to_compare_against_is_perception() -> None:
    entry = Entry(code=ErrorCode.ELEMENT_NOT_FOUND.value, details={"closest": []})
    assert classify(entry) is Fault.PERCEPTION

    assert classify(Entry(code=ErrorCode.ELEMENT_NOT_FOUND.value)) is Fault.PERCEPTION


def test_an_invented_ref_is_the_model() -> None:
    entry = Entry(code=ErrorCode.ELEMENT_NOT_FOUND.value, details={"known_refs": ["e1", "e2"]})
    assert classify(entry) is Fault.MODEL


def test_waiting_for_something_that_never_appears_is_the_model() -> None:
    entry = Entry(action="wait_for", code=ErrorCode.TIMEOUT.value)
    assert classify(entry) is Fault.MODEL


def test_any_other_timeout_is_the_device() -> None:
    entry = Entry(action="tap", code=ErrorCode.TIMEOUT.value)
    assert classify(entry) is Fault.DEVICE


# -- aggregation ------------------------------------------------------------


def test_the_histogram_sums_to_the_failures() -> None:
    entries = [
        Entry(ok=True, code=None),
        Entry(code=ErrorCode.RUNNER_CRASHED.value),
        Entry(code=ErrorCode.NO_SNAPSHOT.value),
        Entry(code=ErrorCode.INVALID_ARGUMENT.value),
        Entry(code=ErrorCode.APP_NOT_ALLOWED.value),
    ]
    counts = attribute(entries)
    assert counts == {"device": 1, "perception": 1, "model": 1, "policy": 1}
    assert sum(counts.values()) == sum(1 for e in entries if not e.ok)
