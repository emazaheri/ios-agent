"""The loop. Deliberately the stupidest thing that can finish a task.

One model node, one tool node, an edge back. No plan, no verification step, no
memory, no subagents. That is the point: this is the baseline every later slice
has to beat, and a baseline that already contains half the ideas cannot show
what any of them bought.

The two things it does have are not optional. It stops when the session says to
stop, because halting and loop detection already exist in the policy layer and
an agent that keeps driving a halted session is worse than one that gives up.
And it bounds its own steps, because a model that has lost the thread will
otherwise spend a budget discovering that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from ios_agent.state import AgentState
from ios_agent.tools import Run

#: A model that has taken this many turns without finishing has lost the
#: thread. Recovering from that is a planning problem, which is slice 3.
DEFAULT_MAX_STEPS = 24

#: What the loop is invoked with. Kept as a callable so a test can drive the
#: graph with a scripted model and no network.
ModelCall = Callable[[list[AnyMessage]], Awaitable[AIMessage]]


def build_graph(
    run: Run,
    call_model: ModelCall,
    tools: list[Any],
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> Any:
    """Wire one goal into a graph. One run, one graph; they share no state."""
    by_name = {t.name: t for t in tools}
    turns = 0

    async def agent(state: AgentState) -> dict[str, list[AnyMessage]]:
        reply = await call_model(state["messages"])
        return {"messages": [reply]}

    async def act(state: AgentState) -> dict[str, list[AnyMessage]]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        out: list[AnyMessage] = []
        for call in last.tool_calls:
            chosen = by_name.get(call["name"])
            if chosen is None:
                # Better to tell the model than to crash the graph: an
                # unknown tool is a recoverable mistake, and naming the real
                # ones costs one message against a whole lost run.
                content = f"no such tool {call['name']!r}; available: {', '.join(by_name)}"
            else:
                content = str(await chosen.ainvoke(call["args"]))
            out.append(ToolMessage(content=content, tool_call_id=call["id"] or ""))
        return {"messages": out}

    def next_step(state: AgentState) -> str:
        nonlocal turns
        if run.finished:
            return END
        stop = run.backend.stop_reason()
        if stop is not None:
            run.summary = run.summary or f"stopped: {stop}"
            return END
        turns += 1
        if turns > max_steps:
            run.summary = run.summary or f"gave up after {max_steps} turns"
            return END
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "act"
        # No tool call and no `done`. Keep whatever the model said instead:
        # stopping without a tool call is often a refusal, and discarding the
        # text turns "I will not erase this device" into "the model stopped
        # early", which is the same outcome reported as a malfunction.
        if isinstance(last, AIMessage) and last.text:
            run.summary = run.summary or last.text
        return END

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent)
    builder.add_node("act", act)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", next_step, ["act", END])
    builder.add_edge("act", "agent")
    return builder.compile()


def opening_messages(system_prompt: str, goal: str) -> list[AnyMessage]:
    """The transcript the loop starts from.

    The goal arrives as a user turn rather than being folded into the system
    prompt, so the system prompt stays byte-identical across every task and
    stays cacheable.
    """
    return [SystemMessage(content=system_prompt), HumanMessage(content=goal)]
