"""Screen fingerprints drive change detection, settle loops, and loop detection."""

from __future__ import annotations

from trees import form_screen, list_screen, node, settings_screen

from ios_mcp.config import Settings
from ios_mcp.perception.digest import build_digest
from ios_mcp.wda.models import SnapshotNode


def fp(tree: dict) -> str:
    return build_digest(SnapshotNode.from_wda(tree), Settings().digest).fingerprint


def test_identical_screens_fingerprint_identically() -> None:
    assert fp(settings_screen()) == fp(settings_screen())


def test_a_toggled_switch_changes_the_fingerprint() -> None:
    """Without this, an agent cannot tell whether its tap did anything."""
    assert fp(settings_screen(airplane_on=False)) != fp(settings_screen(airplane_on=True))


def test_a_different_screen_changes_the_fingerprint() -> None:
    assert fp(settings_screen()) != fp(form_screen())


def test_sub_pixel_animation_jitter_does_not_change_the_fingerprint() -> None:
    """Otherwise the settle loop would never converge."""
    base = settings_screen()
    jittered = settings_screen()
    jittered["children"][1]["children"][0]["rect"]["y"] += 1
    assert fp(base) == fp(jittered)


def test_a_real_layout_shift_does_change_the_fingerprint() -> None:
    base = settings_screen()
    shifted = settings_screen()
    shifted["children"][1]["children"][0]["rect"]["y"] += 40
    assert fp(base) != fp(shifted)


def test_scrolling_a_list_changes_the_fingerprint() -> None:
    a = list_screen(rows=50)
    b = list_screen(rows=50)
    for cell in b["children"][0]["children"][1]["children"]:
        cell["rect"]["y"] -= 200
        for wrapper in cell["children"]:
            wrapper["rect"]["y"] -= 200
            for leaf in wrapper["children"]:
                leaf["rect"]["y"] -= 200
    assert fp(a) != fp(b)


def test_element_order_does_not_affect_the_fingerprint() -> None:
    """Sibling order in the raw tree is not stable across WDA snapshots."""
    a = node(
        "Application",
        h=852,
        children=[node("Button", label="One", y=100), node("Button", label="Two", y=200)],
    )
    b = node(
        "Application",
        h=852,
        children=[node("Button", label="Two", y=200), node("Button", label="One", y=100)],
    )
    assert fp(a) == fp(b)
