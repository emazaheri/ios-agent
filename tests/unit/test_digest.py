"""UI Digest compaction: the layer that decides what an agent loop costs."""

from __future__ import annotations

import json

from trees import (
    custom_header_screen,
    drawn_controls_screen,
    form_screen,
    list_screen,
    node,
    opaque_canvas_screen,
    settings_screen,
    third_party_card_screen,
    webview_screen,
)

from ios_mcp.config import DigestSettings, Settings
from ios_mcp.perception.digest import build_digest
from ios_mcp.wda.models import Rect, SnapshotNode


def digest_of(tree: dict, settings: DigestSettings | None = None, **kw):
    return build_digest(SnapshotNode.from_wda(tree), settings or Settings().digest, **kw)


def test_wrappers_and_label_echoes_are_removed() -> None:
    d = digest_of(settings_screen())
    roles = [n.role for n in d.nodes]
    assert "application" not in roles
    assert "window" not in roles
    assert "nav" not in roles
    assert d.title == "Settings"


def test_one_visual_row_produces_exactly_one_element() -> None:
    """UIKit reports a switch row three times: cell, echoed label, and switch.

    An agent shown three entries for one row wastes tokens on all of them and
    then cannot tell which to act on.
    """
    d = digest_of(settings_screen())
    airplane = [n for n in d.nodes if n.label == "Airplane Mode"]
    assert len(airplane) == 1
    # The control survives rather than the row that wraps it, because it is
    # what carries the state and what set_value has to target.
    assert airplane[0].role == "switch"
    assert airplane[0].value == "0"


def test_every_actionable_element_survives() -> None:
    d = digest_of(form_screen())
    labels = {n.label for n in d.nodes}
    assert {"Cancel", "Send", "To:", "Subject:", "Body"} <= labels


def test_disabled_elements_are_kept_but_marked() -> None:
    """An agent needs to know Send exists and is greyed out, not that it is absent."""
    d = digest_of(form_screen())
    disabled = [n for n in d.nodes if not n.enabled]
    assert len(disabled) == 1
    assert disabled[0].label == "Send"
    assert disabled[0].actionable is False
    assert "disabled" in disabled[0].render()


def test_invisible_and_zero_size_nodes_are_dropped() -> None:
    d = digest_of(form_screen())
    assert all(n.rect.area > 0 for n in d.nodes)


def test_switch_state_is_preserved() -> None:
    off = digest_of(settings_screen(airplane_on=False))
    on = digest_of(settings_screen(airplane_on=True))
    off_switch = next(n for n in off.nodes if n.role == "switch")
    on_switch = next(n for n in on.nodes if n.role == "switch")
    assert off_switch.value == "0"
    assert on_switch.value == "1"
    assert on_switch.selected is True
    assert off_switch.selected is False


def test_scrollable_containers_are_kept_as_scroll_targets() -> None:
    d = digest_of(settings_screen())
    tables = [n for n in d.nodes if n.role == "table"]
    assert len(tables) == 1
    assert tables[0].scrollable is True


def test_a_long_list_compacts_by_more_than_an_order_of_magnitude() -> None:
    """The whole point of this layer: a 200-row list must stay affordable."""
    tree = list_screen(rows=200)
    raw_tokens = len(json.dumps(tree)) // 4
    d = digest_of(tree)

    assert raw_tokens > 15_000, "the fixture should be realistically large"
    assert d.estimated_tokens() < 1600, f"digest cost {d.estimated_tokens()} tokens"
    assert raw_tokens / max(d.estimated_tokens(), 1) > 10


def test_budget_truncation_is_announced_not_silent() -> None:
    settings = DigestSettings(token_budget=200, max_nodes=1000)
    d = digest_of(list_screen(rows=200), settings)
    assert d.truncated is True
    assert d.total_nodes > len(d.nodes)
    assert "more elements omitted" in d.render()
    assert "ios_observe" in d.render()


def test_budget_keeps_the_most_salient_elements_first() -> None:
    """Under pressure, keep what the agent can act on."""
    settings = DigestSettings(token_budget=200, max_nodes=1000)
    d = digest_of(list_screen(rows=200), settings)
    assert d.nodes, "budget must never produce an empty digest"
    assert sum(1 for n in d.nodes if n.actionable) / len(d.nodes) > 0.8


def test_max_nodes_is_enforced_independently_of_the_token_budget() -> None:
    settings = DigestSettings(token_budget=100_000, max_nodes=10)
    d = digest_of(list_screen(rows=200), settings)
    assert len(d.nodes) == 10
    assert d.truncated is True


