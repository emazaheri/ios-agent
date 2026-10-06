"""Sprout's planted bugs behave as declared, and the report scorer reads them fairly.

Both halves are fixed before any model runs (ADR 0021). A fixture whose bug did
not fire would score a model on nothing, and a scorer that matched loosely
would credit a report that never named the bug.
"""

from __future__ import annotations

import pytest
from report import PLANTED, lines, score
from screens import DeviceModel, build_session
from tasks import BY_NAME
from test_agent_evals import eval_settings

from ios_mcp.errors import ElementNotInteractable

NAME = "Avery Montgomery-Castellanos the Third"


async def _sprout(screen: str = "sprout_welcome"):  # type: ignore[no-untyped-def]
    model = DeviceModel(screen=screen)
    session, fake, _ = build_session(model, eval_settings(BY_NAME["explore_signup"]))
    return model, session, fake


# -- the planted bugs fire ----------------------------------------------------------


async def test_the_email_field_loses_its_first_character_and_the_read_back_says_so() -> None:
    model, session, _ = await _sprout("sprout_account")
    result = await session.type_text("test@example.com", target="Email")
    assert model.sprout.email.text == "est@example.com"
    assert result.ok is False
    assert result.to_dict()["typed"]["status"] == "mismatch"


async def test_the_terms_switch_is_below_the_fold_until_scrolled() -> None:
    _, session, _ = await _sprout("sprout_account")
    assert "I agree" not in (await session.observe()).render()
    result = await session.scroll("down", until="I agree to the Terms")
    assert "found" in (result.note or "")


async def test_continue_is_disabled_and_says_so_rather_than_vanishing() -> None:
    """The digest showed it as disabled while a tap said nothing matched."""
    _, session, _ = await _sprout("sprout_account")
    with pytest.raises(ElementNotInteractable, match="disabled"):
        await session.tap(target="Continue")


async def test_saving_claims_success_and_keeps_nothing() -> None:
    model, session, _ = await _sprout("sprout_profile")
    await session.type_text(NAME, target="Full name")
    saved = await session.tap(target="Save")
    assert "Profile saved" in repr(saved.to_dict())
    # Review reads what was kept, and nothing was.
    assert "Name: Not set" in repr(model.sprout.tree("sprout_review"))


async def test_the_name_field_takes_all_38_characters() -> None:
    model, session, _ = await _sprout("sprout_profile")
    await session.type_text(NAME, target="Full name")
    assert len(model.sprout.name.text) == 38 > 30


async def test_the_count_is_one_more_than_the_chips() -> None:
    _, session, _ = await _sprout("sprout_interests")
    await session.set_value("on", target="Running")
    await session.set_value("on", target="Reading")
    screen = (await session.observe()).render()
    assert '"3 selected"' in screen
    assert sum(" =1 " in line for line in screen.splitlines()) == 2


async def test_the_upload_never_finishes() -> None:
    _, session, _ = await _sprout("sprout_photo")
    await session.tap(target="Choose Photo")
    for _ in range(3):
        assert "Uploading photo" in (await session.observe()).render()


async def test_done_takes_the_tap_and_changes_nothing() -> None:
    model, session, _ = await _sprout("sprout_review")
    result = await session.tap(target="Done")
    assert result.screen_changed is False
    assert model.sprout.done_taps == 1
    assert model.screen == "sprout_review"


async def test_the_visual_bug_is_ordinary_text_in_the_tree() -> None:
    """White on white: a reader of the tree has nothing to notice."""
    _, session, _ = await _sprout()
    assert "By continuing you accept our Terms." in (await session.observe()).render()


# -- the scorer -----------------------------------------------------------------------


def test_each_bug_needs_its_subject_and_its_fault_on_one_line() -> None:
    """Done mentioned in passing, beside a different complaint, is not the Done bug."""
    caught = score("I tapped Done. The photo upload never finishes.", spec_given=False).caught
    assert caught["upload_stuck"] is True
    assert caught["dead_done"] is False


@pytest.mark.parametrize(
    ("line", "bug"),
    [
        ("Tapping Done on the Review screen has no effect", "dead_done"),
        ("The Done button is unresponsive", "dead_done"),
        (
            "After saving, the profile said saved but Review shows the name as Not set",
            "save_claimed",
        ),
        ("Uploading the photo hangs forever", "upload_stuck"),
        ("The interest count is wrong: it says 3 selected with 2 chosen", "count_wrong"),
        ("The email field dropped the first letter of the address", "email_first_char"),
        ("Name field accepts more than 30 characters", "name_over_cap"),
    ],
)
def test_plain_phrasings_of_each_bug_score(line: str, bug: str) -> None:
    assert score(line, spec_given=True).caught[bug] is True


def test_the_spec_bug_is_expected_only_when_the_spec_was_given() -> None:
    assert "name_over_cap" in score("", spec_given=True).expected
    assert "name_over_cap" not in score("", spec_given=False).expected


def test_the_visual_bug_is_never_expected() -> None:
    assert "caption_unreadable" not in score("", spec_given=True).expected


def test_a_complaint_about_something_unplanted_is_a_candidate_false_report() -> None:
    result = score("The Back button on Interests is broken.\nDone does nothing.", spec_given=False)
    assert result.false_report_candidates == ("The Back button on Interests is broken.",)


def test_a_style_remark_is_not_a_false_report() -> None:
    assert (
        score("The welcome copy could be friendlier.", spec_given=False).false_report_candidates
        == ()
    )


def test_a_numbered_paragraph_and_bullets_split_alike() -> None:
    assert lines("1. Done does nothing. 2. Upload hangs.") == [
        "1.",
        "Done does nothing.",
        "2.",
        "Upload hangs.",
    ]
    assert lines("- Done does nothing\n- Upload hangs") == ["Done does nothing", "Upload hangs"]


def test_seven_bugs_in_three_classes() -> None:
    classes = [bug.bug_class for bug in PLANTED]
    assert classes.count("self_evident") == 5
    assert classes.count("needs_spec") == 1
    assert classes.count("visual") == 1


async def test_sprout_is_installed_for_the_runs_that_use_it() -> None:
    """Left out, an agent told to use the Sprout app reported it missing and stopped."""
    _, session, _ = await _sprout()
    result = await session.open_app("Sprout")
    assert result.ok
