# 1. The agent is a peer of the server, not a layer above it

Accepted, 2026-08-25.

## Context

CLAUDE.md planned the deep agent as `ios_mcp/agent/`, and the first draft of
the Phase 6 plan drew it as a seventh layer stacked on top of the six in
ARCHITECTURE.md. Both were wrong, for two separate reasons.

**The layer number does not match the import graph.** ARCHITECTURE.md draws
policy as layer 6, above the MCP server at 5 and the session at 4. That is a
presentation choice, not a dependency: `ios_mcp/policy/` is imported *by*
`ios_mcp/session.py:38-40` and by `ios_mcp/server/context.py`, and `PolicyGate`
is constructed inside `IosSession.__init__` at `session.py:70`. Nothing sits
above the gate. Drawing the agent at layer 7 implied the gate mediates between
the agent and the tools, when in fact an agent passes through the same gate on
the same code path the server does.

**A subpackage cannot hold the boundary that matters.** The value of layers 1
to 4 is that they are a plain async library with no MCP imports, so any
consumer can use them. An agent bound to LangGraph and to one model provider,
living inside the same distribution, undercuts that: `ios-mcp` would carry a
provider-shaped dependency even behind an optional extra, and "the agent must
not reach past the public API" would be a second honour-system rule policed by
review, exactly like the first one, which had no test.

## Decision

`ios_agent` is a separate distribution in a uv workspace, at `agent/`.
`ios-agent` depends on `ios-mcp`; nothing points the other way.
`tests/unit/test_layering.py` enforces both directions statically.

The agent is described as a second consumer of `IosSession`, a peer of the MCP
server. ARCHITECTURE.md's diagram already shows "MCP clients (Claude Code,
Claude Desktop, a future agent service or iOS app)" above the stack. The agent
belongs in that row.

## Alternatives rejected

**`ios_mcp/agent/` as layer 7**, which is what CLAUDE.md said. Simplest, no
build changes, and wrong on the import graph as above. The boundary would have
been a directory convention.

**A sibling top-level package in one wheel** (`ios_agent/` beside `ios_mcp/`,
agent deps in an `[agent]` extra). Most of the dependency benefit for almost
none of the plumbing, and an import test still works. Rejected because the two
still ship together: anyone installing the MCP server receives agent source
they did not ask for, so the separation stays a convention rather than a fact.

**A separate repository.** The strongest boundary and the most faithful
demonstration, but it splits the eval harness across repos, and the measured
argument the eval harness carries is the deliverable of this phase.

## Consequences

- `pip install ios-mcp` never sees LangGraph. Verified against the built
  wheel: it contains only `ios_mcp/` and declares `anyio`, `fastmcp`, `httpx`,
  `pydantic-settings`, `pydantic`, plus `pillow` under the `vision` extra.
- The public surface is now written down, as `_PUBLIC_SURFACE` in
  `tests/unit/test_layering.py`: `session`, `config`, `errors`,
  `actions.result`, `perception.digest`, `devices.base`, `devices.pool`.
  Widening it is a deliberate act that shows up in a diff.
- The long-documented "layers 1 to 4 must not import MCP" invariant is
  enforced for the first time. It had never had a test.
- The MCP-backed eval variant must reach the server as a client over a
  transport rather than importing `ios_mcp.server` in-process. That is the
  honest version of the claim anyway: importing the server would prove nothing
  about whether it works for anyone else.
- Two `pyproject.toml` files, one `uv.lock`, and `mypy` now runs over both
  packages. `uv sync` installs both, because the eval suite drives both.
