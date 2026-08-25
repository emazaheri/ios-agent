# ios-agent

A goal-directed agent that drives an iPhone by consuming the `ios-mcp`
library. It is a peer of the MCP server, not a layer above it: both are
consumers of the same `IosSession`.

Kept as a separate distribution so `ios-mcp` never acquires a dependency on
LangGraph or on a model provider. `tests/unit/test_layering.py` asserts the
dependency only ever points one way.

See `docs/adr/0001-the-agent-is-a-peer-of-the-server.md`.
