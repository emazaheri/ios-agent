# 6. The agent calls `IosSession` directly

Accepted, 2026-08-25.

## Context

CLAUDE.md carried this as an open decision from before any agent code existed:
does the agent go through MCP, or call `IosSession`?

- **Direct** is faster, and is exactly what the rule keeping layers 1 to 4 free
  of MCP imports was preserved for.
- **Through MCP** proves the server works for *any* agent, which is arguably
  the better thing to demonstrate.

## Decision

The loop calls `IosSession` in-process. MCP stays the interface for external
clients: Claude Code, Claude Desktop, anything else.

Going direct does not bypass any safety. `PolicyGate` is constructed inside
`IosSession.__init__`, not in the server, so both consumers pass through the
same gate on the same code path. What differs is only how the human is asked:
the server injects an elicitation handler, while the agent leaves
`on_approval` unset so an action raises `ActionRequiresApproval` carrying a
signature, which is the interrupt-and-resume path the session was built for.

A unit test asserts this by forcing a destructive tap on the direct path and
checking the gate refuses it.

## Why not through MCP for the loop

Serialisation and a transport round-trip per action, to reach a library that is
already in the same process, in exchange for nothing the direct path does not
have. The no-MCP-imports rule in layers 1 to 4 exists precisely so this choice
is available, and routing through MCP anyway would make that rule decorative.

## The MCP-backed variant, built and measured

`McpBackend` exists, behind the same `Backend` protocol, and it connects as a
genuine `fastmcp.Client` rather than importing the server. The layering test
forbids `ios_agent` from importing `ios_mcp.server`, and that constraint is the
point: an in-process import would exercise a path no real client has and prove
nothing about whether anyone else can drive the server.

The same route, one observation and three actions, median of five runs on the
scripted device:

| | latency | device tokens |
|---|---|---|
| direct | 6.6 ms | 573 |
| over MCP | 12.8 ms | 573 |

**Protocol overhead is +1.6 ms per call and zero tokens.** Against a real
device snapshot of roughly 3,700 ms that is 0.04% per call, which is why the
direct path is preferred for latency reasons that turn out to be almost
irrelevant, and why the architectural reason is the one that actually decides
it.

Token parity is not a coincidence and is asserted as an equality rather than an
approximation: the server serialises exactly the dict `ActionResult.to_dict()`
produces. If a payload ever gains or loses a field in transit, the two sets of
numbers this project reports stop being comparable, and that should fail
loudly rather than drift.

Two capabilities are deliberately absent over MCP rather than faked. Halting
and loop detection live on the session object, and polling them would cost a
round trip per turn purely to read a flag, distorting the very latency this
backend exists to measure; the agent's step budget still bounds a runaway loop.
Approval arrives as an `action_requires_approval` error carrying a signature,
which is the documented human-in-the-loop path for a client that cannot elicit,
and the tool layer's `interrupt()` handles it identically on both backends.

## Consequences

- No protocol overhead in the agent loop, and no serialisation of a digest that
  is about to be rendered to text anyway.
- The MCP surface stays honest by being the only way external clients reach the
  device, rather than by being the only way anything reaches it.
- `IosSession` now has two independent consumers, which is the practical test
  of whether it was ever really a library. It was: no change to layers 1 to 4
  was needed to add the agent.
