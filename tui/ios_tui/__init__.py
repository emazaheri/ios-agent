"""A terminal front end for the agent.

The agent had a demo script and no product surface: `run_goal` returned an
outcome and printed nothing on the way there, which on a phone is minutes of
silence in which a working run and a hung one look identical.

Two things shape this package.

**It is a third distribution, not a subpackage.** `tests/unit/test_layering.py`
restricts `ios_agent` to a seven-module public surface of `ios_mcp`, and a
front end needs two modules outside it: device discovery, to offer a picker,
and the doctor, to explain a machine that is not set up. Widening that surface
to buy a front end a device list would loosen an invariant for a reason
unrelated to why it exists. Living outside `ios_agent` keeps it untouched.
See `docs/adr/0008-the-front-end-is-a-third-distribution.md`.

**The event path never imports Textual.** `events`, `bus`, `stream`,
`progress`, `runner` and `printer` are a plain async seam that any consumer
could drive; `app`, `widgets` and `approval` are the Textual one. That split is
what makes the interesting half testable without a terminal, and a layering
test enforces it rather than trusting it.

The rule this package is held to: **a front end may not change what a run
costs.** `EventBackend` wraps a `Backend` and emits around it without adding an
await or touching the device, and `tests/tui/test_cost.py` asserts the counters
by equality against an unwrapped run.
"""

from __future__ import annotations

__all__: list[str] = []
