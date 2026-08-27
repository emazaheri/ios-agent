"""Shared scaffolding for the front-end tests.

The scripted model and the tightened settings are the same shapes
`tests/unit/test_agent_loop.py` uses, deliberately: these tests compare a
wrapped run against an unwrapped one, and the comparison is only worth
anything if the unwrapped side is driven exactly the way the agent's own tests
drive it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from langchain.messages import AIMessage, AnyMessage

from ios_mcp.config import Settings

Script = list[list[tuple[str, dict[str, object]]]]


def settings(*, confirm_destructive: bool = False) -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    cfg.policy.loop_detection_window = 50
    cfg.policy.confirm_destructive = confirm_destructive
    return cfg


class ScriptedModel:
    """Replays a fixed list of tool calls, one turn at a time.

    `prefix` decides the tool call ids, and those ids are the idempotency keys,
    so it decides something real rather than being cosmetic.

    - **Comparing two runs** wants the *same* prefix, so the two sides key
      identically and the comparison measures the thing under test rather than
      the ids.
    - **Two goals in one session** want *different* prefixes, because the
      idempotency cache lives on `IosSession` and outlives a goal. A real
      provider issues a fresh id for every call it makes, so identical ids
      across two separate goals is an artefact of scripting, not a situation
      the agent can be in. Reusing one here makes the second goal replay from
      the cache and never touch the device.
    """

    def __init__(self, script: Script, *, prefix: str = "call", delay: float = 0.0) -> None:
        self.script = script
        self.prefix = prefix
        #: Seconds per turn. A real model is never instant, and a run that
        #: finishes between two keystrokes cannot exercise a stop.
        self.delay = delay
        self.turns = 0
        self.seen: list[list[AnyMessage]] = []

    def __call__(self, _tools: list[object]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        async def call(messages: list[AnyMessage]) -> AIMessage:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.seen.append(list(messages))
            if self.turns >= len(self.script):
                return AIMessage(content="out of script")
            calls = [
                {
                    "name": name,
                    "args": args,
                    "id": f"{self.prefix}-{self.turns}-{i}",
                    "type": "tool_call",
                }
                for i, (name, args) in enumerate(self.script[self.turns])
            ]
            self.turns += 1
            return AIMessage(content="", tool_calls=calls)  # type: ignore[arg-type]

        return call
