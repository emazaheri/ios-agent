"""Pool, session, goal, stop. No terminal anywhere in here.

`GoalRunner` is what both front ends drive: the plain printer and the Textual
app differ in how they render events, not in how they get a device or run a
goal.

Two decisions are recorded here rather than in a comment somewhere downstream.

**One `run_goal` per goal, with no message carried between them.** The agent
does not remember the last goal; the phone does. That is not a shortcut. The
system prompt stays byte-identical and therefore cacheable, which is exactly
why `opening_messages` puts the goal in a user turn. A carried transcript would
carry stale rendered screens, which `ios_agent.state` exists to prevent. And a
prefix that grows with every goal changes what a run costs, which this package
is not allowed to do.

**A fresh `SessionBackend` and a fresh `Verifier` per goal.** The verifier's
ledger counts repeats of one attempt while pursuing one goal, which is what
`ios_agent.verify` says it is for. Across goals a repeat is new intent: turn
wi-fi off, flip it back by hand, ask again. A session-lifetime verifier would
hard-refuse that second goal's first action before touching the device, which
is a front end inventing a policy the agent does not have. Nothing is lost,
because the session-scoped protections are still session-scoped: loop detection
and the idempotency cache both live on `IosSession` and survive.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from ios_agent import AgentSettings, Approver, Outcome, SessionBackend, run_goal
from ios_agent.loop import ModelFactory
from ios_agent.verify import Verifier

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool, Lease
from ios_mcp.session import IosSession
from ios_tui.bus import EventSink
from ios_tui.events import (
    ApprovalAsked,
    DeviceReady,
    Failed,
    GoalFinished,
    GoalStarted,
    StatsSnapshot,
    Stopping,
)
from ios_tui.progress import device_progress
from ios_tui.stream import EventBackend

#: How a lease is obtained. Injectable so tests drive a scripted device and
#: never start a simulator.
Acquire = Callable[[], Awaitable[Lease]]


def tuned(base: Settings | None = None) -> Settings:
    """Timings a real device needs.

    A snapshot costs about 3.7s on a phone against well under a second on a
    simulator, so the settle ceiling has to exceed several snapshots or a
    device times out on every action. Applied unconditionally: both numbers are
    ceilings, so raising them costs a simulator nothing and not raising them
    costs a phone every action it tries.
    """
    cfg = base or Settings()
    cfg.stabilize.max_wait_s = max(cfg.stabilize.max_wait_s, 20.0)
    cfg.wda.startup_timeout_s = max(cfg.wda.startup_timeout_s, 300.0)
    return cfg


class GoalRunner:
    """One device, many goals."""

    def __init__(
        self,
        sink: EventSink,
        settings: Settings,
        agent: AgentSettings | None = None,
        *,
        device: str | None = None,
        bundle_id: str | None = None,
        acquire: Acquire | None = None,
        model: ModelFactory | None = None,
    ) -> None:
        self.sink = sink
        self.settings = settings
        self.agent = agent or AgentSettings()
        #: Which device to acquire. Public because the picker sets it after
        #: the runner exists but before `start()` has been called.
        self.device = device
        self._bundle_id = bundle_id
        self._pool: DevicePool | None = None
        self._acquire = acquire
        #: `None` means the loop builds the configured provider. Injectable for
        #: the same reason `run_goal` takes one: the mechanics deserve a
        #: deterministic test, and one that needs an API key is not that.
        self._model = model
        self.session: IosSession | None = None
        #: Carried across goals so a new goal's screen pane does not start
        #: blank. The backend itself is rebuilt every time; this is only text.
        self._last_screen = ""

    @property
    def last_screen(self) -> str:
        """The most recent screen any goal ended on, carried across goals."""
        return self._last_screen

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> IosSession:
        """Get a device. The long, silent part, so it reports as it goes."""
        try:
            with device_progress(self.sink):
                lease = await self._lease()
        except Exception as exc:
            self.sink.emit(Failed(where="acquire", message=str(exc)))
            raise
        self.session = IosSession(lease, self.settings)
        self.sink.emit(DeviceReady(lease=lease.to_dict()))
        return self.session

    async def _lease(self) -> Lease:
        if self._acquire is not None:
            return await self._acquire()
        self._pool = DevicePool(self.settings)
        return await self._pool.acquire(self.device, bundle_id=self._bundle_id)

    async def switch(self, device: str) -> IosSession:
        """Let go of the current device and take another.

        The session, the pool lease and the last screen all belong to the
        device being left, so all three go. The audit trail goes with the
        session for the same reason: it is that device's record of what was
        done to it, and carrying it onto the next one would make one trail
        claim to describe two phones.
        """
        await self.close()
        self._pool = None
        self.session = None
        self._last_screen = ""
        self.device = device
        return await self.start()

    async def close(self) -> None:
        """Always run this. `DevicePool.acquire` has no cleanup of its own on
        cancellation, so a cancelled start can leave a runner holding the
        device and the next run times out waiting for it."""
        if self._pool is not None:
            try:
                await self._pool.release_all()
            except Exception as exc:
                self.sink.emit(Failed(where="release", message=str(exc)))

    # -- running a goal ----------------------------------------------------

    async def run(
        self,
        goal: str,
        *,
        approve: Approver | None = None,
        max_steps: int | None = None,
    ) -> Outcome:
        session = self.session
        assert session is not None, "call start() before run()"

        # A halt from the previous goal, or loop detection that fired at the
        # end of it, would otherwise make this session permanently dead.
        session.resume()

        backend = EventBackend(SessionBackend(session, Verifier()), self.sink)
        backend.last_screen = self._last_screen

        self.sink.emit(GoalStarted(goal=goal, model=self.agent.describe()))
        started = time.monotonic()
        try:
            outcome = await run_goal(
                session,
                goal,
                model=self._model,
                backend=backend,
                settings=self.agent,
                approve=self._watched(approve),
                max_steps=max_steps,
            )
        except Exception as exc:
            self.sink.emit(Failed(where="run", message=str(exc)))
            raise
        finally:
            self._last_screen = backend.last_screen

        self.sink.emit(
            GoalFinished(
                goal=goal,
                succeeded=outcome.succeeded,
                summary=outcome.summary,
                stopped_because=outcome.stopped_because,
                steps=outcome.steps,
                approvals_asked=outcome.approvals_asked,
                stats=StatsSnapshot.of(outcome.stats),
                prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                elapsed_s=time.monotonic() - started,
            )
        )
        return outcome

    def stop(self, reason: str = "you asked it to stop") -> None:
        """End the run at the next node boundary.

        Synchronous, so a key handler can call it directly. It is not a
        cancellation: `SessionBackend.stop_reason` reads the halt and the
        graph routes to END after the current node, so `run_goal` returns
        normally with a complete outcome and real numbers. The cost is that an
        action already in flight finishes.
        """
        if self.session is not None:
            self.session.halt(reason)
        self.sink.emit(Stopping(reason=reason))

    # -- internals ---------------------------------------------------------

    def _watched(self, approve: Approver | None) -> Approver | None:
        """Announce the question, then ask it.

        Wrapping rather than emitting inside the approver keeps every consumer
        from having to remember to do it, and keeps the record of *being asked*
        separate from the answer: a run that stopped to ask and never got an
        answer is a thing that happened.
        """
        if approve is None:
            return None

        async def ask(request: dict[str, object]) -> bool:
            self.sink.emit(ApprovalAsked(request=dict(request)))
            return await approve(request)

        return ask
