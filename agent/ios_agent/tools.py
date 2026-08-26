"""The verbs the model is given, and the run they act on.

Eight tools, not the server's thirty. The reason is the same one the server
already acts on: a large set of confusable tools measurably degrades tool
selection. `read_text`, `handle_alert`, `wait_for` and `screenshot` are
deliberately absent until a task fails without them, so that adding one is a
decision with a number behind it.

## Idempotency keys come from the tool call id

Resuming an interrupted graph re-runs the **whole node**, so every tool call in
it executes a second time. `IosSession` has had act-once semantics since its
first action for that reason, and this is the layer that has to use them
correctly.

The first version did not. Keys were `f"{id(run)}-{steps}-{action}"` off a
counter that incremented on every call, so a re-run produced a *different* key,
the cache missed, and the device was touched twice. The comment said the keys
existed to prevent exactly that. They were decorative, and would have stayed
that way until the day someone wired up approvals.

The key is now the tool call id, which LangGraph stores in the message and
replays unchanged. Same logical call, same key, whatever happens in between.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from langchain.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import interrupt

from ios_agent.backend import Backend


@dataclass
class Run:
    """Mutable state for one goal that is not part of the message channel.

    The step counter feeds the idempotency keys and the tool closures read it,
    so it cannot live in the graph state without threading `Command` through
    every tool. For a skeleton that is plumbing without a measurement behind
    it, so it lives here instead.
    """

    backend: Backend
    goal: str
    steps: int = 0
    finished: bool = False
    succeeded: bool = False
    summary: str = ""
    #: Every error a tool turned into a message rather than raising. An agent
    #: that cannot see its own failures cannot recover from them.
    errors: list[str] = field(default_factory=list)

    #: Approvals granted by a human this run, by signature. Kept so a resumed
    #: node does not ask twice for the same action.
    approved: set[str] = field(default_factory=set)
    #: How many times a human was stopped and asked. Reported, because an agent
    #: that interrupts constantly is unusable however safe it is.
    approvals_asked: int = 0

    def count(self) -> None:
        """Record that an action was attempted. Not used for keying."""
        self.steps += 1


def build_tools(run: Run) -> list[BaseTool]:
    """Bind the tool surface to one run."""
    backend = run.backend

    async def guarded(action: str, call: object) -> str:
        """Run one tool call, turning a typed failure into something readable.

        Two failures are handled differently on purpose.

        An approval requirement is not a failure at all, it is a question, so
        it pauses the graph with `interrupt()` and waits for a human. The
        action has not touched the device at that point: the gate classifies
        *before* acting, which is what makes the pause worth anything.

        Everything else typed becomes a message. Resolution failures arrive
        with candidate elements attached, so an agent that aimed at the wrong
        label is told what it could have picked instead, and raising would end
        the graph on a mistake fixable in one turn.
        """
        from ios_mcp.errors import ActionRequiresApproval, IosAutomationError

        assert callable(call)
        try:
            return str(await call())
        except ActionRequiresApproval as exc:
            return await _ask_a_human(action, exc, call)
        except IosAutomationError as exc:
            message = f"{action} failed ({exc.code}): {exc}"
            if exc.hint:
                message += f"\nhint: {exc.hint}"
            run.errors.append(message)
            return message

    async def _ask_a_human(action: str, exc: object, call: object) -> str:
        """Pause the graph, and act only if the answer is yes.

        `interrupt()` raises out of the node, so the retry below is reached
        only on a resume, when it returns the value the caller supplied. On
        that resume the whole node re-runs, which is why every action's key is
        its tool call id: any *other* call in the same node replays from the
        idempotency cache instead of touching the device again.
        """
        details = getattr(exc, "details", {}) or {}
        signature = str(details.get("signature", ""))
        verdict = details.get("verdict") or {}

        if signature not in run.approved:
            answer = interrupt(
                {
                    "type": "approval_required",
                    "action": action,
                    "goal": run.goal,
                    "signature": signature,
                    "reason": verdict.get("reason") or str(exc),
                    "matched": verdict.get("matched"),
                }
            )
            if not _is_yes(answer):
                refusal = f"{action} was not approved by the operator, so it did not run."
                run.errors.append(refusal)
                return refusal
            run.approved.add(signature)

        backend.approve(signature)
        assert callable(call)
        return str(await call())

    @tool
    async def observe() -> str:
        """Read the current screen. Only when you do not already know it."""
        return await guarded("observe", backend.observe)

    @tool
    async def tap(target: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
        """Tap an element by its visible label, for example "Accessibility"."""
        run.count()
        key = tool_call_id
        return await guarded("tap", lambda: backend.tap(target, idem_key=key))

    @tool
    async def type_text(
        text: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        target: str | None = None,
    ) -> str:
        """Type into a field, focusing `target` first when one is given."""
        run.count()
        key = tool_call_id
        return await guarded("type_text", lambda: backend.type_text(text, target, idem_key=key))

    @tool
    async def set_value(
        value: str, target: str, tool_call_id: Annotated[str, InjectedToolCallId]
    ) -> str:
        """Set a switch, slider or picker. Use "on" or "off" for a switch.

        Prefer this over tapping a switch: it is state-aware, so asking for a
        state the control is already in does nothing rather than toggling it.
        """
        run.count()
        key = tool_call_id
        return await guarded("set_value", lambda: backend.set_value(value, target, idem_key=key))

    @tool
    async def scroll(
        tool_call_id: Annotated[str, InjectedToolCallId],
        direction: str = "down",
        until: str | None = None,
    ) -> str:
        """Scroll, optionally repeating until `until` appears on screen.

        Passing `until` is much cheaper than scrolling one screen at a time:
        the search runs on the server and costs you nothing per step.
        """
        run.count()
        key = tool_call_id
        return await guarded("scroll", lambda: backend.scroll(direction, until, idem_key=key))

    @tool
    async def press_button(name: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
        """Press a hardware button or keyboard key, for example "home"."""
        run.count()
        key = tool_call_id
        return await guarded("press_button", lambda: backend.press_button(name, idem_key=key))

    @tool
    async def open_url(url: str) -> str:
        """Open a deep link, the cheapest way to reach a known screen.

        Some links are accepted and then ignored, so check the screen you get
        back rather than assuming the link worked.
        """
        run.count()
        return await guarded("open_url", lambda: backend.open_url(url))

    @tool
    def done(succeeded: bool, summary: str) -> str:
        """Finish. Set `succeeded` only if the screen shows the goal was reached."""
        run.finished = True
        run.succeeded = succeeded
        run.summary = summary
        return "recorded"

    return [observe, tap, type_text, set_value, scroll, press_button, open_url, done]


def _is_yes(answer: object) -> bool:
    """Read a decision, defaulting to refusal.

    SAFETY.md: a client that cannot answer is treated as refusal, because an
    unanswerable question is not consent. Anything unrecognised is a no.
    """
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, dict):
        return bool(answer.get("approved") or answer.get("approve"))
    if isinstance(answer, str):
        return answer.strip().lower() in {"yes", "y", "approve", "approved", "true", "ok"}
    return False
