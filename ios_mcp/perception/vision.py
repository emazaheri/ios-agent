"""Annotated screenshots: the last resort when accessibility data is absent.

Custom-drawn UI (games, canvas views, some cross-platform frameworks) exposes
nothing useful in the accessibility tree, and a well-labelled app can still
render a word that appears nowhere in it. Rather than dead-ending, the caller
gets a picture with the known elements boxed and labelled, and can pick one by
eye.

Two rules govern everything here. A box in the wrong place is worse than no
box, because the caller acts on it: every path that cannot place a box
correctly raises instead of drawing. And a picture the caller cannot read is
the same as no picture, which is why the label is drawn in a font that grows
with the screenshot rather than PIL's fixed bitmap default.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from ios_mcp.errors import NotSupported, ToolchainMissing
from ios_mcp.perception.digest import Digest

if TYPE_CHECKING:
    from PIL.ImageFont import FreeTypeFont

logger = logging.getLogger(__name__)

#: Cycled so adjacent boxes stay distinguishable.
_COLOURS = [
    (255, 59, 48),
    (0, 122, 255),
    (52, 199, 89),
    (255, 149, 0),
    (175, 82, 222),
    (255, 45, 85),
]

#: First one that exists wins. macOS is what this project runs on; the Linux
#: paths are there so a container does not silently drop to the bitmap font.
_FONT_PATHS = [
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

#: Points, at 1x. A ref is three or four characters, so this is about the size
#: of the smallest label iOS itself draws.
_LABEL_POINTS = 11

_PILLOW_REMEDY = (
    'Install the vision extra: `uv sync --extra vision`, or `pip install "ios-mcp[vision]"`.'
)

#: Width and height must agree on the scale to within this much. They disagree
#: when the screenshot is not in the tree's orientation, and then no single
#: factor places the boxes.
_SCALE_TOLERANCE = 0.02

_fonts: dict[int, FreeTypeFont] = {}


def ensure_available() -> None:
    """Raise unless a screenshot can actually be annotated.

    Separate from `annotate` so a caller can check before spending anything on
    the picture. `IosSession.screenshot` observes the screen first, and an
    observation is a tree fetch and a ref-table generation; neither should be
    spent to discover a missing package.
    """
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise ToolchainMissing(
            "Annotating a screenshot needs Pillow, which is not installed.",
            hint=_PILLOW_REMEDY,
        ) from exc


def annotate(png: bytes, digest: Digest, *, scale_hint: float | None = None) -> bytes:
    """Draw labelled boxes over each element in the digest.

    Screenshots come back in physical pixels while the accessibility tree uses
    points, so the rects are scaled by the ratio between them. Getting this
    wrong would put every box in the wrong place, which is worse than no boxes,
    so the ratio is measured against the screen the digest recorded rather than
    inferred from its contents.
    """
    import io

    ensure_available()
    from PIL import Image, ImageDraw

    if not digest.nodes:
        return png

    image = Image.open(io.BytesIO(png)).convert("RGB")
    scale = scale_hint or _scale_for(image.width, image.height, digest)
    font = _font(scale)
    draw = ImageDraw.Draw(image)

    for index, node in enumerate(digest.nodes):
        colour = _COLOURS[index % len(_COLOURS)]
        box = (
            node.rect.x * scale,
            node.rect.y * scale,
            (node.rect.x + node.rect.width) * scale,
            (node.rect.y + node.rect.height) * scale,
        )
        draw.rectangle(box, outline=colour, width=max(2, int(2 * scale)))
        _label(draw, node.ref, box[0], box[1], colour, font, image.size)

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _label(
    draw: Any,
    text: str,
    x: float,
    y: float,
    colour: tuple[int, int, int],
    font: FreeTypeFont,
    size: tuple[int, int],
) -> None:
    """Draw the ref as a tag pinned to the top-left corner of its box.

    Measured rather than estimated: a character-count guess at the width was
    right only for the bitmap font it was written against, and left the text
    floating in an oversized bar at 2x and 3x.
    """
    pad = max(2, round(font.size / 4))
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width = (right - left) + pad * 2
    height = (bottom - top) + pad * 2
    # Keep the tag on screen when the element touches an edge. Above the box by
    # preference, inside it when there is no room above.
    tag_y = y - height if y - height >= 0 else y
    tag_x = min(max(0.0, x), float(size[0]) - width)
    draw.rectangle((tag_x, tag_y, tag_x + width, tag_y + height), fill=colour)
    draw.text((tag_x + pad - left, tag_y + pad - top), text, fill=(255, 255, 255), font=font)


def _font(scale: float) -> FreeTypeFont:
    """A font at the screenshot's scale, cached by size.

    A ref drawn at a fixed size is unreadable on the 3x screenshots this is
    most useful on: `ImageFont.load_default()` with no size is a bitmap face
    that ignores `size` entirely, so the label came out a third of the height
    of the tag drawn around it. Passing a size gets a scalable face even when
    no system font is found.
    """
    from PIL import ImageFont

    size = max(_LABEL_POINTS, round(_LABEL_POINTS * scale))
    cached = _fonts.get(size)
    if cached is not None:
        return cached
    font: FreeTypeFont | None = None
    for path in _FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        logger.info("No system font found; labelling with Pillow's bundled face")
        # Typed as the bitmap face too, but with a size it is always scalable.
        font = cast("FreeTypeFont", ImageFont.load_default(size))
    _fonts[size] = font
    return font


def _scale_for(image_width: int, image_height: int, digest: Digest) -> float:
    """Points to pixels, measured against the screen the digest recorded."""
    screen = digest.screen
    if screen is None or screen.width <= 0 or screen.height <= 0:
        return _infer_scale(image_width, digest)

    horizontal = image_width / screen.width
    vertical = image_height / screen.height
    if abs(horizontal - vertical) > _SCALE_TOLERANCE * max(horizontal, vertical):
        # Usually a screenshot taken in one orientation against a tree read in
        # another. There is no single factor that places the boxes, and drawing
        # them anyway would be confidently wrong.
        raise NotSupported(
            f"The screenshot is {image_width}x{image_height} pixels but the screen is "
            f"{screen.width:g}x{screen.height:g} points, which is not one scale. "
            "The image and the accessibility tree disagree about the orientation.",
            hint="Re-read the screen with ios_observe and take the screenshot again.",
        )
    return horizontal


def _infer_scale(image_width: int, digest: Digest) -> float:
    """Points to pixels, from the widest element we can see.

    The fallback for a digest built before `screen` existed. It is a guess: a
    layout whose widest element is inset, or one narrowed by `region`, moves
    the ratio far enough to snap to the wrong factor.
    """
    widest = max((n.rect.x + n.rect.width for n in digest.nodes), default=0.0)
    if widest <= 0:
        return 1.0
    ratio = image_width / widest
    # Real devices are 1x, 2x, or 3x; snap to avoid drift from a slightly
    # inset widest element.
    return min((1.0, 2.0, 3.0), key=lambda candidate: abs(candidate - ratio))
