"""What the loop carries between turns.

One rule matters here: **no `Digest` object ever enters the state.** A digest
carries rects and refs, and refs are positional, so one taken before a scroll
denotes a different element afterwards. Carrying the rendered text and the
fingerprint instead means a stale screen in the transcript reads as stale text
rather than as coordinates the agent might act on. Acting on the wrong control
is the worst failure this system can have, and the perception layer goes to
some trouble to prevent it; the agent must not undo that by caching geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain.messages import AnyMessage
from langgraph.graph.message import add_messages

from ios_agent.backend import BackendStats


class AgentState(TypedDict):
    """The graph's channel. Messages accumulate; nothing else does."""

    messages: Annotated[list[AnyMessage], add_messages]


@dataclass
class Outcome:
    """What one run of a goal produced.

    `succeeded` is the agent's own claim and is deliberately kept apart from
    whether the world actually changed. The evals judge the device, never this
    field, because a switch reporting success while never moving is exactly the
    failure being measured.
    """

    goal: str
    #: True only if the agent called `done` and said it finished the goal.
    succeeded: bool = False
    #: The agent's account of what happened, in its own words.
    summary: str = ""
    #: Set when the loop ended for a reason other than the agent finishing:
    #: a halted session, a detected loop, or the step budget running out.
    stopped_because: str | None = None
    steps: int = 0
    #: Times the run paused to ask a human. Zero is the expected value for an
    #: ordinary task; anything else means the policy gate fired.
    approvals_asked: int = 0
    stats: BackendStats = field(default_factory=BackendStats)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def finished_cleanly(self) -> bool:
        return self.stopped_because is None
