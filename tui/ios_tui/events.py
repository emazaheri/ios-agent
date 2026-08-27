"""What happened, as values.

One rule, inherited from `ios_agent.state` for the same reason: **no `Digest`,
`ActionResult` or `IosSession` ever enters an event.** An event is read later
than it was made, by a widget that may be several frames behind, and a digest
carries refs and rects. Refs are positional, so one taken before a scroll
denotes a different element afterwards. Carrying rendered text means a stale
screen reads as stale text rather than as coordinates something might act on.

The same reasoning applies to `BackendStats`, which is mutable and live. An
event carries a `StatsSnapshot` taken at the moment it was emitted, so a
transcript row rendered two seconds later shows what that action cost rather
than what the session has cost since.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ios_agent.backend import BackendStats


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    """A frozen copy of what a run had cost at one instant."""

    observations: int = 0
    actions: int = 0
    device_tokens: int = 0
    refusals: int = 0

    @classmethod
    def of(cls, stats: BackendStats) -> StatsSnapshot:
        return cls(
            observations=stats.observations,
            actions=stats.actions,
            device_tokens=stats.device_tokens,
            refusals=stats.refusals,
        )

    @property
    def observation_overhead(self) -> float:
        """Explicit observations per action. One over N is the floor.

        Mirrors `BackendStats.observation_overhead` rather than importing it,
        because this type exists precisely so nothing holds the live object.
        """
        return self.observations / self.actions if self.actions else float(self.observations)


@dataclass(frozen=True, slots=True)
class Event:
    """Base for every event. `at` is monotonic, so it is only good for deltas."""

    at: float = field(default_factory=time.monotonic)


# -- getting a device ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Progress(Event):
    """One line of device startup, bridged out of `logging`.

    `DevicePool.acquire` can run for minutes with nothing to show for it, and
    its only signal is INFO logging. See `progress.py`.
    """

    text: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class DeviceReady(Event):
    #: `Lease.to_dict()`: device, wda_url, session_id, foreground_app, ages.
    lease: Mapping[str, Any] = field(default_factory=dict)


# -- one goal --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoalStarted(Event):
    goal: str = ""
    #: `AgentSettings.describe()`, e.g. "anthropic:claude-opus-5 effort=medium".
    model: str = ""


@dataclass(frozen=True, slots=True)
class ModelDelta(Event):
    """One streamed fragment. Not cumulative: append them."""

    text: str = ""


@dataclass(frozen=True, slots=True)
class ModelTurn(Event):
    """The assembled turn, emitted after its deltas."""

    text: str = ""
    #: Names only. The arguments arrive on `ActionStarted`, where they belong
    #: to an action rather than to a turn that may have requested several.
    tool_calls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GoalFinished(Event):
    goal: str = ""
    succeeded: bool = False
    summary: str = ""
    stopped_because: str | None = None
    steps: int = 0
    approvals_asked: int = 0
    stats: StatsSnapshot = field(default_factory=StatsSnapshot)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_s: float = 0.0


# -- what it did to the phone ----------------------------------------------


@dataclass(frozen=True, slots=True)
class ActionStarted(Event):
    verb: str = ""
    #: Stringified already. Nothing here is a live object, and a secret never
    #: reaches this layer: `type_secret` is not on the agent's tool surface.
    args: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionFinished(Event):
    verb: str = ""
    args: Mapping[str, str] = field(default_factory=dict)
    #: Exactly the string the model was handed, so what the transcript shows
    #: and what the model read cannot drift apart.
    rendered: str = ""
    elapsed_ms: int = 0
    #: Verification refused it before it reached the device. Kept apart from a
    #: failure because nothing was touched, and apart from a success because
    #: the agent spent a turn.
    refused: bool = False
    #: Whether this action produced a **full** screen to display.
    #:
    #: It often does not, and that is by design rather than a gap: an action
    #: whose screen is similar to the last one returns a *delta* instead, which
    #: is what keeps long flows cheap. The model reads the delta against the
    #: screen it already has; a pane cannot, so a consumer that shows the last
    #: full screen has to know when that screen has been overtaken. Guessing
    #: from the rendered text would mean parsing it, so the wrapper reports it.
    screen_refreshed: bool = False
    stats: StatsSnapshot = field(default_factory=StatsSnapshot)


@dataclass(frozen=True, slots=True)
class Observed(Event):
    rendered: str = ""
    stats: StatsSnapshot = field(default_factory=StatsSnapshot)


@dataclass(frozen=True, slots=True)
class ScreenUpdated(Event):
    """The last screen changed. Emitted only when it did."""

    text: str = ""


# -- asking a human --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalAsked(Event):
    #: The interrupt payload from `ios_agent.tools`, verbatim: type, action,
    #: goal, signature, reason, matched.
    request: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovalAnswered(Event):
    signature: str = ""
    allowed: bool = False


# -- stopping --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stopping(Event):
    """A stop was requested. The graph ends after the current node."""

    reason: str = ""


@dataclass(frozen=True, slots=True)
class Failed(Event):
    #: "acquire" | "run" | "release", so a consumer can tell a device that
    #: never arrived from a goal that fell over.
    where: str = ""
    message: str = ""
