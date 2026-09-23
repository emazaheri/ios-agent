# Screenshots and annotation

The one output a caller trusts by eye, and the unit mismatch underneath it.

- **The tree is in points and the screenshot is in pixels.** The ratio is
  measured from the screen rect the digest carries, never inferred from its
  contents. A screen whose controls are all inset, which is what a drawn view
  with one button on it looks like and the exact case annotation exists for,
  reads as 3x when it is 2x, and every box lands half again too large. A box in
  the wrong place is worse than no box, so a screenshot whose width and height
  disagree about the scale is refused rather than drawn on. Ref labels are
  drawn in a font sized from that ratio, because `ImageFont.load_default()`
  with no size is a bitmap face that ignores it, and a ref a third the height
  of the tag around it is not readable on the 3x screenshots this is for.
