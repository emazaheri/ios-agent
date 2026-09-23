# Compound controls: pickers, wheels and steppers

A control iOS reports as several nodes, or as one node hiding several. Three of these came out of the first run against a real Clock alarm and none was reachable from a fixture.

- **A `Picker` is not a `PickerWheel`, and mapping both to one role cost the
  selection.** A picker's rect is the union of its wheels, so it is concentric
  with whichever wheel is in the middle, and `_dedupe_colocated` read that pair
  as one control reported twice. The container won, because it carries an
  accessibility id where a wheel carries only a value, and the middle column's
  selected option vanished: a date picker read `=6` and `=AM` either side of a
  wheel showing nothing. The container is now its own role and collapses like
  any other wrapper. The same bug is latent wherever a container's centre lands
  on a child, which is why the fixture has three wheels rather than one.
  Measured with the ranking change below: the oracle series unchanged on all
  thirteen tasks, the golden flows 8306 device tokens to 8361, +0.7%, and the
  resolution-tier distribution identical, so two capabilities cost under a
  percent between them.
- **An accessibility id is not a label, and treating them as equal deleted a
  control.** `_beats` asked "does this node have text" as a boolean, so a
  `Stepper` named `interval_stepper` outranked buttons labelled "Increment" and
  "Decrement". A stepper's rect is exactly its two buttons side by side, its
  centre lands on the boundary they share, and `Rect.contains` is inclusive at
  an edge, so *both* children were coincident with the container and both lost.
  The digest showed one node with an id and nothing to tap, whose centre
  happens to sit on Increment: raising a value silently worked and lowering one
  was unreachable at any price. Ranked now, label above id above nothing.
- **A picker wheel hides every option but the one it is showing.** There is no
  options list in the tree at any price, so nothing can compute how far to
  turn, and `set_value` moves it a row at a time and reads it back. The version
  before it sent every non-switch role to `send_keys`, which types into
  whatever holds keyboard focus: a wheel holds none, so the call succeeded,
  changed nothing, and reported success. Segmented controls and steppers are
  not set this way at all, because iOS already reports their parts and the
  digest already shows them; `set_value` on one says so and names the part.
- **A wheel's value is a spoken phrase, not the thing it looks like.** A real
  Clock alarm reports `5 o’clock`, `36 minutes` and `PM`, with a typographic
  apostrophe. `set_value("7")` cannot match at any tier, which is why failing
  lists every option it saw: that list is the only way a caller learns the
  spelling. Measured: 12 steps and 30.7s to walk a twelve-hour wheel once.
- **Apple wraps a picker twice, and the outer wrapper is a `Cell`.** Collapsing
  the `Picker` is not enough. The `Cell` has the same rect, so it is mutually
  centred with the middle wheel of a three-column time picker, and with
  `picker` missing from `ROLE_PRECEDENCE` the tie went to the cell: the minutes
  column vanished while the hours and the meridiem either side of it survived.
  A role absent from that tuple is not neutral, it is beaten by every wrapper
  that has a role on it.
- **A drag cannot move a `UIPickerView` by a known amount; a tap can.**
  `UIPickerView` decelerates a flick through however many rows the momentum
  carries. Measured on an iPhone 17 Pro Max: a fifth of the wheel’s height
  over 0.2s moved four rows, slowing it to 1.6s and shortening it to a tenth
  still moved three, and the count varied between runs. Since an hour wheel
  **wraps**, a nondeterministic multi-row step cannot reach two thirds of the
  options at all: stepping by four from 5 o’clock reaches 1 and 9 and nothing
  else, forever. A tap 30 points from the middle selects the neighbouring row
  exactly, twice out of two, and at 55 points it selects two rows. So rows are
  about 30 points and the band ends near 45. `set_value` on a neighbour went
  from 61.4s of random walk to 9.6s.
- **A wheel that wraps has no end to detect.** "Stop when the value stops
  changing" never returns on an hour wheel. The loop stops on a value it has
  already seen, which covers both a wrap and a finite wheel run onto its own
  end, and a wheel that returns to where it started is known to have been
  walked in full, so the reverse pass is skipped.
- **A simulator's Settings is a reduced build, and it has no wheel in it.** On
  iOS 27, iPhone 18 Pro, there is no Date & Time pane under General, no Sounds
  pane, and no Clock app installed at all, so there is no stock picker wheel or
  slider reachable from Settings. The compound-control integration tests skip
  on that build and say why, and `tests/integration/test_device_picker.py`
  carries the wheel proof instead, against a real iPhone. Three of the bugs
  listed above came out of its first run and none of them was reachable from a
  fixture, which is the rule this file already states and which held again.
