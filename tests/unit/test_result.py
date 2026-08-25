"""Digest diffing: what an action reports back."""

from __future__ import annotations

from trees import form_screen, node, settings_screen

from ios_mcp.actions.result import DigestDelta, diff_digests
from ios_mcp.config import Settings
from ios_mcp.perception.digest import build_digest
from ios_mcp.wda.models import SnapshotNode


def digest_of(tree: dict):
    return build_digest(SnapshotNode.from_wda(tree), Settings().digest)


def test_an_unchanged_screen_produces_an_empty_delta() -> None:
    a = digest_of(settings_screen())
    b = digest_of(settings_screen())
    assert diff_digests(a, b).empty


def test_a_toggled_switch_shows_as_changed_not_replaced() -> None:
    delta = diff_digests(
        digest_of(settings_screen(airplane_on=False)),
        digest_of(settings_screen(airplane_on=True)),
    )
    assert not delta.added and not delta.removed
    assert len(delta.changed) == 1
    before, after = delta.changed[0]
    assert (before.value, after.value) == ("0", "1")


def test_added_and_removed_rows_are_reported() -> None:
    base = settings_screen()
    extended = settings_screen()
    extended["children"][1]["children"].append(
        node("Cell", label="Cellular", name="cell_cell", y=232)
    )

    delta = diff_digests(digest_of(base), digest_of(extended))
    assert [n.label for n in delta.added] == ["Cellular"]

    reverse = diff_digests(digest_of(extended), digest_of(base))
    assert [n.label for n in reverse.removed] == ["Cellular"]


def test_matching_survives_ref_reassignment() -> None:
    """Refs shift when a row is inserted; identity must not."""
    base = settings_screen()
    shifted = settings_screen()
    shifted["children"][1]["children"].insert(0, node("Cell", label="New", name="new", y=98))

    delta = diff_digests(digest_of(base), digest_of(shifted))
    assert [n.label for n in delta.added] == ["New"]
    assert not delta.removed, "the existing rows are the same elements, just renumbered"


def test_the_rendered_delta_is_readable() -> None:
    delta = diff_digests(
        digest_of(settings_screen(airplane_on=False)),
        digest_of(settings_screen(airplane_on=True)),
    )
    text = delta.render()
    assert text.startswith("~")
    assert "Airplane Mode" in text


def test_an_empty_delta_says_so_in_words() -> None:
    assert DigestDelta().render() == "no visible change"


def test_a_different_screen_is_mostly_add_and_remove() -> None:
    delta = diff_digests(digest_of(settings_screen()), digest_of(form_screen()))
    assert delta.added and delta.removed
    assert not delta.changed
