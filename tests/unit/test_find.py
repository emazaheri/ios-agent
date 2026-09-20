"""Searching the raw tree: the query that can see past the digest's own rules.

The feature exists for one question the rest of the stack cannot answer: when
the agent cannot name something a person can see on the phone, is the text
missing from the tree, or did perception drop it? Every test that matters here
is about the `shown` flag, because that flag is the answer.
"""

from __future__ import annotations

from fake_device import make_session
from trees import (
    drawn_controls_screen,
    list_screen,
    node,
    opaque_canvas_screen,
    settings_screen,
    third_party_card_screen,
    webview_screen,
)

from ios_mcp.config import DigestSettings, Settings
from ios_mcp.perception.digest import build_digest
from ios_mcp.perception.find import find_in_tree
from ios_mcp.wda.models import SnapshotNode


def find_in(tree: dict, text: str, settings: DigestSettings | None = None, **kw):
    """Build both artefacts from one tree, the way `IosSession.find` does."""
    root = SnapshotNode.from_wda(tree)
    digest = build_digest(root, settings or Settings().digest)
    return find_in_tree(root, text, digest, **kw)


# -- what it finds ---------------------------------------------------------


def test_a_value_split_from_its_label_is_findable() -> None:
    """The `_value_of` bug, now self-diagnosing.

    A third-party StaticText names the field on its label and carries the
    answer on its value. Searching for the answer has to find it, and has to
    say it matched the value rather than the label, because which field
    matched decides which resolution tier the caller can reach it by.
    """
    result = find_in(third_party_card_screen(), "Let's get together")
    assert result.total == 1
    assert result.matches[0].matched_on == "value"
    assert result.matches[0].label == "Date prompt:"


def test_an_identifier_with_no_label_anywhere_is_findable() -> None:
    """A drawn control arrives as an Image with an id and nothing else."""
    result = find_in(drawn_controls_screen(), "like")
    assert result.total >= 1
    match = next(m for m in result.matches if m.matched_on == "id")
    assert match.label is None
    assert match.identifier is not None and "like" in match.identifier.lower()


def test_matching_is_case_and_whitespace_insensitive() -> None:
    assert find_in(settings_screen(), "AIRPLANE  MODE").total >= 1
    assert find_in(settings_screen(), "airplane mode").total >= 1


def test_an_empty_query_matches_nothing_rather_than_everything() -> None:
    result = find_in(settings_screen(), "   ")
    assert result.total == 0
    assert result.matches == ()


# -- the flag the feature exists for ---------------------------------------


def test_a_match_the_digest_dropped_is_reported_as_hidden() -> None:
    """The one behaviour `ios_observe(query=...)` cannot have.

    A budget tight enough to truncate a long list leaves rows in the tree that
    are not in the digest. Those rows are exactly what the agent cannot name,
    and saying so is the whole capability.
    """
    settings = Settings().digest.model_copy(update={"max_nodes": 8})
    result = find_in(list_screen(rows=200), "Contact 1", settings)
    assert result.hidden > 0
    assert any(not m.shown for m in result.matches)
    assert "not in the digest" in result.render()
    assert "hidden match is on screen" in result.render()


def test_a_match_the_digest_kept_is_reported_as_shown() -> None:
    result = find_in(settings_screen(), "Airplane Mode")
    assert result.hidden == 0
    assert all(m.shown for m in result.matches)
    assert "not in the digest" not in result.render()


def test_an_echo_the_digest_dropped_is_not_a_false_alarm() -> None:
    """The flag is only worth having if it is quiet on ordinary screens.

    A Settings row emits three nodes for one control: the cell, the switch,
    and a `StaticText` echoing the label. The digest keeps the switch and
    drops the other two, and `_dedupe_colocated` gives the kept element the
    switch's tight trailing-edge rect, which does not overlap the row-wide
    label at all. Any position-based test would call those words hidden while
    they sit in the digest one line above, and a flag that cries wolf on every
    Apple screen cannot be trusted on the third-party one it exists for.
    """
    result = find_in(settings_screen(), "Airplane Mode")
    assert result.total == 3, "the cell, the switch, and the echoed StaticText"
    assert all(m.shown for m in result.matches)
    assert result.hidden == 0


# -- honesty about screens with nothing to read ----------------------------


def test_an_unreadable_screen_says_so_rather_than_looking_empty() -> None:
    """ADR 0007: a screen with no accessible tree carries a note."""
    for tree in (webview_screen(), opaque_canvas_screen()):
        result = find_in(tree, "anything at all")
        assert result.total == 0
        assert result.notes, "the digest's note must be carried into the find"
        assert "note:" in result.render()


# -- counting and capping --------------------------------------------------


def test_total_counts_the_whole_tree_and_limit_caps_what_comes_back() -> None:
    result = find_in(list_screen(rows=200), "Contact", limit=5)
    assert result.total > 5
    assert len(result.matches) == 5
    assert "more, raise limit" in result.render()


def test_one_control_reported_twice_by_ios_is_reported_once() -> None:
    """iOS emits a row-wide node and a tighter one for the same control.

    They differ in rect, so they are two matches; a node repeated verbatim is
    one. Without this a search on a long list returns each row several times
    and buries the answer.
    """
    twice = node(
        "Application",
        label="App",
        children=[
            node("StaticText", label="Repeated", x=0, y=10, w=100, h=20),
            node("StaticText", label="Repeated", x=0, y=10, w=100, h=20),
        ],
    )
    assert find_in(twice, "Repeated").total == 1


