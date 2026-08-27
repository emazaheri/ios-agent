"""Element resolution: every tier of the fallback chain."""

from __future__ import annotations

import pytest
from trees import (
    drawn_controls_screen,
    form_screen,
    list_screen,
    node,
    settings_screen,
    third_party_card_screen,
)

from ios_mcp.config import Settings
from ios_mcp.errors import ElementAmbiguous, ElementNotFound, InvalidArgument, NoSnapshot
from ios_mcp.perception.digest import build_digest
from ios_mcp.perception.refs import RefTable
from ios_mcp.perception.resolve import resolve
from ios_mcp.wda.models import SnapshotNode


def make(tree: dict) -> tuple:
    digest = build_digest(SnapshotNode.from_wda(tree), Settings().digest)
    refs = RefTable()
    refs.update(digest)
    return digest, refs


def test_a_reassigned_ref_is_not_silently_acted_on() -> None:
    """Inserting a row shifts every ref below it. Position alone is not identity."""
    before, refs = make(settings_screen())
    switch_ref = next(n.ref for n in before.nodes if n.role == "switch")

    shifted = settings_screen()
    shifted["children"][1]["children"].insert(
        0, node("Cell", label="New Row", name="new_cell", y=98)
    )
    after = build_digest(SnapshotNode.from_wda(shifted), Settings().digest)

    # The element now sitting at that ref is a different one.
    assert after.by_ref(switch_ref).identifier != "airplane_switch"
    # Resolution must follow identity, not position.
    assert resolve(after, refs, ref=switch_ref).identifier == "airplane_switch"


def test_tier1_exact_ref() -> None:
    digest, refs = make(settings_screen())
    switch = next(n for n in digest.nodes if n.role == "switch")
    target = resolve(digest, refs, ref=switch.ref)
    assert target.resolved_via == "exact"
    assert target.identifier == "airplane_switch"


def test_tier2_stale_ref_recovers_by_accessibility_id() -> None:
    """The commonest real failure: the screen scrolled between observe and act."""
    before, refs = make(settings_screen(airplane_on=False))
    switch_ref = next(n.ref for n in before.nodes if n.role == "switch")

    # A row appeared, so every ref below it now denotes a different element.
    # The table still holds what the agent was shown, which is the whole point.
    shifted = settings_screen(airplane_on=False)
    shifted["children"][1]["children"].insert(
        0, node("Cell", label="New Row", name="new_cell", y=98)
    )
    after = build_digest(SnapshotNode.from_wda(shifted), Settings().digest)

    target = resolve(after, refs, ref=switch_ref)
    assert target.resolved_via == "id"
    assert target.identifier == "airplane_switch"


def test_tier3_recovers_by_label_and_role_when_the_id_is_gone() -> None:
    before, refs = make(settings_screen())
    switch_ref = next(n.ref for n in before.nodes if n.role == "switch")

    stripped = settings_screen()
    switch = stripped["children"][1]["children"][0]["children"][1]
    switch["name"] = switch["label"]  # identifier folded away
    stripped["children"][1]["children"].insert(
        0, node("Cell", label="New Row", name="new_cell", y=98)
    )
    after = build_digest(SnapshotNode.from_wda(stripped), Settings().digest)

    target = resolve(after, refs, ref=switch_ref)
    assert target.role == "switch"
    assert target.resolved_via in ("exact", "label+role")


def test_an_unrecoverable_ref_explains_what_it_pointed_at() -> None:
    before, refs = make(form_screen())
    send_ref = next(n.ref for n in before.nodes if n.label == "Send" and n.enabled)

    after = build_digest(SnapshotNode.from_wda(settings_screen()), Settings().digest)

    with pytest.raises(ElementNotFound) as exc_info:
        resolve(after, refs, ref=send_ref)
    assert "no longer on screen" in exc_info.value.message


def test_an_unknown_ref_lists_the_valid_ones() -> None:
    digest, refs = make(settings_screen())
    with pytest.raises(ElementNotFound) as exc_info:
        resolve(digest, refs, ref="e999")
    assert "known_refs" in exc_info.value.details
    assert "e1" in exc_info.value.details["known_refs"]


def test_tier5_exact_label_text() -> None:
    digest, refs = make(form_screen())
    target = resolve(digest, refs, target="Cancel")
    assert target.resolved_via == "text-exact"
    assert target.label == "Cancel"


def test_text_matching_is_case_and_whitespace_insensitive() -> None:
    digest, refs = make(form_screen())
    assert resolve(digest, refs, target="  cancel ").label == "Cancel"


