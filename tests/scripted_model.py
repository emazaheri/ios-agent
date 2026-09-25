"""A model that replays a fixed list of tool calls.

Lives here rather than beside one suite because two need it for opposite
reasons. `tests/unit/test_agent_loop.py` uses it to test the loop's wiring
without paying for a model. `tests/evals/agent` uses it to prove that the
measurement apparatus can *detect* a given behaviour, which has to be settled
before a model-backed number about that behaviour means anything: a task that
cannot fail is not measuring.

What it replaces is only the model. The graph, the tool definitions,
`SessionBackend`, `IosSession`, the policy gate and a device whose screens
respond to taps are all real either way.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.messages import AIMessage, AnyMessage


class ScriptedModel:
    """Replays a fixed list of tool calls, one per turn.

    Records every message list it was handed, so a test can assert on what the
    agent was actually shown rather than on what it was meant to be shown.
    """

    def __init__(self, script: list[list[tuple[str, dict[str, object]]]]) -> None:
        self.script = script
        self.turns = 0
        self.seen: list[list[AnyMessage]] = []

    def __call__(self, _tools: list[object]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        async def call(messages: list[AnyMessage]) -> AIMessage:
            self.seen.append(list(messages))
            if self.turns >= len(self.script):
                return AIMessage(content="out of script")
            calls = [
                {"name": name, "args": args, "id": f"call-{self.turns}-{i}", "type": "tool_call"}
                for i, (name, args) in enumerate(self.script[self.turns])
            ]
            self.turns += 1
            return AIMessage(content="", tool_calls=calls)  # type: ignore[arg-type]

        return call
