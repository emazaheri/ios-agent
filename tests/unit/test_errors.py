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


def test_a_missing_app_is_named_rather_than_reported_as_a_lost_session() -> None:
    """WDA reports an uninstalled bundle id as `session_lost`.

    Four nested Apple error domains, the same code a genuinely dead session
    uses, and no indication that the real problem is a typo. Left alone it
    sends people debugging the runner. Taken from a real failure: the id was
    `com.hinge.mobile.ios` and the app is `co.hinge.mobile.ios`.
    """
    from ios_mcp.wda.client import _missing_app

    real = (
        'Error Domain=FBSOpenApplicationServiceErrorDomain Code=4 "The request to '
        'open "com.hinge.mobile.ios" failed." UserInfo={BSErrorCodeDescription='
        "InvalidRequest, NSUnderlyingError=0x10c676ac0 {Error Domain="
        'FBSOpenApplicationErrorDomain Code=4 "Application info provider '
        "(FBSApplicationLibrary"
    )

    assert _missing_app(real) == "com.hinge.mobile.ios"


def test_a_genuine_session_loss_is_left_alone() -> None:
    """The classifier must not swallow the error it is distinguishing itself from."""
    from ios_mcp.wda.client import _missing_app

    assert _missing_app("Session does not exist") is None
    assert _missing_app("The device could not be, unlocked") is None
    assert _missing_app("A settings request failed") is None


def test_the_missing_app_error_says_how_to_find_the_right_id() -> None:
    """A hint that only says "check the id" is not a hint.

    Bundle ids are frequently not what anyone would guess, which is the whole
    reason this error exists, so the hint names a way to look one up.
    """
    from ios_mcp.errors import AppNotFound

    error = AppNotFound("nope", hint="use ios_list_apps", details={"bundle_id": "x.y.z"})

    assert error.code is ErrorCode.APP_NOT_FOUND
    assert error.details["bundle_id"] == "x.y.z"
