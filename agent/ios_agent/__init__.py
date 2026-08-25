"""A goal-directed agent that drives an iPhone through the ios-mcp library.

Deliberately a separate distribution rather than a subpackage of `ios_mcp`.
The agent is not a layer above the stack; it is a second consumer of
`IosSession`, a peer of the MCP server. The policy gate is constructed inside
`IosSession` itself, so an agent does not sit above it any more than the
server does: both pass through the same gate on the same path.

What that buys, beyond tidiness:

- `ios-mcp` stays provider-neutral. Its pitch is that it works for any agent,
  which a bundled LangGraph-and-Anthropic agent would quietly undercut.
- The dependency direction becomes a fact rather than a convention. Across a
  distribution boundary this package can only reach the public API, and
  `tests/unit/test_layering.py` asserts exactly which modules that is.
- It turns a claim into a demonstration. ARCHITECTURE.md asserts that layers 1
  to 4 are a plain async library an agent framework can import directly. An
  external consumer proves it; an internal sibling directory only asserts it.

Nothing is exported yet. The measurement came first, in `tests/evals/agent`.
"""

from __future__ import annotations

__all__: list[str] = []