def test_query_narrows_to_matching_elements() -> None:
    d = digest_of(list_screen(rows=200), query="Contact 007")
    labels = [n.label for n in d.nodes]
    assert labels == ["Contact 007"]


def test_region_narrows_to_a_screen_area() -> None:
    """Only elements overlapping the region survive; distant rows are dropped."""
    d = digest_of(list_screen(rows=200), region=Rect(0, 780, 393, 72))
    labels = {n.label for n in d.nodes}
    assert {"Contacts", "Recents"} <= labels
    assert "Contact 000" not in labels
    assert "Contact 100" not in labels
    assert len(d.nodes) < 10


def test_elements_are_listed_in_reading_order() -> None:
    d = digest_of(form_screen())
    ys = [n.rect.y for n in d.nodes]
    assert ys == sorted(ys)


def test_refs_are_sequential_and_unique() -> None:
    d = digest_of(list_screen(rows=50))
    refs = [n.ref for n in d.nodes]
    assert len(refs) == len(set(refs))
    assert refs[0] == "e1"


def test_render_is_readable_and_names_the_screen() -> None:
    d = digest_of(settings_screen(), app="com.apple.Preferences")
    text = d.render()
    assert text.startswith("screen: com.apple.Preferences")
    assert '"Settings"' in text.splitlines()[0]
    assert "airplane_switch" in text


# -- what apps outside Apple's own do differently ---------------------------
#
# Every case below was found by pointing the agent at a real third-party app
# and discovering it could not read the screen. Settings never exposes any of
# them, which is exactly why they survived this long.


def test_a_static_text_shows_its_value_as_well_as_its_label() -> None:
    """A field split across label and value must surrender both.

    Found on a real profile card, where the label was "Date prompt:" and the
    value was the answer. `_text_of` returns `label or value`, so the digest
    showed the question and silently dropped the answer, and the agent
    reported that the app "did not expose the prompt text".
    """
    d = digest_of(third_party_card_screen())

    assert "Date prompt:" in d.render()
    assert "Let's get together" in d.render(), "the value was dropped, so only the question showed"


def test_a_container_that_carries_text_is_content_not_a_wrapper() -> None:
    """`Other` is usually an empty box and sometimes the whole screen.

    Apple puts card text in `StaticText` and `Cell`, so collapsing every
    `Other` cost nothing on Settings. Third-party apps compose a card from
    images and hang the readable version on the wrapping container, and
    collapsing that leaves the agent looking at unlabelled boxes.
    """
    d = digest_of(third_party_card_screen())

    assert "Something my pet thinks about me" in d.render()


def test_a_container_with_no_text_is_still_collapsed() -> None:
    """The other half of the rule. Empty wrappers are still noise."""
    d = digest_of(third_party_card_screen())
    others = [n for n in d.nodes if n.role == "other"]

    assert all(n.label for n in others), "an unlabelled container survived"
    assert "Plain row" in d.render(), "the wrapper collapsed but took its child with it"


def test_the_application_and_window_still_collapse_even_though_labelled() -> None:
    """Their labels are the app name and noise, and the app is reported already."""
    d = digest_of(third_party_card_screen())

    assert not any(n.role in ("application", "window") for n in d.nodes)
    assert d.app == "Cards"


def test_a_value_that_is_state_or_noise_stays_hidden() -> None:
    """Showing every value would trade one unreadable screen for a louder one.

    A percentage, a badge count, and anything that merely repeats its label are
    not content, and the budget is the scarcest thing the digest spends.
    """
    rendered = digest_of(third_party_card_screen()).render()

    assert "45%" not in rendered
    assert '"1"' not in rendered


def test_a_switch_still_reports_its_state() -> None:
    """The change to value handling must not cost the stateful roles anything."""
    rendered = digest_of(settings_screen(airplane_on=True)).render()

    assert "=1" in rendered


def test_a_drawn_control_survives_on_its_accessibility_id_alone() -> None:
    """An unlabelled image is not always a decoration.

    `_is_noise` dropped every image with no label, which is true of Apple's
    apps and false everywhere a control is rendered rather than composed. The
    id is the only thing that distinguishes a heart icon from the spacer beside
    it, and dropping both leaves the agent unable to see the one it needs.
    """
    d = digest_of(drawn_controls_screen())
    ids = [n.identifier for n in d.nodes]

    assert "like_post_1" in ids, "the only handle the control has was thrown away"


