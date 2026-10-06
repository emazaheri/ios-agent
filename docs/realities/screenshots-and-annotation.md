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
- **A native capture is too big to send.** 1206x2622 on an iPhone 17
  simulator and 1320x2868 on an iPhone 17 Pro Max, over the 2000 pixels a
  vision API accepts once a request carries many images (mobile-mcp #140).
  Screenshots leave the session with their long edge at 1568, the size
  Anthropic's API scales to anyway, annotated first at native size so the
  boxes are placed by the measured ratio. Pillow does the cutting when it is
  installed and macOS's `sips` when it is not: 2868 to 1568 on the phone,
  812KB to 550KB. Agents act on refs, so no coordinate depends on the scale.
- **Landscape agrees with itself inside an app.** Rotated, the screenshot,
  the window size and the tree all turn together: 2622x1206 pixels against
  874x402 points on a simulator, 956x440 on a phone, and the annotated boxes
  sit on the controls. A tap by ref focused Safari's address bar on a
  simulator and typed 7 and 3 into Calculator's scientific keypad on a phone.
- **A system alert does not rotate with the app.** SpringBoard is
  portrait-only on an iPhone, so over a landscape app it reports its alert in
  portrait coordinates: the decline button read as a 48x288 rect, a tap at its
  centre missed, and the alert stayed. A tap on a button of the alert WDA
  reports is pressed through `/alert/accept` by name instead, which cleared it
  in landscape. Annotation refuses such a screen rather than drawing on it.
- **Settings on iPhone does not rotate at all.** WDA answers "Unable To Rotate
  Device"; landscape has to be tested in an app that supports it.
