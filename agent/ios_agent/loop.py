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

from collections.abc import Awaitable, Callable, Mapping
from importlib import resources
from typing import Any

from langchain.messages import AIMessage, AnyMessage, SystemMessage
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


def cache_writes(details: Mapping[str, Any]) -> int:
    """Tokens written to the prompt cache, wherever the integration put them.

    `cache_creation` alone is not enough. When Anthropic breaks a write down
    by TTL, `langchain-anthropic` moves the count to `ephemeral_5m_input_tokens`
    and `ephemeral_1h_input_tokens` and sets `cache_creation` to 0 so the total
    is not counted twice. Reading only `cache_creation` priced every Anthropic
    write as an ordinary input token; a real run on `claude-haiku-5-5` showed
    it, with 102 tokens written and 0 reported.
    """
    return sum(
        details.get(key) or 0
        for key in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
    )


def cache_breakpoint(messages: list[AnyMessage]) -> list[AnyMessage]:
    """The same transcript, with a cache breakpoint at the end of the system prompt.

    Automatic caching, which `AgentSettings.bind_kwargs` turns on, puts its
    breakpoint on the last block, so it serves the turns of one run and never
    the first turn of the next: that run's goal differs, and the goal comes
    straight after the system prompt. Tools and system prompt are the same
    for every run, and this marker is what lets a run start on a cache hit.

    A copy, made at call time. The graph's own state keeps the plain system
    message, so the transcript stays provider-neutral and every turn sends the
    same bytes for it.
    """
    if not messages or not isinstance(messages[0], SystemMessage):
        return messages
    system = messages[0]
    if not isinstance(system.content, str):
        return messages
    marked = SystemMessage(
        content=[{"type": "text", "text": system.content, "cache_control": {"type": "ephemeral"}}]
    )
    return [marked, *messages[1:]]


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

        bound = chat.bind_tools(tools, **cfg.bind_kwargs())
        caching = cfg.caches_prompt

        async def call(messages: list[AnyMessage]) -> AIMessage:
            reply = await bound.ainvoke(cache_breakpoint(messages) if caching else messages)
            assert isinstance(reply, AIMessage)
            return reply

        return call

    return factory


async def _device_context(session: IosSession) -> tuple[list[str], str | None]:
    """What is installed, and which of it is in front, for the opening turn.

    Both are best effort. A device that will not enumerate its apps is still a
    device the agent can drive, and failing a run over a nicety would be a
    worse trade than starting without the list.

    The second half exists because the first half caused a regression. Once
    the agent could see an app list it opened an app on its very first move,
    every run, including the runs already inside that app: measured at one
    wasted device action per run across the whole task set, which pushed
    actions from 1.29x the oracle floor to 1.52x. Naming what exists without
    naming where you are is an invitation to guess, and the guess costs a
    round trip to the device to learn nothing.
    """
    try:
        apps = await session.lease.adapter.list_apps("all")
    except Exception:
        apps = []
    names = sorted({a.name for a in apps if a.name})
    try:
        active = await session.foreground_app()
    except Exception:
        active = None
    current = next((a.name for a in apps if a.bundle_id == active and a.name), None)
    return names, current


