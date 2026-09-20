"""The loop. Deliberately the stupidest thing that can finish a task.

One model node, one tool node, and an edge back that ends the run instead
when the tools already did. No plan, no memory, no subagents; verification
lives outside the model, in `verify.py`, because it costs nothing there.

The one thing the shape does carry is an interrupt. A destructive action pauses
the graph rather than deciding for the person whose phone it is, and resuming
re-runs the whole node, which is why every action's idempotency key is its tool
call id.

The two things it does have are not optional. It stops when the session says to
stop, because halting and loop detection already exist in the policy layer and
an agent that keeps driving a halted session is worse than one that gives up.
And it bounds its own steps, because a model that has lost the thread will
otherwise spend a budget discovering that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ios_agent.batch import stop_after
from ios_agent.state import AgentState
from ios_agent.tools import Run

#: A model that has taken this many turns without finishing has lost the
#: thread. Recovering from that is a planning problem, which is slice 3.
DEFAULT_MAX_STEPS = 24

#: And this many things done to a stranger's phone is enough, whatever the
#: turn count says. Until a turn could carry several calls, the turn budget
#: bounded both: one action per turn meant at most `DEFAULT_MAX_STEPS` of
#: them. Batching breaks that link and would leave the device side unbounded,
#: so the same guarantee is now stated directly rather than implied.
DEFAULT_MAX_ACTIONS = 24

#: What the loop is invoked with. Kept as a callable so a test can drive the
#: graph with a scripted model and no network.
ModelCall = Callable[[list[AnyMessage]], Awaitable[AIMessage]]


def build_graph(
    run: Run,
    call_model: ModelCall,
    tools: list[Any],
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_actions: int = DEFAULT_MAX_ACTIONS,
    checkpointer: Any | None = None,
) -> Any:
    """Wire one goal into a graph. One run, one graph; they share no state."""
    by_name = {t.name: t for t in tools}

    async def agent(state: AgentState) -> dict[str, list[AnyMessage]]:
        reply = await call_model(state["messages"])
        # Counted here rather than in `next_step` so the number is model calls,
        # which is what a turn costs. `next_step` returns END before its own
        # checks when the model called `done`, so counting there silently
        # dropped the finishing turn. The budget below is unaffected: it can
        # only fire on a turn that did not finish, and on those two turns the
        # counter has the same value at the same point it always did.
        run.turns += 1
        return {"messages": [reply]}

    async def act(state: AgentState) -> dict[str, list[AnyMessage]]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        out: list[AnyMessage] = []
        calls = list(last.tool_calls)
        for i, call in enumerate(calls):
            chosen = by_name.get(call["name"])
            if chosen is None:
                # Better to tell the model than to crash the graph: an unknown
                # tool is a recoverable mistake, and naming the real ones costs
                # one message against a whole lost run.
                out.append(
                    ToolMessage(
                        content=f"no such tool {call['name']!r}; available: {', '.join(by_name)}",
                        tool_call_id=call["id"] or "",
                    )
                )
                continue
            before = run.backend.last_action
            # The whole ToolCall, not just its args: the action tools take
            # their idempotency key from the call id, and LangChain only
            # injects it when handed the full call. Passing args alone raises.
            result = await chosen.ainvoke(call)
            out.append(
                result
                if isinstance(result, ToolMessage)
                else ToolMessage(content=str(result), tool_call_id=call["id"] or "")
            )

            remaining = calls[i + 1 :]
            if not remaining:
                break
            reason = stop_after(
                call["name"],
                run.backend.last_action,
                before.seq if before else 0,
                finished=run.finished,
                stopped=run.backend.stop_reason(),
            )
            if reason is None:
                continue
            # Every skipped call still needs a reply. LangChain requires one
            # `ToolMessage` per tool call id, and a turn that answered two of
            # three calls would fail on the next model invocation rather than
            # here, which is a long way from the cause.
            out.extend(
                ToolMessage(
                    content=(
                        f"not run: {reason} Re-issue it if the screen above is "
                        "still where you expected to be."
                    ),
                    tool_call_id=skipped["id"] or "",
                )
                for skipped in remaining
            )
            break
        return {"messages": out}

    def ending() -> bool:
        """Whether the run must stop, asked without spending a model call.

        Both edges ask, which is the point. Asking only after the model node
        meant a finished run still paid for one more call: `done` sets
        `run.finished` inside `act`, the unconditional edge went back to
        `agent`, and a whole turn was spent on a transcript whose last word
        was "recorded" before the reply was discarded. Every run paid it.
        """
        if run.finished:
            return True
        stop = run.backend.stop_reason()
        if stop is not None:
            run.summary = run.summary or f"stopped: {stop}"
            return True
        return False

    def after_act(_state: AgentState) -> str:
        return END if ending() else "agent"

    def next_step(state: AgentState) -> str:
        if ending():
            return END
        if run.turns > max_steps:
            run.summary = run.summary or f"gave up after {max_steps} turns"
            return END
        if run.steps >= max_actions:
            run.summary = run.summary or f"gave up after {max_actions} actions"
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
    builder.add_conditional_edges("act", after_act, ["agent", END])
    # A checkpointer is what makes `interrupt()` work: the state has to survive
    # the pause. In memory, because durable execution is out of scope for this
    # project and LangGraph checkpoints persist data rather than execution, so
    # a disk-backed saver would imply a guarantee it does not give.
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


def opening_messages(system_prompt: str, goal: str, apps: Sequence[str] = ()) -> list[AnyMessage]:
    """The transcript the loop starts from.

    The goal arrives as a user turn rather than being folded into the system
    prompt, so the system prompt stays byte-identical across every task and
    stays cacheable.

    `apps` rides along in the same user turn, for the same reason. The agent
    cannot otherwise know what this device has: the home screen shows one page
    of icons, and an app on page three or in a folder is invisible. Naming them
    up front costs about a hundred tokens once, against a whole turn for a
    `list_apps` call that only happens after the agent notices it is stuck.
    """
    human = goal if not apps else f"{goal}\n\nApps on this device: {', '.join(apps)}."
    return [SystemMessage(content=system_prompt), HumanMessage(content=human)]
