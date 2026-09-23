# Perception geometry, and what a node carries

Rules about rects, visibility, identity and roles. Every bug here was a rule of the form "role X is always Y", which is a bet on the app's UI framework.

- **iOS reports one control several times.** A switch row appears row-wide with
  the label and again as the toggle at the trailing edge. `_dedupe_colocated`
  merges them, taking semantics from the labelled node and geometry from the
  tighter one. Without the geometry half, taps hit the label and switches never
  move while reporting success.
- **Real rects do not nest.** A toggle at `x=305 w=63` sits inside a row at
  `x=36 w=330`, overhanging by two points. Containment must be proportional.
- **A control's value is not its identity.** Switches report `value="0"`. Using
  `_text_of` (which includes value) where identity is meant makes an unlabelled
  toggle look like a named element. Use `_identity_text`.
- **Split views keep most of the screen identical during navigation**, which is
  why the screen title is part of the fingerprint.
- **`isVisible` is trustworthy, and the rects around it are not.** On a
  virtualised list every offscreen row can report the same `y`, so geometry
  alone cannot tell what is on screen. Verified by capturing a tree and a
  screenshot in the same instant and comparing them: every string the
  screenshot showed was marked visible, and every string it did not was marked
  invisible.
- **Text the digest shows must be text the agent can name.** `_value_of` was
  fixed to surrender a field split across label and value; the resolution tiers
  matched `label` only, so the answer on screen was the one string that could
  not be targeted. A value now has its own tier below the label rather than
  beside it: typing into a search field makes that field's value an exact match
  for the row being looked for, and treating the two as peers turns the next
  tap into an ambiguity error. The eval floors caught that, not reasoning.
- **A home screen icon is not an image.** `XCUIElementTypeIcon` was mapped to
  `image`, and the drawn-control rule only rescues an image that is
  *unlabelled*, so every app icon on SpringBoard collapsed into one opaque
  `Home screen icons` container. The agent could not launch an app by tapping
  it, on any device, and asked to do so it repeated `tap Maps` against
  "Nothing on screen matches" until its budget ran out. No eval caught it
  because every task starts inside an app something else opened. `Icon` is now
  its own actionable role. Measured: the oracle series unchanged, the golden
  flows 8648 device tokens to 8595, so the capability was free.