async def run_goal(
    session: IosSession,
    goal: str,
    *,
    model: ModelFactory | None = None,
    backend: Backend | None = None,
    settings: AgentSettings | None = None,
    approve: Approver | None = None,
    max_steps: int | None = None,
    route: ModelFactory | None = None,
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
    large_name = cfg.model

    # The cascade from ADR 0015: start on the small model when one is given,
    # and move to the configured one for the rest of the run at the first sign
    # of trouble. `route` is injectable for the same reason `model` is.
    small_name = cfg.route_model
    call_small = None
    if route is not None:
        call_small = route(tools)
        small_name = small_name or "route"
    elif cfg.route_model and model is None:
        call_small = chat_model(cfg.model_copy(update={"model": cfg.route_model}))(tools)
    escalated_at: int | None = None
    #: The counters the trouble check compares against, as of the last turn.
    seen = {"errors": 0, "refusals": 0, "actions": 0, "progress": 0}

    def trouble() -> bool:
        """Did anything go wrong since the last turn? Counters only, no device read.

        A failed tool call, a refused repeat, or an action that neither moved
        the screen nor found its element already as asked.
        """
        stats = run.backend.stats
        progress = stats.changes + stats.satisfied
        now = {
            "errors": len(run.errors),
            "refusals": stats.refusals,
            "actions": stats.actions,
            "progress": progress,
        }
        bad = (
            now["errors"] > seen["errors"]
            or now["refusals"] > seen["refusals"]
            or (now["actions"] - seen["actions"]) > (now["progress"] - seen["progress"])
        )
        seen.update(now)
        return bad

    prompt_tokens = 0
    completion_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    model_served: str | None = None
    tokens_by_model: dict[str, list[int]] = {}

    async def metered(messages: list[AnyMessage]) -> AIMessage:
        nonlocal prompt_tokens, completion_tokens, model_served, escalated_at
        nonlocal cache_read_tokens, cache_write_tokens
        on_small = call_small is not None and escalated_at is None
        if on_small and trouble():
            escalated_at = run.turns
            on_small = False
        caller = call_small if on_small and call_small is not None else call_model
        name = (small_name if on_small else large_name) or large_name
        reply = await caller(messages)
        usage = reply.usage_metadata
        if usage:
            prompt_tokens += usage.get("input_tokens", 0)
            completion_tokens += usage.get("output_tokens", 0)
            spent = tokens_by_model.setdefault(name, [0, 0])
            spent[0] += usage.get("input_tokens", 0)
            spent[1] += usage.get("output_tokens", 0)
            # Already inside `input_tokens`, so these split the total rather
            # than add to it. Either key can be absent or None: a provider
            # that does not cache, or one that does and did not say.
            details = usage.get("input_token_details") or {}
            cache_read_tokens += details.get("cache_read") or 0
            cache_write_tokens += cache_writes(details)
        # What the provider says it served, which `AgentSettings.model` cannot
        # say: that is an alias, and the thing behind it moves. Read from the
        # first reply that names one and not overwritten, so a report says what
        # produced the run rather than what produced its last turn.
        #
        # Best effort by design. The key is not part of any provider contract,
        # a scripted model in a test has no metadata at all, and losing a run
        # over a missing label would be a worse trade than recording None.
        if model_served is None:
            served = (reply.response_metadata or {}).get("model_name")
            model_served = str(served) if served else None
        return reply

    # One budget for both. The action cap exists to keep the guarantee the turn
    # budget used to give (one action per turn, so at most `max_steps` of
    # them), and passing only the turns left it at its default: a caller that
    # raised `max_steps` to 60 still had its run ended at 24 actions.
    budget = max_steps or cfg.max_steps
    graph = build_graph(run, metered, tools, max_steps=budget, max_actions=budget)
    decide = approve or refuse_everything
    # One thread per run. The checkpointer keys on it, and reusing an id across
    # runs would resume someone else's conversation.
    config = {"configurable": {"thread_id": f"{id(run):x}"}}

    apps, current = await _device_context(session)
    step: Any = {"messages": opening_messages(operator_prompt(), goal, apps, current)}
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

    stats = run.backend.stats
    # The claim, with the device allowed to disagree. The agent decides when it
    # is finished, and it has no reliable way of knowing: the loop ends whenever
    # the model stops asking for tools, so `done(succeeded=True)` is an opinion
    # formed from the same screens that produced it.
    #
    # A run that acted and never moved anything is the one case where the
    # opinion can be contradicted without reading the device again. Zero actions
    # is not that case and must not be treated as one: reading a screen and
    # answering from it is a legitimate way to finish, which two eval tasks do
    # at their floor, and one of them passes precisely by refusing to act.
    #
    # `stopped_because` stays out of it. That field means the loop ended for a
    # reason other than the agent finishing, and this run did finish; writing
    # here would make `finished_cleanly` say something untrue.
    #
    # An action the session left alone because the element was already as asked
    # backs the claim as well as a change does: the device is in the state the
    # agent says it is. Without this the verdict called that correct run a lie,
    # which is the flaw its own docstring used to rule out for a stronger rule.
    contradicted = bool(
        run.succeeded and stats.actions > 0 and stats.changes == 0 and stats.satisfied == 0
    )
    return Outcome(
        goal=goal,
        succeeded=run.succeeded,
        summary=run.summary,
        stopped_because=None if run.finished else (run.summary or "the model stopped early"),
        steps=run.steps,
        turns=run.turns,
        approvals_asked=run.approvals_asked,
        stats=stats,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        model_served=model_served,
        contradicted=contradicted,
        tokens_by_model={k: (v[0], v[1]) for k, v in tokens_by_model.items()},
        escalated_at_turn=escalated_at,
    )
