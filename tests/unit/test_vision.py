"""Annotated screenshots: the fallback for UI with no accessibility data."""

from __future__ import annotations

import io

import pytest
from trees import settings_screen

from ios_mcp.config import Settings
from ios_mcp.perception.digest import build_digest
from ios_mcp.perception.vision import _infer_scale, annotate
from ios_mcp.wda.models import SnapshotNode

PIL = pytest.importorskip("PIL")


def blank_png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def digest():
    return build_digest(SnapshotNode.from_wda(settings_screen()), Settings().digest)


def test_annotation_produces_a_valid_png_of_the_same_size() -> None:
    from PIL import Image

    source = blank_png(786, 1704)  # 2x of a 393pt-wide screen
    out = annotate(source, digest())

    image = Image.open(io.BytesIO(out))
    assert image.size == (786, 1704)


def test_annotation_actually_draws_something() -> None:
    source = blank_png(786, 1704)
    out = annotate(source, digest())
    assert out != source

    from PIL import Image

    colours = Image.open(io.BytesIO(out)).convert("RGB").getcolors(maxcolors=100_000)
    assert colours is not None and len(colours) > 1, "the image is still blank"


@pytest.mark.parametrize(("width", "expected"), [(393, 1.0), (786, 2.0), (1179, 3.0)])
def test_scale_is_inferred_from_the_image_width(width: int, expected: float) -> None:
    """Screenshots are in pixels and the tree is in points; a wrong scale
    would put every box in the wrong place."""
    assert _infer_scale(width, digest()) == expected


def test_a_slightly_inset_layout_still_snaps_to_a_real_scale() -> None:
    """Real screens rarely have an element spanning the full width."""
    assert _infer_scale(800, digest()) == 2.0


def test_an_empty_digest_returns_the_screenshot_untouched() -> None:
    source = blank_png(393, 852)
    assert (
        annotate(
            source,
            build_digest(
                SnapshotNode.from_wda(
                    {
                        "type": "Application",
                        "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
                        "isVisible": "1",
                        "isEnabled": "1",
                        "children": [],
                    }
                ),
                Settings().digest,
            ),
        )
        == source
    )


def test_a_box_at_the_top_edge_keeps_its_label_on_screen() -> None:
    """A tag drawn above y=0 would be invisible, which defeats the fallback."""
    from PIL import Image

    tree = settings_screen()
    tree["children"][1]["children"][0]["rect"]["y"] = 0
    d = build_digest(SnapshotNode.from_wda(tree), Settings().digest)
    out = annotate(blank_png(786, 1704), d)
    assert Image.open(io.BytesIO(out)).size == (786, 1704)