def test_a_decoration_with_neither_label_nor_id_is_still_dropped() -> None:
    """The other half. Keeping every unlabelled image would flood the budget."""
    d = digest_of(drawn_controls_screen())
    images = [n for n in d.nodes if n.role == "image"]

    assert all(n.label or n.identifier for n in images), "a nameless decoration survived"


def test_a_container_with_only_a_test_id_is_not_collapsed() -> None:
    """React Native's everyday shape: a `testID` and no `accessibilityLabel`.

    `_is_wrapper` asked `_text_of`, which looks at label, value and
    placeholder but never at the identifier, so a container named only by its
    test id collapsed to nothing and took its meaning with it.
    """
    d = digest_of(drawn_controls_screen())

    assert "compose_button" in [n.identifier for n in d.nodes]


def test_a_drawn_control_is_offered_as_something_to_tap() -> None:
    """Keeping it is not enough if the agent is told it cannot act on it.

    `_is_actionable` asks only whether the role is in INTERACTIVE_ROLES, and a
    drawn control is an `image` or an `other`. Resolution filters to actionable
    elements first, so a control marked inert is unreachable by target even
    though it is sitting in the digest.
    """
    d = digest_of(drawn_controls_screen())
    like = next(n for n in d.nodes if n.identifier == "like_post_1")

    assert like.actionable


def test_a_card_that_wraps_a_control_is_not_itself_a_control() -> None:
    """Widening actionability must stop at the thing that is actually tapped.

    The post wraps the like icon. Marking both actionable would make every
    card an ambiguous target and put the tap at the card's centre, which is
    the same failure as tapping a switch row's label.
    """
    d = digest_of(drawn_controls_screen())
    post = next(n for n in d.nodes if n.identifier == "post_1")

    assert not post.actionable


def test_a_screen_titled_by_a_drawn_header_still_has_a_title() -> None:
    """The title is part of the fingerprint, and apps do not all ship a nav bar.

    Without it two structurally similar screens hash the same, and navigating
    between them reads as an action that changed nothing. That is the split-view
    failure already recorded in CLAUDE.md, reached by a different route.
    """
    d = digest_of(custom_header_screen())

    assert d.title == "Inbox"


def test_a_navigation_bar_still_wins_over_anything_drawn_above_it() -> None:
    """The fallback must not outrank the real thing where there is one."""
    assert digest_of(settings_screen()).title == "Settings"
    assert digest_of(third_party_card_screen()).title == "Profile"


# -- screens the accessibility tree cannot reach ----------------------------
#
# Not a bug and not fixable here: web content is a second tree behind a context
# switch this project has no concept of, and a Flutter canvas has no tree at
# all. What is fixable is the silence. A near-empty digest is indistinguishable
# from a screen that genuinely has four things on it, and an agent that cannot
# tell those apart will keep tapping at nothing.


def test_a_screen_behind_a_webview_says_so() -> None:
    d = digest_of(webview_screen())

    assert any("WebView" in note for note in d.notes)
    assert "WebView" in d.render(), "the note has to reach the model, not just the payload"


def test_a_screen_with_no_accessibility_data_says_so() -> None:
    d = digest_of(opaque_canvas_screen())

    assert any("no accessibility data" in note for note in d.notes)


def test_an_ordinary_screen_says_nothing() -> None:
    """A warning that cries wolf is worse than no warning.

    Both detectors have to stay silent on every screen this project already
    reads correctly, including the third-party ones, or the agent learns to
    ignore them.
    """
    for tree in (settings_screen(), list_screen(), form_screen(), third_party_card_screen()):
        assert digest_of(tree).notes == []


def test_a_webview_is_not_also_reported_as_drawn_content() -> None:
    """Web content and canvas content are different problems and different fixes."""
    assert len(digest_of(webview_screen()).notes) == 1


def test_a_disclosure_glyph_named_only_by_its_id_is_still_noise() -> None:
    """The shape iOS 26 actually ships, and the cost of keeping id-only nodes.

    Settings emits one enabled `Image` per row whose only name is
    `chevron.forward` on the identifier. The older guard wanted a *disabled*
    node with the glyph as its *label*, so neither half matched, and keeping
    unlabelled nodes that carry an id reopened the exact noise that guard
    exists to close: nine extra elements on the root screen alone.
    """
    tree = node(
        "Application",
        label="Settings",
        name="Settings",
        h=852,
        children=[
            node("Button", label="General", name="com.apple.settings.general", y=100),
            node("Image", name="chevron.forward", x=350, y=110, w=20, h=20),
        ],
    )
    d = digest_of(tree)

    assert [n.identifier for n in d.nodes] == ["com.apple.settings.general"]
