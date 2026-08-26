# Decision records

Why things are the way they are, and what would change them back. Written when
a decision was actually made, so the numbering is chronological rather than
grouped.

| # | Decision | Settled by |
|---|---|---|
| [0001](0001-the-agent-is-a-peer-of-the-server.md) | The agent is a peer of the server, not a layer above it | reading the import graph |
| [0002](0002-no-explicit-planner.md) | No explicit planner | 1.05x an oracle on long tasks, with no planner |
| [0003](0003-no-cross-session-memory.md) | No cross-session memory | hedged framing measured worse than none; assertive stopped the agent checking |
| [0004](0004-no-subagents.md) | No subagents | 5,864 tokens/run against a 1M window |
| [0005](0005-langgraph-core-not-deepagents.md) | LangGraph core, not the deepagents harness | three of its four pillars were later rejected on their own numbers |
| [0006](0006-the-agent-calls-iossession-directly.md) | The agent calls `IosSession` directly | the no-MCP-imports rule existed for this |

Four of the six are refusals. That is the point rather than an accident: the
eval harness was built before the agent so it could overturn the design, and it
did, on the first run and repeatedly afterwards.

Each record states what would reopen it. Most of them come down to a harder
task set: longer horizons, an unfamiliar third-party app, or a screen the agent
cannot read without vision.