def test_tier6_partial_match_prefers_the_shortest_label() -> None:
    tree = node(
        "Application",
        h=852,
        children=[
            node("Button", label="Wi-Fi", y=100),
            node("Button", label="Wi-Fi Networks Nearby", y=150),
        ],
    )
    digest, refs = make(tree)
    target = resolve(digest, refs, target="Wi-Fi Net")
    assert target.label == "Wi-Fi Networks Nearby"

    target2 = resolve(digest, refs, target="Fi")
    assert target2.label == "Wi-Fi"
    assert target2.alternatives, "the other candidate must be surfaced"


def test_fuzzy_match_tolerates_a_typo() -> None:
    digest, refs = make(form_screen())
    target = resolve(digest, refs, target="Cancle")
    assert target.resolved_via == "text-fuzzy"
    assert target.label == "Cancel"


def test_fuzzy_matching_refuses_a_wild_guess() -> None:
    digest, refs = make(form_screen())
    with pytest.raises(ElementNotFound) as exc_info:
        resolve(digest, refs, target="launch the rocket")
    assert "closest" in exc_info.value.details
    assert "annotate_refs" in (exc_info.value.hint or "")


def test_duplicate_labels_raise_rather_than_guess() -> None:
    """Guessing between two identical Delete buttons is how agents do damage."""
    tree = node(
        "Application",
        h=852,
        children=[
            node("Button", label="Delete", name="del_a", y=100),
            node("Button", label="Delete", name="del_b", y=200),
        ],
    )
    digest, refs = make(tree)
    with pytest.raises(ElementAmbiguous) as exc_info:
        resolve(digest, refs, target="Delete")
    assert len(exc_info.value.details["candidates"]) == 2
    assert "ios_observe" in (exc_info.value.hint or "")


def test_role_narrows_an_otherwise_ambiguous_match() -> None:
    digest, refs = make(settings_screen())
    target = resolve(digest, refs, target="Airplane Mode", role="switch")
    assert target.role == "switch"


def test_actionable_elements_are_preferred_over_labels() -> None:
    digest, refs = make(list_screen(rows=5))
    target = resolve(digest, refs, target="Contact 002")
    assert target.role == "cell"
    assert target.enabled


def test_resolution_by_accessibility_identifier() -> None:
    digest, refs = make(settings_screen())
    target = resolve(digest, refs, target="airplane_switch")
    assert target.resolved_via == "id-exact"


def test_neither_ref_nor_target_is_a_usage_error() -> None:
    digest, refs = make(settings_screen())
    with pytest.raises(InvalidArgument) as exc_info:
        resolve(digest, refs)
    assert "ios_observe" in (exc_info.value.hint or "")


def test_an_empty_screen_says_so_clearly() -> None:
    digest, refs = make(node("Application", h=852))
    with pytest.raises(NoSnapshot) as exc_info:
        resolve(digest, refs, target="anything")
    assert "ios_screenshot" in (exc_info.value.hint or "")


def test_target_exposes_a_tap_point_at_the_element_centre() -> None:
    digest, refs = make(settings_screen())
    target = resolve(digest, refs, target="Wi-Fi")
    x, y = target.point
    assert target.rect.contains(x, y)


def test_text_shown_only_as_a_value_can_be_targeted() -> None:
    """The digest must not display text the agent cannot then act on.

    `_value_of` was fixed so a field split across label and value surrenders
    both, which is what made a real profile card readable. The text tiers were
    not: they match `label` only, so the answer the agent can plainly see is
    the one string on screen it cannot name. Showing text and refusing to
    resolve it is worse than not showing it.
    """
    digest, refs = make(third_party_card_screen())

    target = resolve(digest, refs, target="Let's get together", actionable_only=False)

    assert target.label == "Date prompt:", "matched something other than the field it belongs to"
    assert target.resolved_via == "value-exact", "a value ranks below a label, not beside it"


def test_an_identifier_is_searchable_the_way_it_is_rendered() -> None:
    """`ios_observe(query=...)` filters on `_text_of`, which skips identifiers.

    The rendered line shows `id=like_post_1`, so an agent narrowing a crowded
    screen by that id gets an empty result for a string the digest itself
    printed.
    """
    d = build_digest(
        SnapshotNode.from_wda(drawn_controls_screen()),
        Settings().digest,
        query="like_post_1",
    )

    assert [n.identifier for n in d.nodes] == ["like_post_1"]
