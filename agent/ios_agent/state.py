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

    `verified` is that claim after the device has been allowed to contradict it.
    Callers that need an answer rather than a claim read that one; the claim is
    kept because the gap between the two is measurable only while both exist.
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
    #: Model calls made. Not on `BackendStats`: a turn is the graph's
    #: unit, not the device's, and `tests/tui/test_cost.py` compares the
    #: whole of `stats` by equality between a watched and a bare run.
    turns: int = 0
    #: Times the run paused to ask a human. Zero is the expected value for an
    #: ordinary task; anything else means the policy gate fired.
    approvals_asked: int = 0
    stats: BackendStats = field(default_factory=BackendStats)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: What the provider said it actually ran, or None when it did not say and
    #: when no model was in the loop at all.
    #:
    #: Kept apart from `AgentSettings.model` because that is a request and this
    #: is an answer. `claude-opus-5` is an alias over something that moves, so a
    #: number recorded against the alias alone cannot tell a prompt change from
    #: a provider changing what the alias points at.
    #:
    #: How much it helps is the provider's decision, not this field's. Measured
    #: against `openai:gpt-5.6-sol`, the answer came back as `gpt-5.6-sol`: the
    #: same string as the request, so for that provider and model the gap stays
    #: open and this records that it does. It closes only where a provider names
    #: a dated snapshot, which is untested here.
    model_served: str | None = None
    #: Set when the agent claimed success and nothing it did moved the device.
    #: See `verified`, which is the field to read.
    contradicted: bool = False

    @property
    def verified(self) -> bool:
        """The claim, with the device given a chance to disagree.

        Narrow on purpose, and worth knowing how narrow. It is False only when
        *nothing* the agent did moved the screen. A run that navigates somewhere
        and then meets a dead switch has one action that changed something, so
        its false claim still reads as verified.

        The stronger rule, "the last action changed nothing", is not available:
        `verify.py` documents that a dead switch and a control already in the
        requested state produce byte-identical results, so that rule would call
        a correct run on an already-satisfied goal a lie. This one has no false
        positives and a short reach, which is the trade.
        """
        return self.succeeded and not self.contradicted

    @property
    def finished_cleanly(self) -> bool:
        return self.stopped_because is None
