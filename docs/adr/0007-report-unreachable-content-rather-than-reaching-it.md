# 7. Report unreachable content rather than reaching it

Accepted, 2026-08-26.

## Context

`.claude/research/capturing-the-tree-across-apps.md` set out to answer an
efficiency question: a snapshot is the most expensive thing the agent does, so
where does the time go? The measurements closed the question rather than
answering it.

    excluded set : 750 ms      excluded off : 743 ms
    depth 30 : 740 ms          depth 100 : 756 ms
    json : 757 ms / 66 KB      description : 531 ms / 16 KB

The documented Appium optimisation is a no-op on `format=json`. Depth bottoms
out around 20 and everything past 30 is free. The one cheaper format costs a
parser and the typed attributes the digest is built on, to save 226 ms on a
simulator, where a device's round trip dominates anyway.

So the remaining snapshot cost is XCTest's, not ours. And the agent already
spends **exactly one observation per run**, the hand-written oracle's floor,
because every action folds its resulting screen into its own response. There is
nothing left to win by taking fewer snapshots either.

**The binding constraint is not speed. It is completeness.**

## The decision

Incompleteness has two causes and they need different answers.

Content that **is** in the tree and our rules discard is a bug, and those are
fixed: the role-based shortcuts that assumed Apple built the app now key off
what a node carries rather than what it is.

Content that is **not in the tree at all** cannot be fixed here at any price:

- A Flutter app presents as a single view with no accessible children. There is
  no tree to capture.
- Web content is a second tree behind an explicit context switch this stack has
  no concept of, and `SafariViewController` is documented as unreachable even
  with one. Many third-party apps put login, payment and settings there.
- A control rendered rather than composed has nothing to match on, which is
  also why the policy gate cannot see it.

For these, **the digest says so and names the tool that can see them.** It does
not try to reach them.

## Why this rather than the alternatives

**Rather than silence.** This is the option being replaced, and it is the worst
of the three. A WebView screen returns a nav bar and a scroll container: four
plausible elements, indistinguishable from a screen that genuinely has four
things on it. The agent has no way to tell "I have read this screen" from "this
screen cannot be read", so it taps at nothing and reports the app is broken.
That is what happened on the first third-party run.

**Rather than solving it.** A WebView bridge through the remote debugger is a
project, not a fix, and it buys nothing on Flutter. Vision buys both, and the
server already has `ios_screenshot` with ref annotation for exactly this. What
is deliberately *not* decided here is giving the deep agent that tool: ADR 0004
rejected subagents partly because the agent had never taken a screenshot, and
the honest way to reopen that is an eval task that fails without one, not an
argument. Detection is the cheap half, and it is also the half that tells you
whether the expensive half is ever needed.

**Rather than a heuristic that fires often.** Both detectors are deliberately
narrow: a WebView that contributed no readable descendants, and a single view
covering the screen with nothing beneath it. They are asserted silent on every
screen this project already reads, including the third-party fixtures. A
warning that cries wolf is worse than no warning, because the agent learns to
ignore it.

## Consequences

The digest carries `notes`, rendered into the text the model reads rather than
only into the payload, so it costs a line and reaches both the MCP path and the
agent's backend.

No new tool. The MCP surface is capped at 32 by
`tests/unit/test_server.py`, stands at 30, and the tool these notes point at
already exists.

If a note ever fires on a screen the agent could in fact read, the detector is
wrong and should be narrowed or deleted, not tuned. The whole point is that it
means something.
