"""Error taxonomy contract."""

from __future__ import annotations

import pytest

from ios_mcp.errors import (
    ElementNotFound,
    ErrorCode,
    IosAutomationError,
    RunnerCrashed,
)


def test_subclasses_carry_their_code() -> None:
    err = ElementNotFound("no such button")
    assert err.code is ErrorCode.ELEMENT_NOT_FOUND
    assert err.to_dict()["error"] == "element_not_found"


def test_to_dict_includes_hint_and_details_only_when_present() -> None:
    bare = RunnerCrashed("runner died")
    assert set(bare.to_dict()) == {"error", "message", "recoverable"}

    rich = RunnerCrashed("runner died", hint="restart it", details={"pid": 42}, recoverable=True)
    payload = rich.to_dict()
    assert payload["hint"] == "restart it"
    assert payload["details"] == {"pid": 42}
    assert payload["recoverable"] is True


def test_str_is_agent_readable() -> None:
    err = ElementNotFound("no button labelled Send", hint="call ios_observe first")
    assert str(err) == "[element_not_found] no button labelled Send Hint: call ios_observe first"


def test_every_code_has_a_unique_value() -> None:
    values = [c.value for c in ErrorCode]
    assert len(values) == len(set(values))


def test_base_class_defaults_to_internal() -> None:
    assert IosAutomationError("boom").code is ErrorCode.INTERNAL


def test_errors_are_raisable_and_catchable_as_base() -> None:
    with pytest.raises(IosAutomationError):
        raise ElementNotFound("x")
