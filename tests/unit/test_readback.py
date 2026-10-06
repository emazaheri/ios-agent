"""Reading a field back after typing, so text that did not land is not reported as typed."""

from __future__ import annotations

import pytest
from fake_device import make_session
from fake_wda import FakeField
from trees import settings_screen

from ios_mcp.actions.readback import compare, field_text
from ios_mcp.errors import ElementNotInteractable

BULLET = chr(0x2022)

# -- the comparison ---------------------------------------------------------------


def test_text_that_arrived_as_sent_is_verified() -> None:
    assert compare("Airplane", "", "Airplane").status == "verified"


def test_typing_appends_to_what_the_field_held() -> None:
    assert compare("-Fi", "Wi", "Wi-Fi").status == "verified"


def test_dropped_leading_characters_are_a_mismatch() -> None:
    """agent-device #2080: eleven characters requested, seven landed."""
    readback = compare("hello world", "", "o world")
    assert readback.status == "mismatch"
    assert readback.landed is False
    assert readback.shown == "o world"


def test_text_already_in_the_field_is_not_taken_for_text_that_arrived() -> None:
    """A field that held the text and received nothing must not verify."""
    assert compare("Airplane", "Airplane", "Airplane").status == "mismatch"


def test_autocapitalisation_is_a_reformat_not_a_failure() -> None:
    readback = compare("hello", "", "Hello")
    assert readback.status == "reformatted"
    assert readback.landed is True


def test_smart_punctuation_is_a_reformat() -> None:
    curly = "don" + chr(0x2019) + "t"
    assert compare("don't", "", curly).status == "reformatted"


def test_an_input_mask_is_a_reformat() -> None:
    assert compare("5551234567", "", "(555) 123-4567").status == "reformatted"


def test_an_empty_field_reads_as_its_placeholder() -> None:
    """XCTest reports an empty field's value as its placeholder."""
    assert field_text("Search", "Search") == ""
    assert field_text(None, "Search") == ""
    assert field_text("Airplane", "Search") == "Airplane"


def test_a_secret_is_judged_by_length_and_never_echoed() -> None:
    landed = compare("hunter22", "", BULLET * 8, secret=True)
    assert landed.status == "verified"
    short = compare("hunter22", "", BULLET * 5, secret=True)
    assert short.status == "mismatch"
    assert short.shown is None
    assert "hunter22" not in str(short.to_dict())
    assert "hunter22" not in (short.note or "")


def test_a_secure_field_that_cleared_itself_on_focus_still_verifies() -> None:
    """iOS empties a secure field when typing resumes after refocus."""
    assert compare("pw", BULLET * 6, BULLET * 2, secret=True).status == "verified"


# -- through the session ----------------------------------------------------------


def _typing_session(field: FakeField):  # type: ignore[no-untyped-def]
    session, fake, _ = make_session(settings_screen())
    fake.focused_field = field
    return session, fake


async def test_text_that_landed_adds_nothing_to_the_result() -> None:
    """A clean type costs no extra tokens: the eval trend is byte-deterministic."""
    session, _ = _typing_session(FakeField())
    result = await session.type_text("Airplane")
    assert result.ok
    assert result.readback is not None and result.readback.status == "verified"
    assert "typed" not in result.to_dict()


async def test_dropped_characters_fail_the_action_and_say_what_landed() -> None:
    session, _ = _typing_session(FakeField(drop_leading=4))
    result = await session.type_text("hello world")
    assert result.ok is False
    payload = result.to_dict()
    assert payload["typed"] == {"status": "mismatch", "shown": "o world"}
    assert "o world" in payload["note"]


async def test_a_mismatch_is_audited_as_a_failure() -> None:
    session, _ = _typing_session(FakeField(drop_leading=4))
    await session.type_text("hello world")
    entry = session.audit.entries[-1]
    assert entry.ok is False
    assert entry.code == "text_mismatch"


async def test_a_reformat_succeeds_and_says_what_the_field_shows() -> None:
    session, _ = _typing_session(FakeField(transform=str.capitalize))
    result = await session.type_text("hello")
    assert result.ok
    assert result.to_dict()["typed"]["status"] == "reformatted"


async def test_a_secret_that_did_not_land_reports_counts_only() -> None:
    session, _ = _typing_session(FakeField(secure=True, placeholder="Password", drop_leading=3))
    result = await session.type_text("hunter22", _redact=True)
    assert result.ok is False
    payload = str(result.to_dict())
    assert "hunter22" not in payload
    assert result.to_dict()["typed"] == {
        "status": "mismatch",
        "expected_chars": 8,
        "shown_chars": 5,
    }
    assert "hunter22" not in str(session.audit.entries[-1])


async def test_clear_first_empties_the_field_rather_than_typing_ctrl_a() -> None:
    """The old select-all sent Ctrl and `a` as keys; on a simulator the field
    then read `Airplane<ctrl>aWi` where `Wi` was asked for."""
    field = FakeField(text="Airplane")
    session, fake = _typing_session(field)
    result = await session.type_text("Wi", clear_first=True)
    assert field.clears == 1
    assert field.text == "Wi"
    assert result.ok
    assert all("" not in "".join((b or {}).get("value", [])) for _, _, b in fake.calls)


async def test_clear_first_with_nothing_focused_refuses_rather_than_typing_blind() -> None:
    session, _ = _typing_session(None)  # type: ignore[arg-type]
    with pytest.raises(ElementNotInteractable):
        await session.type_text("Wi", clear_first=True)


async def test_with_nothing_focused_typing_behaves_as_before() -> None:
    session, _ = _typing_session(None)  # type: ignore[arg-type]
    result = await session.type_text("Airplane")
    assert result.ok
    assert result.readback is None


async def test_the_field_is_read_before_submitting() -> None:
    """Submitting can navigate away, taking the field with it."""
    session, fake = _typing_session(FakeField())
    await session.type_text("Airplane", submit=True)
    paths = [p for _, p, _ in fake.calls]
    last_read = max(i for i, p in enumerate(paths) if p.endswith("/attribute/value"))
    keys = [i for i, p in enumerate(paths) if p.endswith("/wda/keys")]
    assert last_read < keys[-1], "the read-back has to happen before the return key"


def test_an_unchanged_secure_field_is_a_mismatch() -> None:
    assert compare("pw", BULLET * 2, BULLET * 2, secret=True).status == "mismatch"
