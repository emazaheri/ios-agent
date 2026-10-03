# 17. No simulator-native tree source

Accepted, 2026-10-02.

## Context

ADR 0016 rejected the Accessibility Inspector as a device tree source. The
simulator offered a different route, the one Meta's idb takes. CoreSimulator
can read a simulated app's whole accessibility tree, with frames, through the
private `AccessibilityPlatformTranslation` framework, and inject touches and
buttons through `SimulatorKit`, with no XCTest involved.

Two gains were claimed:

- **Cheaper reads.** Every action settles by polling the tree at least twice
  (`stabilize`, `stable_samples=2`), so reads are most of an action's wall
  time on a simulator.
- **A simulator with no WebDriverAgent,** and so no WDA build and no
  `xcodebuild test-without-building` launch.

The gate, set before any code: it must work on the current Xcode without
patching the tool, return frames in points, read in at most half of WDA's
time on the same screens, and reach 95% recall against the WDA digest.

The tool was AXe 1.8.0, an MIT-licensed CLI built on idb's FBSimulatorControl
frameworks, run against an iPhone 17 simulator on iOS 27.0 with Xcode 27.0.

## What was measured

Median of five reads per screen, both readers on the same screen in the same
session:

| screen | WDA `source()` | AXe `describe-ui` | AXe saving |
|---|---|---|---|
| Settings root | 1.07s | 0.82s | 23% |
| General > About | 0.81s | 0.73s | 10% |

**The saving is not the gap the design assumed.** AXe's process start is
0.02s, so nearly all of its time is the read itself. Two different readers,
one through XCTest and one through CoreSimulator, land within a quarter of
each other, because both wait on the same thing: the app serialising its
accessibility tree. ADR 0007 called the snapshot cost XCTest's floor. It is
lower than that: it is the floor of accessibility itself, and changing the
reader does not move it.

A read also returns more to parse. After a reboot, Settings root came back as
306 KB of JSON in 1.02s, against about 66 KB from WDA for the same screen.

A single-point query (`describe-ui --point`) took 0.30s. That is the only
read that was materially cheaper, and it answers a different question.

## Reads go through XCTest anyway on Xcode 27

AXe's framework loader carries a separate path for "the Xcode frameworks
needed to bootstrap simulator Accessibility on Xcode 27+". It starts a
short-lived `XCUIDeviceRemoteAutomationSession`, which is XCTest machinery, to
get Accessibility loaded in the simulator. So the "no XCTest" premise no
longer holds on the current Xcode.

On a freshly booted simulator that bootstrap timed out ("Timed out creating
the simulator remote automation session", 5.5s) on the first two reads, after
both of two boots. Once a WDA session or an AXe tap had run, reads worked for
the rest of the boot. A WDA-free simulator would depend on a bootstrap that
fails on first use for reasons not established here.

## What did work

Input. `axe button home` took 0.20s and the simulator went home, with no
XCTest anywhere. A cold `axe tap` took 1.46s. HID injection through
`SimulatorKit` survived the Xcode 27 framework move that broke the read side.
It is not adopted on its own, because a WDA session is still needed to read
the screen, and once one is running it taps as well.

## Decision

No simulator-native tree source, and no WDA-free simulator. The `TreeSource`
seam and the AXe-backed reader are not built.

It fails the gate's speed criterion by a wide margin: 10 to 23% against 50%.
Recall was not measured, because it could not change the outcome.

## Alternatives rejected

**Adopt it for a 10 to 23% read saving.** It would be a second perception
path on private frameworks that Apple has already moved once, with a
bootstrap that fails on first use, a payload four to five times larger, and
role names the digest would need a second mapping for. That buys less than a
quarter of a second per read.

**Use idb instead.** Its warm daemon would remove process start, and process
start is 0.02s. It reads through the same framework, so it meets the same
floor.

**Point queries for settle polling.** At 0.30s, a `--point` read is cheap
enough to poll with, but a settle check that looks at one point cannot see
the rest of the screen change. Detecting a settled screen from device signals
(frame commits, screenshots) addresses the same cost without that blind spot,
and it remains open.

## Consequences

- No package code was written.
- The read floor is now measured from two directions. The Accessibility
  Inspector (ADR 0016) and CoreSimulator both fail to get under it. The
  remaining lever on read cost is **taking fewer reads**, not cheaper ones.
- **Found in passing:** the WDA port check binds IPv4 `127.0.0.1`, so a
  process listening on IPv6 `*:8100`, such as a Docker container, passes it.
  WDA then fails to bind, and the readiness probe reaches the other server
  until the startup timeout. That is a separate fix.
- **What would reopen it:** a reader that returns the tree materially faster
  than the app can be asked for it, such as an incremental or diff-based
  accessibility API, or an Xcode release that removes the XCTest bootstrap
  and makes simulator Accessibility reliable from boot. The second would
  revive the WDA-free simulator on its own merits, as a setup simplification
  rather than a speed one.
