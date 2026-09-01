"""The entry point: give it a session and a goal, get an outcome.

Two things are injected rather than hardcoded, for different reasons.

The **provider** comes from `AgentSettings`, so the loop is not an Anthropic
agent that happens to be configurable. It builds through
`init_chat_model`, which means OpenAI, Gemini, Bedrock, a local Ollama model or
anything else LangChain integrates is a pair of environment variables rather
than a code change. Anthropic is the default because it is what this project's
numbers were measured on; the loop does not depend on it.

The **model callable** can be replaced outright, which is how the graph gets
driven by a scripted model in tests. The loop's mechanics deserve a
deterministic test, and one that needs an API key and a network is not.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Any

from langchain.messages import AIMessage, AnyMessage
from langgraph.types import Command

from ios_agent.backend import Backend, SessionBackend
from ios_agent.config import AgentSettings, export_provider_credentials
from ios_agent.graph import build_graph, opening_messages
from ios_agent.state import Outcome
from ios_agent.tools import Run, build_tools
from ios_mcp.session import IosSession

ModelFactory = Callable[[list[Any]], Callable[[list[AnyMessage]], Awaitable[AIMessage]]]

#: Asked when the agent wants to do something destructive. Receives the
#: interrupt payload (action, target signature, why it was flagged) and returns
#: whether to allow it.
#:
#: The default refuses. SAFETY.md: a client that cannot answer is treated as
#: refusal, because an unanswerable question is not consent. An agent left
#: running unattended must not be able to erase a phone because nobody was
#: there to say no.
Approver = Callable[[dict[str, Any]], Awaitable[bool]]


async def refuse_everything(_request: dict[str, Any]) -> bool:
    return False


def operator_prompt() -> str:
    """The system prompt, kept in a file so it diffs like the code does."""
    return (resources.files("ios_agent") / "prompts" / "operator.md").read_text()


def chat_model(settings: AgentSettings | None = None) -> ModelFactory:
    """Bind whichever provider is configured to a tool list.

    Everything provider-specific is decided in `AgentSettings.chat_kwargs`, so
    switching to OpenAI or a local Ollama model is `IOS_AGENT_PROVIDER` and
    `IOS_AGENT_MODEL`, not a code change. Anthropic is the default because it
    is what this project's numbers were measured on, not because the loop
    depends on it.
    """
    from langchain.chat_models import init_chat_model

    cfg = settings or AgentSettings()
    # The vendor SDK reads its key from the process environment, and
    # pydantic-settings only ever put `.env` into a settings object. Without
    # this, a key written beside the model it configures is invisible.
    export_provider_credentials()

    def factory(tools: list[Any]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        try:
            chat = init_chat_model(
                model=cfg.model, model_provider=cfg.provider, **cfg.chat_kwargs()
            )
        except ImportError as exc:
            # The default error names a pip package; this one names the extra
            # that installs it in this repository.
            raise ImportError(f"{exc}\n{cfg.missing_package_hint()}") from exc

        bound = chat.bind_tools(tools)

        async def call(messages: list[AnyMessage]) -> AIMessage:
            reply = await bound.ainvoke(messages)
            assert isinstance(reply, AIMessage)
            return reply

        return call

    return factory


async def _installed_apps(session: IosSession) -> list[str]:
    """The names of the apps on this device, for the opening turn.

    Best effort on purpose. A device that will not enumerate its apps is still
    a device the agent can drive, and failing a run over a nicety would be a
    worse trade than starting without the list.
    """
    try:
        apps = await session.lease.adapter.list_apps("all")
    except Exception:
        return []
    return sorted({a.name for a in apps if a.name})


async def run_goal(
    session: IosSession,
    goal: str,
    *,
    model: ModelFactory | None = None,
    backend: Backend | None = None,
    settings: AgentSettings | None = None,
    approve: Approver | None = None,
    max_steps: int | None = None,
) -> Outcome:
    """Drive one goal to a stopping point and report what it cost.

    A destructive action pauses the graph rather than being decided for the
    person whose phone it is. `approve` is asked and the graph resumes with the
    answer; without one, everything destructive is refused.
    """
    cfg = settings or AgentSettings()
    run = Run(backend=backend or SessionBackend(session), goal=goal)
    tools = build_tools(run)
    call_model = (model or chat_model(cfg))(tools)

    prompt_tokens = 0
    completion_tokens = 0

    async def metered(messages: list[AnyMessage]) -> AIMessage:
        nonlocal prompt_tokens, completion_tokens
        reply = await call_model(messages)
        usage = reply.usage_metadata
        if usage:
            prompt_tokens += usage.get("input_tokens", 0)
            completion_tokens += usage.get("output_tokens", 0)
        return reply

    graph = build_graph(run, metered, tools, max_steps=max_steps or cfg.max_steps)
    decide = approve or refuse_everything
    # One thread per run. The checkpointer keys on it, and reusing an id across
    # runs would resume someone else's conversation.
    config = {"configurable": {"thread_id": f"{id(run):x}"}}

    apps = await _installed_apps(session)
    step: Any = {"messages": opening_messages(operator_prompt(), goal, apps)}
    while True:
        result = await graph.ainvoke(step, config=config)
        pending = result.get("__interrupt__") if isinstance(result, dict) else None
        if not pending:
            break
        # Answer every pending question, then resume. Each is scoped to one
        # action, so approving one never approves another.
        answers = {item.id: await decide(dict(item.value)) for item in pending}
        run.approvals_asked += len(answers)
        step = Command(resume=answers if len(answers) > 1 else next(iter(answers.values())))

    return Outcome(
        goal=goal,
        succeeded=run.succeeded,
        summary=run.summary,
        stopped_because=None if run.finished else (run.summary or "the model stopped early"),
        steps=run.steps,
        approvals_asked=run.approvals_asked,
        stats=run.backend.stats,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
