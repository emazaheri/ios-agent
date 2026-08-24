"""Annotated screenshots: the last resort when accessibility data is absent.

Custom-drawn UI (games, canvas views, some cross-platform frameworks) exposes
nothing useful in the accessibility tree. Rather than dead-ending, the agent
gets a picture with the known elements boxed and labelled, and can pick one by
eye. Pillow is optional; without it the screenshot is returned unchanged rather
than failing.
"""

from __future__ import annotations

import logging

from ios_mcp.perception.digest import Digest

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


def annotate(png: bytes, digest: Digest, *, scale_hint: float | None = None) -> bytes:
    """Draw labelled boxes over each element in the digest.

    Screenshots come back in physical pixels while the accessibility tree uses
    points, so the rects are scaled by the ratio between them. Getting this
    wrong would put every box in the wrong place, which is worse than no boxes.
    """
    try:
        import io

        from PIL import Image, ImageDraw
    except ImportError:
        logger.info("Pillow is not installed; returning the screenshot unannotated")
        return png

    if not digest.nodes:
        return png

    image = Image.open(io.BytesIO(png)).convert("RGB")
    scale = scale_hint or _infer_scale(image.width, digest)
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
        _label(draw, node.ref, box[0], box[1], colour, scale)

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _label(draw, text: str, x: float, y: float, colour, scale: float) -> None:  # type: ignore[no-untyped-def]
    pad = 3 * scale
    width = (len(text) * 7 + 6) * scale
    height = 14 * scale
    # Keep the tag inside the image when the element touches the top edge.
    top = max(0.0, y - height)
    draw.rectangle((x, top, x + width, top + height), fill=colour)
    draw.text((x + pad, top + pad / 2), text, fill=(255, 255, 255))


def _infer_scale(image_width: int, digest: Digest) -> float:
    """Points to pixels, from the widest element we can see."""
    widest = max((n.rect.x + n.rect.width for n in digest.nodes), default=0.0)
    if widest <= 0:
        return 1.0
    ratio = image_width / widest
    # Real devices are 1x, 2x, or 3x; snap to avoid drift from a slightly
    # inset widest element.
    return min((1.0, 2.0, 3.0), key=lambda candidate: abs(candidate - ratio))
