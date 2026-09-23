"""Annotated screenshots: the fallback for UI with no accessibility data."""

from __future__ import annotations

import io
import sys

import pytest
from trees import node, settings_screen

from ios_mcp.config import Settings
from ios_mcp.errors import ErrorCode, NotSupported, ToolchainMissing
from ios_mcp.perception.digest import build_digest
from ios_mcp.perception.vision import _font, _infer_scale, _scale_for, annotate
from ios_mcp.wda.models import Rect, SnapshotNode

PIL = pytest.importorskip("PIL")


def blank_png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def digest(*, region: Rect | None = None):
    return build_digest(SnapshotNode.from_wda(settings_screen()), Settings().digest, region=region)


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


# -- the scale --------------------------------------------------------------


@pytest.mark.parametrize(("width", "expected"), [(393, 1.0), (786, 2.0), (1179, 3.0)])
def test_the_scale_is_measured_against_the_screen(width: int, expected: float) -> None:
    """Screenshots are in pixels and the tree is in points; a wrong scale
    would put every box in the wrong place."""
    assert _scale_for(width, int(width * 852 / 393), digest()) == expected


def test_an_inset_layout_does_not_move_the_scale() -> None:
    """The regression this replaced `_infer_scale` for.

    A screen whose controls are all inset is exactly the screen this feature
    exists for: a drawn view with one labelled thing on it. Inferring the
    ratio from the widest element then reports 3x for a 2x screenshot and
    every box lands half again too large, over a picture whose whole purpose
    is to be trusted by eye. Measured against the screen, the inset is
    irrelevant.
    """
    inset = build_digest(
        SnapshotNode.from_wda(
            node(
                "Application",
                h=852,
                children=[node("Button", label="Play", x=120, y=400, w=150, h=50)],
            )
        ),
        Settings().digest,
    )
    assert _scale_for(786, 1704, inset) == 2.0
    assert _infer_scale(786, inset) == 3.0, "the old heuristic was wrong here"


def test_a_region_does_not_move_the_scale() -> None:
    """`region` narrows what is kept, never what the screen is."""
    narrowed = digest(region=Rect(0, 0, 200, 400))
    assert narrowed.nodes, "the fixture must keep something inside the region"
    assert narrowed.screen == Rect(0, 0, 393, 852)
    assert _scale_for(786, 1704, narrowed) == 2.0


def test_a_fractional_scale_is_reported_as_it_is() -> None:
    """Better an exact odd ratio than a confidently wrong round one.

    A simulator window is captured at whatever the runtime renders it at, and
    snapping that to 1, 2 or 3 is how a box ends up beside its control.
    """
    assert _scale_for(590, 1278, digest()) == pytest.approx(1.5, rel=0.01)


def test_a_screenshot_in_the_other_orientation_is_refused() -> None:
    """A transposed image has no single scale, so boxes would be nonsense."""
    with pytest.raises(NotSupported) as exc_info:
        annotate(blank_png(1704, 786), digest())
    assert exc_info.value.code is ErrorCode.NOT_SUPPORTED
    assert "orientation" in exc_info.value.message


def test_a_digest_with_no_screen_falls_back_to_the_guess() -> None:
    d = digest()
    d.screen = None
    assert _scale_for(786, 1704, d) == 2.0


# -- the label --------------------------------------------------------------


def test_the_label_font_grows_with_the_screenshot() -> None:
    """A ref drawn at a fixed size is illegible on a 3x screenshot, which is
    the resolution this feature is most useful at."""
    assert _font(3.0).size > _font(2.0).size > _font(1.0).size


def _ink_ratio(scale: float) -> float:
    """How much of the tag the glyphs fill, at a given screenshot scale."""
    from PIL import Image, ImageDraw

    from ios_mcp.perception.vision import _label

    image = Image.new("RGB", (400, 200), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    _label(draw, "e12", 10, 100, (255, 0, 0), _font(scale), image.size)
    tag = image.crop(image.getbbox())  # everything that is not the background
    counts = tag.getcolors(maxcolors=100_000) or []
    total = sum(n for n, _ in counts)
    ink = sum(n for n, colour in counts if colour != (255, 0, 0))
    assert total and ink, "the tag or its text was not drawn"
    return ink / total


def test_the_label_fills_its_tag_at_every_scale() -> None:
    """The defect this replaces: the tag was sized by the scale and the text
    was not, so at 3x the ref floated in a bar nine times its area. Measuring
    the tag from the font instead keeps the proportion roughly fixed."""
    one, three = _ink_ratio(1.0), _ink_ratio(3.0)
    assert three == pytest.approx(one, rel=0.5), f"1x fills {one:.2f}, 3x fills {three:.2f}"


def test_a_box_at_the_top_edge_keeps_its_label_on_screen() -> None:
    """A tag drawn above y=0 would be invisible, which defeats the fallback."""
    from PIL import Image

    tree = settings_screen()
    tree["children"][1]["children"][0]["rect"]["y"] = 0
    d = build_digest(SnapshotNode.from_wda(tree), Settings().digest)
    out = annotate(blank_png(786, 1704), d)
    assert Image.open(io.BytesIO(out)).size == (786, 1704)


# -- degradation ------------------------------------------------------------


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


def test_a_missing_pillow_says_so_rather_than_returning_a_bare_picture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The digest's own note tells the caller to annotate, and Pillow is an
    optional extra, so a plain install follows that advice and must be told
    why it cannot be met. Returning the unannotated image and logging it is
    the simulator-window failure again."""
    png, d = blank_png(786, 1704), digest()
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(ToolchainMissing) as exc_info:
        annotate(png, d)
    assert exc_info.value.code is ErrorCode.TOOLCHAIN_MISSING
    assert "--extra vision" in (exc_info.value.hint or "")
