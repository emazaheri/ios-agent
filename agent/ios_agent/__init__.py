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

The loop is deliberately the stupidest thing that can finish a task: one model
node, one tool node, an edge back. No plan, no verification step, no memory, no
subagents. Each of those is a later slice that has to justify itself against
this baseline's numbers.

The provider is configuration, not a dependency. The loop builds its model
through `init_chat_model`, so OpenAI, Gemini, Bedrock or a local Ollama model
is `IOS_AGENT_PROVIDER` and `IOS_AGENT_MODEL` rather than a code change.
Anthropic is the default because it is what this project's numbers were
measured on; each integration package is a separate extra.
"""

from __future__ import annotations

from ios_agent.backend import Backend, BackendStats, SessionBackend
from ios_agent.config import (
    KNOWN_EXTRAS,
    AgentSettings,
    ProviderProbe,
    export_provider_credentials,
    probe_provider,
)
from ios_agent.loop import Approver, chat_model, operator_prompt, refuse_everything, run_goal
from ios_agent.mcp_backend import McpBackend, McpClient
from ios_agent.state import AgentState, Outcome
from ios_agent.verify import Judgement, Verdict, Verifier

__all__ = [
    "KNOWN_EXTRAS",
    "AgentSettings",
    "AgentState",
    "Approver",
    "Backend",
    "BackendStats",
    "Judgement",
    "McpBackend",
    "McpClient",
    "Outcome",
    "ProviderProbe",
    "SessionBackend",
    "Verdict",
    "Verifier",
    "chat_model",
    "export_provider_credentials",
    "operator_prompt",
    "probe_provider",
    "refuse_everything",
    "run_goal",
]
