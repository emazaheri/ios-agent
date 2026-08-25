"""The verbs the model is given, and the run they act on.

Eight tools, not the server's thirty. The reason is the same one the server
already acts on: a large set of confusable tools measurably degrades tool
selection. `read_text`, `handle_alert`, `wait_for` and `screenshot` are
deliberately absent until a task fails without them, so that adding one is a
decision with a number behind it.

Every action carries an idempotency key derived from the step number. Agent
frameworks re-run the node an interrupt was raised from, so a resumed graph
without keys taps Send twice. `IosSession` has had act-once semantics since its
first action for exactly this reason, and this is the layer that has to use
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain.tools import BaseTool, tool

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

    def next_key(self, action: str) -> str:
        self.steps += 1
        return f"{id(self):x}-{self.steps}-{action}"


def build_tools(run: Run) -> list[BaseTool]:
    """Bind the tool surface to one run."""
    backend = run.backend

    async def guarded(action: str, call: object) -> str:
        """Run one tool call, turning a typed failure into something readable.

        Resolution failures arrive with candidate elements attached, so an
        agent that aimed at the wrong label is told what it could have picked
        instead. Raising here would end the graph on a recoverable mistake.
        """
        from ios_mcp.errors import IosAutomationError

        assert callable(call)
        try:
            return str(await call())
        except IosAutomationError as exc:
            message = f"{action} failed ({exc.code}): {exc}"
            if exc.hint:
                message += f"\nhint: {exc.hint}"
            run.errors.append(message)
            return message

    @tool
    async def observe() -> str:
        """Read the current screen. Only when you do not already know it."""
        return await guarded("observe", backend.observe)

    @tool
    async def tap(target: str) -> str:
        """Tap an element by its visible label, for example "Accessibility"."""
        key = run.next_key("tap")
        return await guarded("tap", lambda: backend.tap(target, idem_key=key))

    @tool
    async def type_text(text: str, target: str | None = None) -> str:
        """Type into a field, focusing `target` first when one is given."""
        key = run.next_key("type")
        return await guarded("type_text", lambda: backend.type_text(text, target, idem_key=key))

    @tool
    async def set_value(value: str, target: str) -> str:
        """Set a switch, slider or picker. Use "on" or "off" for a switch.

        Prefer this over tapping a switch: it is state-aware, so asking for a
        state the control is already in does nothing rather than toggling it.
        """
        key = run.next_key("set_value")
        return await guarded("set_value", lambda: backend.set_value(value, target, idem_key=key))

    @tool
    async def scroll(direction: str = "down", until: str | None = None) -> str:
        """Scroll, optionally repeating until `until` appears on screen.

        Passing `until` is much cheaper than scrolling one screen at a time:
        the search runs on the server and costs you nothing per step.
        """
        key = run.next_key("scroll")
        return await guarded("scroll", lambda: backend.scroll(direction, until, idem_key=key))

    @tool
    async def press_button(name: str) -> str:
        """Press a hardware button or keyboard key, for example "home"."""
        key = run.next_key("press_button")
        return await guarded("press_button", lambda: backend.press_button(name, idem_key=key))

    @tool
    async def open_url(url: str) -> str:
        """Open a deep link, the cheapest way to reach a known screen.

        Some links are accepted and then ignored, so check the screen you get
        back rather than assuming the link worked.
        """
        run.next_key("open_url")
        return await guarded("open_url", lambda: backend.open_url(url))

    @tool
    def done(succeeded: bool, summary: str) -> str:
        """Finish. Set `succeeded` only if the screen shows the goal was reached."""
        run.finished = True
        run.succeeded = succeeded
        run.summary = summary
        return "recorded"

    return [observe, tap, type_text, set_value, scroll, press_button, open_url, done]
