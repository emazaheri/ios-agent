"""UI Digest compaction: the layer that decides what an agent loop costs."""

from __future__ import annotations

import json

from trees import form_screen, list_screen, settings_screen

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
