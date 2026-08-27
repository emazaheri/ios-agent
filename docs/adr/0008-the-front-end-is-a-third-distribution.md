# 8. The front end is a third distribution

Accepted, 2026-08-26.

## Context

The agent had no product surface. `scripts/ask.py` acquired a device, called
`run_goal`, and printed a summary afterwards. It was not installed, it emitted
nothing while a run was happening, and it asked for approval with a blocking
`input()` inside the event loop while a WebDriverAgent session sat open.

That last part matters more than it sounds. On a physical device a run is
minutes of silence: `wda.startup_timeout_s` is raised to 300, a simulator boot
can take 180 seconds, and each snapshot costs about 3.7. A working run and a
hung one looked identical, and the numbers this project is judged on were only
visible once it was over.

Building a terminal front end raised a placement question with a concrete
answer rather than a matter of taste.

## Decision

`tui/ios_tui/`, a third distribution in the uv workspace, depending on
`ios-agent` and `ios-mcp`. Nothing points back.

The forcing constraint is `tests/unit/test_layering.py`. It holds
`agent/ios_agent/**` to a seven-module public surface of `ios_mcp`, and both
`ios_mcp.devices.discovery` and `ios_mcp.devices.doctor` are outside it. A
front end needs discovery to offer a device picker and the doctor to explain a
machine where nothing will run at all.

So the choice was: widen the surface to buy a front end a device list, or put
the front end somewhere the surface does not apply. Widening would loosen an
invariant for a reason unrelated to why it exists. The surface is scoped to the
agent so that *the agent* cannot quietly start reimplementing resolution or
talking to WebDriverAgent; a terminal app listing simulators is not that
failure, and should not be governed by the rule that prevents it.

Living outside `ios_agent` also keeps `textual` out of the agent's dependency
graph, where the eval install would otherwise drag it in for nothing.

## Two boundaries this created, both enforced

The layering test gained two checks in the same static-AST style as the others:

- **Nothing beneath the front end imports it.** `ios-mcp` must work for any
  agent and `ios-agent` must work for any front end. One import pointing back
  turns a terminal app from one consumer among several into part of the stack.
- **The event path never imports Textual.** `events`, `bus`, `stream`,
  `progress`, `runner` and `printer` are a plain async seam; `app`, `widgets`
  and `approval` are the Textual half. That split is what lets cost, screen
  changes and approval questions all be asserted against a plain list with no
  canvas, and it is why `--no-tui` is a second consumer of one set of events
  rather than a second implementation.

## What holds it accountable

Every other addition to this project justified itself with a measurement.
Three of the deep agent's four pillars were rejected on their own numbers. A
terminal app cannot be justified that way, which makes it the most exposed to
the feature-count trap CLAUDE.md warns about.

So it gets the one claim it can be held to: **watching a run must not change
it.** `EventBackend` wraps a `Backend`, delegates `stats` and `last_screen`
rather than mirroring them, and emits synchronously so it introduces no
scheduling point the bare backend did not have. `tests/tui/test_cost.py` runs
the same scripted task wrapped and unwrapped and asserts all four counters and
the whole audit trail equal, by equality, in the spirit of the agent eval
oracles.

## What building it found

Two things that were not visible from reading the code.

**A graceful stop already existed.** `IosSession.halt()` is public and
synchronous, `SessionBackend.stop_reason()` reads it, and `graph.py:next_step`
checks it after every node. So Escape ends a run with a complete `Outcome` and
real numbers, and cancellation is only the second press. Nothing had to be
built for it; it had to be noticed.

**`last_screen` is not refreshed by every action.** `SessionBackend._render`
replaces it only when an action returns a full digest, and an action whose new
screen is similar returns a *delta* instead. That is deliberate and is what
keeps long flows cheap: the model reads the delta against the screen it already
has. A pane cannot, so a front end showing `last_screen` displays a screen the
phone has already left. `ActionFinished` carries `screen_refreshed` for exactly
this, and the pane says how far behind it is rather than looking current.

## What would reopen this

Two things.

If `ios_mcp.devices.discovery` and `ios_mcp.devices.doctor` ever become part of
the agent's own surface for a reason of the agent's own, the constraint that
forced a third distribution is gone and the front end could fold into
`agent/ios_agent/tui/`.

If a second front end appears (the iOS client ARCHITECTURE.md keeps as a
possibility), the shared piece is the event seam, not the Textual half, and it
would be worth asking whether `events`, `bus`, `stream` and `runner` belong in
their own distribution with the terminal app on top.
