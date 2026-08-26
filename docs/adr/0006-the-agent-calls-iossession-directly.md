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

## The MCP-backed variant, and its honest status

The plan committed to an `McpBackend` behind the same `Backend` protocol, so
the eval suite could run the same tasks both ways and publish the difference.
The `Backend` protocol exists and `SessionBackend` implements it.
**`McpBackend` was not built.**

That is a gap, recorded rather than glossed. It was deprioritised because the
architectural claim it would demonstrate is already enforced statically by
`tests/unit/test_layering.py`, and because the number it would produce is
predictable: a local transport round-trip against actions that cost 8 to 12
seconds on a real device.

One constraint is worth writing down before anyone builds it. The layering test
forbids `ios_agent` from importing `ios_mcp.server`, so `McpBackend` must
connect as a genuine client over a transport. An in-process import would be
easier and would prove nothing about whether the server works for anyone else,
which is the entire point of building it.

## Consequences

- No protocol overhead in the agent loop, and no serialisation of a digest that
  is about to be rendered to text anyway.
- The MCP surface stays honest by being the only way external clients reach the
  device, rather than by being the only way anything reaches it.
- `IosSession` now has two independent consumers, which is the practical test
  of whether it was ever really a library. It was: no change to layers 1 to 4
  was needed to add the agent.
