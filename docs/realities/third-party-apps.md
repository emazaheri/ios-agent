# Apps Apple did not write

The habits that broke perception the first time this stack was pointed at a non-Apple screen. None of them is exotic; they are ordinary practice outside UIKit's table views.

- **Apps outside Apple's own hide their text in two places Settings never
  does**, and both made a real third-party screen unreadable to the agent. A
  `StaticText` splits a field across `label` and `value` (label "Date prompt:",
  value the answer), so `_text_of` returning `label or value` showed the
  question and dropped the answer. And a card composed of images hangs its
  readable version on the wrapping `Other`, which the digest collapsed
  unconditionally. Apple keeps card text in `StaticText` and `Cell`, so neither
  costs anything on Settings and neither was visible until a real profile was
  opened.
- **A well-labelled third-party app costs the drawn-control rule nothing.** On
  a real Hinge screen the id-only shape occurs five times, all of them icons
  nested inside labelled buttons, and `_dedupe_colocated` folds every one into
  the control that wraps it. Digest identical before and after the rule, to the
  element. The +3.1% measured on Settings is the worst case, not the typical
  one.
- **Visible text and accessibility label can be different strings.** That same
  screen renders a filter chip reading "Signals" whose label is the raw
  localisation key `discover_circleMembersFilter_accessibilityLabel`. Nothing
  in the tree carries the word a person sees, so `target="Signals"` cannot
  match at any tier. Not fixable in perception; it is an argument for the
  screenshot path, not against the digest.
- **A node nobody labelled is not automatically decoration.** Outside Apple's
  apps a control is often *drawn* rather than composed, and arrives as an
  `Image` or an `Other` with an accessibility id and no label at all. The
  digest dropped every unlabelled image as noise and marked every non-Apple
  role inert, so the one thing worth tapping was both invisible and
  unreachable. The line is the id: a developer who named something nobody can
  read named it so that something could find it. React Native produces the
  same shape wholesale, since `testID` is set far more often than
  `accessibilityLabel`.
- **Not every app ships a `UINavigationBar`, and the header is lower than you
  think.** The screen title is part of the fingerprint precisely so a
  navigation is not mistaken for an action that did nothing, and an app drawing
  its own header left it empty. The topmost line of text in the upper band now
  stands in. The band was first set at 15% against an invented fixture whose
  header sat at 7%, and it did nothing on the real screen it existed for: a
  third-party Discover screen puts a filter row above its title, which lands at
  **197 of 956 points, 21%**, with the next text at 24%. Measured on an iPhone
  17 Pro Max; `custom_header_screen` now carries those proportions.
- **A drawn header is the only copy of its text; a navigation bar's is not.**
  The echo filter is seeded with the screen title because a nav bar reports its
  title twice, as the bar and as a StaticText inside it. Seeding a *drawn*
  title deletes content instead: real Settings shows search results under no
  nav bar at all, so `No Results for "Airplane"` was promoted to the title and
  then dropped as an echo of itself, leaving nothing on screen carrying the
  word searched for. `_screen_title` now reports whether the title came from
  chrome, and only chrome seeds the filter. Caught by the integration suite,
  which is the only suite that runs against a Settings build nobody wrote a
  fixture for.
