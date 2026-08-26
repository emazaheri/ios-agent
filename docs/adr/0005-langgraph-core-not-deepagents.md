# 5. LangGraph core, not the deepagents harness

Accepted, 2026-08-25.

## Context

The agent was going to be built in LangChain's "deep agent" shape, and
`deepagents` packages exactly that: `create_deep_agent()` with
`FilesystemMiddleware`, `TodoListMiddleware`, `SubAgentMiddleware` and system
prompts, on the LangGraph runtime.

Adopting it would have been an afternoon's work. CLAUDE.md's rule for this
phase is why it was not:

> Wiring up `deepagents` would be a tutorial, not a showcase. Anyone can follow
> the quickstart. Adopt a pillar because a measured constraint demands it,
> never because the framework offers it.

## Decision

Depend on `langgraph` and `langchain` only. Build the loop directly, and adopt
a middleware only where a measurement shows it winning.

## Why

The harness charges its overhead per turn, on top of a screen that already
costs about 300 tokens and 3.7 seconds on real hardware. That is the wrong
direction for the metric this project is judged on, and LangChain's own
positioning says as much: use `create_agent` for a lighter harness, and drop to
LangGraph when the loop is not the right shape.

The decisive argument turned out to be different, and only visible afterwards.
Of the four pillars `deepagents` provides by default, **three were measured and
rejected**:

| pillar | what the harness gives | measured outcome |
|---|---|---|
| Task planning | `TodoListMiddleware`, a `write_todos` tool | rejected, ADR 0002 |
| Subagents | `SubAgentMiddleware`, a `task` tool | rejected, ADR 0004 |
| Filesystem memory | `FilesystemMiddleware` | rejected, ADR 0003 |
| System prompts | a prompt parameter | kept, and one file |

Verification, the one thing that was kept and the only mechanism that moved a
number, is **not** one of the four. It came from reading the eval traces and
noticing the agent retrying an action the device was ignoring.

So adopting the harness would have installed three mechanisms this project can
now show are useless here, while not providing the one that helped.

## Alternatives rejected

**`deepagents` as the base.** Fastest to a working demo. It would have made the
central claim of this phase unavailable: with the pillars arriving switched on,
there would have been no baseline to measure them against and no way to tell
which of them was doing anything.

**Both, measured head to head.** Considered at planning time and worth it if
the pillars had survived. Once three of four were rejected on their own
numbers, benchmarking a harness whose main features are those three had nothing
left to answer.

## Consequences

- Two dependencies rather than a harness, and each pillar exists only if a
  number justified it.
- The loop is about 120 lines across `graph.py` and `loop.py`, which is small
  enough to read in one sitting and argue with.
- If a later task set needs planning or subagents, `deepagents` remains the
  sensible way to get them, and ADRs 0002 and 0004 record the conditions that
  would reopen each.
- The system-prompt "pillar" is a markdown file loaded from the package. It did
  not need a framework.