def test_matches_come_back_in_reading_order() -> None:
    result = find_in(list_screen(rows=20), "Contact")
    tops = [m.rect.y for m in result.matches]
    assert tops == sorted(tops)


# -- agreement with the filter that already exists -------------------------


def test_find_and_observe_query_agree_about_what_matches() -> None:
    """`build_digest(query=...)` narrows by the same four fields.

    They are separate implementations because find also reports *which* field
    matched, so this is the guard that stops them drifting apart: anything the
    digest's own filter keeps must be something find also calls a match.
    """
    tree = settings_screen()
    root = SnapshotNode.from_wda(tree)
    for needle in ("airplane", "wi-fi", "general"):
        filtered = build_digest(root, Settings().digest, query=needle)
        found = find_in(tree, needle)
        labels = {m.label for m in found.matches} | {m.identifier for m in found.matches}
        for kept in filtered.nodes:
            assert kept.label in labels or kept.identifier in labels, (
                f"{needle!r} kept {kept.ref} in the digest but find did not match it"
            )


# -- what a find costs, and what it must not touch -------------------------


async def test_a_find_costs_exactly_one_source_fetch() -> None:
    """The raw tree and the digest come from one round trip, not two.

    Two fetches would be slower and, worse, wrong: `shown` would be decided
    against a screen the tree had already moved on from.
    """
    session, fake, _ = make_session(settings_screen())

    def fetches() -> int:
        return sum(1 for path in fake.paths_called() if path.endswith("/source"))

    before = fetches()
    await session.find("Airplane")
    assert fetches() - before == 1


async def test_a_find_is_not_an_observation() -> None:
    """The invariant the whole no-refs decision exists to protect.

    `RefTable.update()` is called only when a digest is returned to the agent.
    A find that bumped the generation would destroy the memory that lets a ref
    pointing at a different element than the agent was shown be detected, and
    acting on the wrong control is the worst failure this system has.
    """
    session, _, _ = make_session(settings_screen())
    await session.observe()
    generation = session.refs.generation
    fingerprint = session.refs.fingerprint

    result = await session.find("Airplane")

    assert result.total > 0, "the find has to have done real work to prove anything"
    assert session.refs.generation == generation
    assert session.refs.fingerprint == fingerprint


async def test_shown_is_judged_against_the_budget_the_caller_observed_with() -> None:
    """Found on a real Settings root, not reasoned about.

    Observing at a tight budget drops elements the default budget keeps. A find
    that rebuilt the digest at the default would call every one of them
    `shown`, telling a caller it can name something it was never given. That is
    the one direction this result must never be wrong in, so the budget has to
    travel with the question.
    """
    session, _, _ = make_session(list_screen(rows=20))

    tight = await session.find("Contact 0", budget=40)
    loose = await session.find("Contact 0", budget=4000)

    assert tight.total == loose.total, "the tree is the same either way"
    assert tight.hidden > loose.hidden, (
        "a tighter budget keeps less, so more of the same matches are out of reach"
    )


def test_a_coincidental_substring_elsewhere_on_screen_is_not_reachability() -> None:
    """Found on a real Settings root at a tight budget, not reasoned about.

    The row labelled "Siri" was dropped while "Optimizing Search and Siri",
    468 points further up, survived. Containment alone called the dropped row
    reachable; an agent taking that at face value and targeting "Siri" resolves
    by substring onto the other row and taps the wrong thing. Two elements
    sharing a word are not the same element.
    """
    screen = node(
        "Application",
        label="Settings",
        h=852,
        children=[
            node("Button", label="Optimizing Search and Siri", name="progress", y=326, h=44),
            node("Button", label="Siri", name="siri_row", y=794, h=44),
        ],
    )
    root = SnapshotNode.from_wda(screen)
    # A digest that kept only the first row, which is what the budget did.
    kept = build_digest(root, Settings().digest)
    kept.nodes = [n for n in kept.nodes if n.label == "Optimizing Search and Siri"]

    result = find_in_tree(root, "Siri", kept)
    by_label = {m.label: m for m in result.matches}
    assert by_label["Optimizing Search and Siri"].shown is True
    assert by_label["Siri"].shown is False, "a word in common is not the same row"


def test_child_text_inside_the_element_that_carries_it_is_reachable() -> None:
    """The other half of the same rule, and why containment is not simply dropped.

    A button's label contains its child StaticText's, and the child sits inside
    the button. Those really are one thing, and calling the child hidden would
    put a false alarm on the commonest shape in iOS.
    """
    screen = node(
        "Application",
        label="Settings",
        h=852,
        children=[
            node(
                "Button",
                label="Apple Account, Sign in to access your iCloud data",
                name="com.apple.settings.primaryAppleAccount",
                x=16,
                y=180,
                w=360,
                h=80,
                children=[node("StaticText", label="Apple Account", x=60, y=190, w=150, h=24)],
            )
        ],
    )
    root = SnapshotNode.from_wda(screen)
    result = find_in_tree(root, "Apple Account", build_digest(root, Settings().digest))
    assert result.total >= 1
    assert all(m.shown for m in result.matches)
    assert result.hidden == 0
