# 19. Settle on frames, as an off-by-default option

Accepted, 2026-10-03.

## Context

Every action waits for the screen it produced before reading it back. The
wait polled the accessibility tree until its fingerprint repeated twice, so
a settle cost at least two tree reads and a poll interval, and a tree read is
the expensive call in this system.

The proposal was to settle on a cheaper signal from the device and read the
tree once. Before building anything, the settle loop was instrumented to find
out what it spends and how much any signal could save: everything after the
tree read that first showed the final screen, which is the poll sleep and the
read that only proves nothing moved.

The gate, set before any code: the possible saving had to exceed 300 ms per
settle on a phone, and a signal had to return early no more often than the
tree loop does, judged by the same oracle.

## What was measured

The golden flows, 3 runs per arm, on an iPhone 17 Pro Max (iOS 26.6.1) and an
iPhone 17 simulator (iOS 27.0), through `scripts/settle_baseline.py`. On the
phone the two flows that change state were skipped.

What one read costs, on Settings root:

| | tree read | screenshot |
|---|---|---|
| simulator | 1.00s | 0.10s |
| phone, USB | 1.28s | 0.15s |
| phone, Wi-Fi | 1.61s | 0.17s |

The confirming read was 43% of settle time on the simulator and 50% on the
phone: 1.2s and 2.0s per settle, well past the gate.

**Tuning was tried first and is not the lever.** No initial delay and a 50 ms
poll changed the phone by 2% and made the simulator slower, because tree reads
issued back to back slowed each other (0.98s to 1.49s each).

**Frames.** Take screenshots until the screen stops changing, then read the
tree once. An untimed oracle checked each settle: a full tree settle run
straight afterwards, compared by fingerprint. It ran on the tree loop as well,
so both arms faced the same standard.

| arm, phone | route | settle total | median | returned early |
|---|---|---|---|---|
| tree loop | USB | 363s | 3.59s | 2 of 97 |
| frames, 2 identical in a row | USB | 230s | 2.29s | 1 of 101 |
| frames, 0.6s with no novel frame | USB | **300s** | **2.67s** | **0 of 102** |
| tree loop | Wi-Fi | 422s | 3.98s | 1 of 101 |
| frames, 0.6s with no novel frame | Wi-Fi | **340s** | **3.36s** | **0 of 102** |
| the same, as shipped | Wi-Fi | **342s** | **3.37s** | **1 of 100** |

That is 17 to 22% off a phone's settle time, with early returns at the tree
loop's own rate: 1 in 304 against 3 in 198. Every early return on either side
was a scroll or a deep link.

On the simulator the same rule returned early 0 times in 93, against 3 for the
naive rule. Simulator timings are not evidence: the unchanged tree loop
totalled 239s in one arm and 407s in another.

## Why "no novel frame" and not "identical frames"

The naive rule failed on Settings search, three runs out of three on the
simulator. A probe logged every frame after typing: the screen held still for
0.42s while the search waited, then animated its results in, then the caret
blinked through the same two or three frames indefinitely. A window shorter
than the pause took the pause for the end. A window longer than the pause
never went quiet, because the caret never stops.

Counting only frames not already seen in this settle answers both. The caret
revisits old frames, so it does not reset the clock, and the window can be
long enough to outlast the pause.

## Decision

- `stabilize.signal = "frames"` settles on WebDriverAgent screenshots, then
  reads the tree once, after `quiet_s` (0.6s) passes with no novel frame.
- Anything the frames cannot answer falls through to the tree loop: frames
  that never go quiet within `frame_timeout_s`, a screenshot that fails, and a
  single read that still matches the pre-action baseline. The baseline does
  not cover scrolls, which pass none, and that is where the naive rule's one
  early return on the phone came from: two still frames before the swipe had
  rendered at all. The 0.6s window is what removed it.
- **Off by default.** Every screen measured is Settings. The default is the
  tree loop until the same comparison holds in an app Apple did not write.
- The signal lives in `IosSession`, not behind a device adapter: a screenshot
  is a WebDriverAgent call that every route already has, and no DTX
  instruments service was needed. That service needs a tunnel, and ADR 0018
  found no no-root tunnel that reaches a phone on Wi-Fi.

## Consequences

- No change to what the agent sees: the read that is returned is still one
  full tree read, through `snapshot`, so the ref table invariant holds.
- A settle now costs a few screenshots plus one tree read, against two or
  more tree reads. The saving grows with the cost of a tree read, so it is
  largest on exactly the routes that were slowest.
- 0.6s is fitted to one app's pause. A pause longer than the window would fool
  the frames, as one longer than a poll plus a read, about 2s on a phone,
  already fools the tree loop.
- Scrolls settle without a baseline, under either signal, and every early
  return the oracle found on the phone was a scroll or a deep link. Giving
  `scroll` a baseline would cover both signals and is a separate change.
- A still screen waiting on the network with no animation is untested, by
  either signal. None of the flows produce one.
- **What would make it the default:** the same oracle comparison, frames
  against tree, in a third-party app, with early returns no higher than the
  tree loop's.
- **What would reopen it:** a cheaper tree read. Against a 0.4s read the
  confirming read is worth less than the 0.6s quiet window, and the tree loop
  wins.
