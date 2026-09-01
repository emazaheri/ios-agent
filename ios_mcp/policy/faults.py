"""Whose fault was it: the device, perception, the model, or policy.

An eval that reports a success rate says a run failed. It does not say whether
the digest failed to show something, the model named something that was never
on screen, or the phone simply would not comply, and those three call for
completely different fixes. The audit trail already carries the error code of
every failure, so the attribution is a pure function over the trail rather
than anything the session has to be taught to collect.

This deliberately reads entries structurally rather than importing
``AuditEntry``, so ``audit`` can import this without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from ios_mcp.errors import ErrorCode


class Fault(StrEnum):
    DEVICE = "device"
    PERCEPTION = "perception"
    MODEL = "model"
    POLICY = "policy"
    UNKNOWN = "unknown"


class Entry(Protocol):
    """The part of an audit entry attribution reads."""

    ok: bool
    action: str
    code: str | None
    details: dict[str, Any] | None


#: Every code that attributes on its own. The two that need the entry's own
#: context are handled in ``classify``.
BY_CODE: Mapping[ErrorCode, Fault] = {
    # The host or the phone is not in a state where anything could have worked.
    ErrorCode.TOOLCHAIN_MISSING: Fault.DEVICE,
    ErrorCode.DEVICE_UNAVAILABLE: Fault.DEVICE,
    ErrorCode.DEVICE_NOT_READY: Fault.DEVICE,
    ErrorCode.DEVICE_LOCKED: Fault.DEVICE,
    ErrorCode.TUNNEL_DOWN: Fault.DEVICE,
    ErrorCode.SIGNING_INVALID: Fault.DEVICE,
    ErrorCode.RUNNER_CRASHED: Fault.DEVICE,
    ErrorCode.SESSION_LOST: Fault.DEVICE,
    ErrorCode.WDA_ERROR: Fault.DEVICE,
    # The environment interposed something nobody asked for.
    ErrorCode.UNEXPECTED_ALERT: Fault.DEVICE,
    # The screen was there and the digest could not offer it.
    ErrorCode.ELEMENT_STALE: Fault.PERCEPTION,
    ErrorCode.NO_SNAPSHOT: Fault.PERCEPTION,
    # Named the wrong thing. ``app_not_found`` is a bundle id nobody has, which
    # errors.py already describes as a typo or a wrong guess; an ambiguous
    # label is answered by passing a ref, which is what the hint says; and a
    # disabled element was found correctly, so the fix is a different target.
    ErrorCode.APP_NOT_FOUND: Fault.MODEL,
    ErrorCode.ELEMENT_AMBIGUOUS: Fault.MODEL,
    ErrorCode.ELEMENT_NOT_INTERACTABLE: Fault.MODEL,
    ErrorCode.INVALID_ARGUMENT: Fault.MODEL,
    ErrorCode.NOT_SUPPORTED: Fault.MODEL,
    # The gate did its job. None of these mean anything went wrong.
    ErrorCode.ACTION_REQUIRES_APPROVAL: Fault.POLICY,
    ErrorCode.ACTION_REJECTED_BY_POLICY: Fault.POLICY,
    ErrorCode.APP_NOT_ALLOWED: Fault.POLICY,
    ErrorCode.SESSION_HALTED: Fault.POLICY,
    ErrorCode.SECRET_NOT_FOUND: Fault.POLICY,
    # A bug here, by definition.
    ErrorCode.INTERNAL: Fault.UNKNOWN,
    # Both of these depend on the entry, see classify.
    ErrorCode.ELEMENT_NOT_FOUND: Fault.PERCEPTION,
    ErrorCode.TIMEOUT: Fault.DEVICE,
}


def classify(entry: Entry) -> Fault | None:
    """Attribute one failed entry. ``None`` for an entry that succeeded."""
    if entry.ok:
        return None
    if entry.code is None:
        # Pre-dates the code being recorded, or was never an IosAutomationError.
        return Fault.UNKNOWN
    try:
        code = ErrorCode(entry.code)
    except ValueError:
        return Fault.UNKNOWN
    if code is ErrorCode.ELEMENT_NOT_FOUND:
        return _not_found(entry)
    if code is ErrorCode.TIMEOUT:
        # Waiting for something that never appears is a wrong expectation.
        # Any other timeout is the device failing to get there.
        return Fault.MODEL if entry.action == "wait_for" else Fault.DEVICE
    return BY_CODE[code]


def _not_found(entry: Entry) -> Fault:
    """Split a resolution miss on what the resolver could see.

    The direction is the opposite of the intuitive reading, and it follows from
    how ``closest`` is built: it lists every node on screen carrying prose. So
    a non-empty ``closest`` means the screen was perfectly readable and the
    name simply did not match anything on it, which is the model's fault. An
    absent ``closest`` means the pool was empty or nothing on screen had any
    text at all, which is the digest failing to offer what the screen showed.

    An invented ref, which comes back with ``known_refs``, is the model too.
    """
    details = entry.details or {}
    if details.get("known_refs") is not None:
        return Fault.MODEL
    return Fault.MODEL if details.get("closest") else Fault.PERCEPTION


def attribute(entries: Iterable[Entry]) -> dict[str, int]:
    """Histogram of faults over a trail. Sums to the number of failures."""
    counts: dict[str, int] = {}
    for entry in entries:
        fault = classify(entry)
        if fault is not None:
            counts[fault.value] = counts.get(fault.value, 0) + 1
    return counts
