# 16. No Accessibility Inspector tree source

Accepted, 2026-10-02.

## Context

Xcode's Accessibility Inspector reads a device's accessibility tree through
`com.apple.accessibility.axAuditDaemon.remoteserver`, a DTX service like the
testmanagerd one go-ios already speaks. It needs Developer Mode and a mounted
developer disk image, and **no signed WebDriverAgent**. Two claims rested on
that:

- a read cheaper than WDA's 3.7s device snapshot, and
- a read-only, no-signing mode, without the free account's 7-day profile.

The plan held both to a gate set before any code: rects present on
actionable elements, a full-screen read in half of WDA's time (1.85s), and
95% recall against the WDA digest. Package code would only follow if all
three passed.

ADR 0007 had already argued that snapshot cost is XCTest's floor, and that
the binding constraint is completeness rather than speed. This was the chance
to find out whether a different floor existed.

## What was measured

An iPhone 17 Pro Max on iOS 26.6.1, over Wi-Fi, with pymobiledevice3 11.20.2.
A throwaway script walked the foreground screen and recorded every element's
raw fields and timestamps.

| screen | elements | walk | connect | total | per element |
|---|---|---|---|---|---|
| home screen | 29 | 2.32s | 0.35s | **2.66s** | 80 ms |
| Settings root, run A | 53 (36 unique) | 4.01s | 0.35s | **4.36s** | 76 ms |
| Settings root, run B | 36 | 2.84s | 0.42s | **3.26s** | 79 ms |
| Settings root, run C | 37 (36 unique) | 2.93s | 0.34s | **3.27s** | 79 ms |
| Settings > Apps | 132 (131 unique) | 7.84s | 0.36s | **8.20s** | 59 ms |

**No screen met the speed bar.** The service reads a screen by moving
inspector focus one element at a time and waiting for the event that reports
it, so cost is linear in elements by construction. At roughly 60 to 80 ms
each, a screen of 18 elements misses the 1.85s target and a screen of 42 is
slower than WDA. Settings root, the screen most of the golden flows start on,
came in 12% faster than WDA at best and 18% slower at worst.

The Apps list number is not a like-for-like comparison, and the difference
counts against the service: WDA reads the cells a table has materialised,
while this walk went through all 131 rows.

## What an element carries

- **One merged string per element.** The caption joins label, value, traits
  and hint ("YouTube, 204 new items, Updates Frequently, Double tap to
  open"). There is a spoken description and an opaque element handle. **No
  frame and no hierarchy** come back in the walk.
- **Fields cost a round trip each.** The inspector's sections name Label,
  Value, Traits, Identifier, Hint, class and hierarchy attributes, and no
  frame attribute. Reading any of them separately means a
  `deviceElement:valueForAttribute:` call per attribute per element. Two
  encodings of that call failed: one timed out, and the other returned `None`
  for every attribute, Label included. That is unresolved, and even working
  it adds about 25 ms per field per element.

Without rects, the digest's geometry, the visibility rules in
`docs/realities/perception-geometry.md` and every coordinate a tap resolves
to have nothing to work from. The gate's first criterion fails on its own.

## What it does to the device

- **It scrolls the screen.** Confirmed by eye on Settings: walking a list
  moves it to reach rows below the fold. A read that changes what is shown
  breaks the contract every observation here keeps, and it would hand the
  agent a digest of a screen that has since moved.
- **It cannot act on most apps.** `perform_press` found "Apps, Button" in
  Settings and pressed it, and the next walk still read the Settings root.
  Its implementation documents why: it needs `task_for_pid-allow`, which only
  the developer's own debug builds carry. Taps would still need WDA.
- **The walk does not reliably know when it has finished.** It starts on a
  different element each run (three runs, three starting points) and stops
  when it revisits one. In run A it wrapped past the end and re-read 17
  elements, nearly half the screen again. Captions are not unique, so a
  product could not dedupe on them.
- **The tunnel drops when the phone sleeps.** Seen twice. The no-root
  `start-tunnel --native` mode works, but a session would have to detect the
  drop and rebuild.

## Decision

No Accessibility Inspector tree source. Phase 1 of the plan, a `TreeSource`
seam in `IosSession` with a pymobiledevice3-backed implementation, is not
built.

The no-signing, read-only mode goes with it. What it would deliver is a list
of captions with no geometry, read slower than WDA on most screens, scrolling
the screen as it reads, with no way to act. That is not a degraded mode of
this tool. It is a different and weaker one.

## Alternatives rejected

**Keep it as an opt-in read-only fallback.** It would be an unmaintained
second perception path whose output the digest cannot use as it stands, and
ADR 0003's rule applies: a mode nobody runs is not a mode.

**Fix the attribute encoding first, then decide.** Working attribute reads
would add fields, not speed: each one is another round trip on top of a walk
that already misses the bar. Only rects would change the first criterion, and
the second and the scrolling would still fail.

**Import pymobiledevice3 rather than shell out.** Moot now, and worth
recording: the package is GPL-3.0-or-later and this one is MIT, so any future
use goes through a subprocess.

## Consequences

- No package code was written.
- This was the highest-ranked of the low-level levers considered. Settle
  detection from device signals is untouched by this result and still
  attacks the same 3.7s, from the other side: fewer snapshots rather than
  cheaper ones.
- **What would reopen it:** a bulk read. The service exposes
  `deviceInspectorAutodrillIntoElements:` and an `_AXHierarchyElementsAttribute`,
  and if either returns a subtree with frames in one round trip, the linear
  walk is not the floor and the speed criterion needs measuring again. So
  would an iOS release that puts a frame in the focus payload.
