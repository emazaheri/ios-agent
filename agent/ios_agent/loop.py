"""The entry point: give it a session and a goal, get an outcome.

The model is injected rather than constructed here so the graph can be driven
by a scripted model in tests. The loop's mechanics are worth testing on their
own, and they should not need an API key or a network to be trustworthy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import resources
from typing import Any

from langchain.messages import AIMessage, AnyMessage

from ios_agent.backend import Backend, SessionBackend
from ios_agent.graph import DEFAULT_MAX_STEPS, build_graph, opening_messages
from ios_agent.state import Outcome
from ios_agent.tools import Run, build_tools
from ios_mcp.session import IosSession

#: Claude Opus 5. Thinking is on by default on this model and is left on
#: deliberately: with thinking disabled it occasionally writes a tool call into
#: its visible text instead of emitting a tool-use block, and the call then
#: silently never runs. Cost is controlled with effort instead.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"
#: Thinking and the reply share this budget, so it has to leave room for both.
DEFAULT_MAX_TOKENS = 16000

ModelFactory = Callable[[list[Any]], Callable[[list[AnyMessage]], Awaitable[AIMessage]]]


def operator_prompt() -> str:
    """The system prompt, kept in a file so it diffs like the code does."""
    return (resources.files("ios_agent") / "prompts" / "operator.md").read_text()


def anthropic_model(
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ModelFactory:
    """Bind Claude to a tool list.

    `temperature` is never set: it is rejected outright on Claude Opus 5, so
    run-to-run variance is controlled with a pinned effort level and repeated
    runs rather than with sampling.
    """
    from langchain_anthropic import ChatAnthropic

    def factory(tools: list[Any]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        chat = ChatAnthropic(
            model_name=model,
            max_tokens_to_sample=max_tokens,
            output_config={"effort": effort},
            timeout=None,
            stop=None,
        ).bind_tools(tools)

        async def call(messages: list[AnyMessage]) -> AIMessage:
            reply = await chat.ainvoke(messages)
            assert isinstance(reply, AIMessage)
            return reply

        return call

    return factory


async def run_goal(
    session: IosSession,
    goal: str,
    *,
    model: ModelFactory | None = None,
    backend: Backend | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> Outcome:
    """Drive one goal to a stopping point and report what it cost."""
    run = Run(backend=backend or SessionBackend(session), goal=goal)
    tools = build_tools(run)
    call_model = (model or anthropic_model())(tools)

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

    graph = build_graph(run, metered, tools, max_steps=max_steps)
    await graph.ainvoke({"messages": opening_messages(operator_prompt(), goal)})

    return Outcome(
        goal=goal,
        succeeded=run.succeeded,
        summary=run.summary,
        stopped_because=None if run.finished else (run.summary or "the model stopped early"),
        steps=run.steps,
        stats=run.backend.stats,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
