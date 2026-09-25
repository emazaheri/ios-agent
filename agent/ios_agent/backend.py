"""What the agent is allowed to do to a phone, and what it costs.

Two things live here that look like they belong elsewhere.

**The counters.** The agent's whole design argument is measured in observations
per action, so counting has to happen at the point the call is made, not in a
test wrapper. A backend that reports its own cost is also the honest thing in
production: an agent should be able to say what it spent.

**The rendering.** Every method returns the string the model will read. Keeping
that here rather than in `tools.py` means the tool definitions stay one line
each, and it puts the decision about how much screen to hand back in one place,
which is where the token bill is decided.

The protocol exists so the same loop can run against `IosSession` directly or
across MCP, and the two can be measured against each other. Direct is the
default and the production path; MCP is a demonstration that the server works
for any agent.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ios_agent.batch import LastAction
from ios_agent.verify import Attempt, Verifier, attempt_key
from ios_mcp.actions.result import ActionResult
from ios_mcp.session import IosSession

#: Matches the digest's own estimate and both eval harnesses, so the numbers
#: are comparable without converting between units.
_CHARS_PER_TOKEN = 4


@dataclass
class BackendStats:
    """What this run has cost so far, in the units the design argument uses."""

    observations: int = 0
    #: Read-only tree searches. Counted apart from `observations` because a
    #: find returns no refs and is not a screen, so folding it into the
    #: observation floor would make a task look like it re-read a screen it
    #: never read. What it does cost is a device round trip, which `device_tokens`
    #: and the eval's wall clock both still see.
    finds: int = 0
    actions: int = 0
    device_tokens: int = 0
    #: Calls refused by verification before reaching the device. Counted apart
    #: from `actions` because the device was never touched, and reported so the
    #: waste stays visible: an agent that spends its turns on refused repeats
    #: has traded one failure mode for a quieter one.
    refusals: int = 0
    #: Actions that moved the device, mirrored from the verifier, which is where
    #: the judgement is made.
    #:
    #: The odd one out here: everything else on this object is what the run
    #: cost, and this is what it achieved. It sits here anyway because this is
    #: the only container both backends fill and the front end forwards, and a
    #: verdict built anywhere else would mean one thing over the direct
    #: transport and nothing over MCP.
    changes: int = 0

    @property
    def observation_overhead(self) -> float:
        """Explicit observations per action. One over N is the floor.

        Every action folds the screen it produced into its own result, so an
        agent that never re-reads a screen it was already handed pays a single
        observation for the whole task. On a physical device each avoidable
        one is roughly 300 tokens and 3.7 seconds.
        """
        return self.observations / self.actions if self.actions else float(self.observations)


class Backend(Protocol):
    """The tool surface, narrow on purpose.

    Nine verbs, not the server's thirty-one. A large set of confusable tools
    measurably degrades tool selection, and the server already accounts for
    that; an agent driving the same device does not get a free pass on it.
    """

    stats: BackendStats
    last_screen: str
    #: What the last action recorded, for the guard that decides whether the
    #: rest of a multi-call turn still makes sense. `None` until one has run.
    #: The graph reads it; nothing below the graph does. See `batch.py`.
    last_action: LastAction | None

    async def observe(self) -> str: ...
    async def find(self, text: str) -> str: ...
    async def tap(self, target: str, *, idem_key: str) -> str: ...
    async def type_text(self, text: str, target: str | None, *, idem_key: str) -> str: ...
    async def set_value(self, value: str, target: str, *, idem_key: str) -> str: ...
    async def scroll(self, direction: str, until: str | None, *, idem_key: str) -> str: ...
    async def press_button(self, name: str, *, idem_key: str) -> str: ...
    async def open_url(self, url: str) -> str: ...
    async def open_app(self, name: str) -> str: ...

    def approve(self, signature: str) -> None: ...

    def stop_reason(self) -> str | None: ...


class SessionBackend:
    """Calls `IosSession` in-process. No transport, no serialisation."""

    def __init__(self, session: IosSession, verifier: Verifier | None = None) -> None:
        self.session = session
        self.stats = BackendStats()
        self.last_screen = ""
        self.last_action: LastAction | None = None
        self._seq = 0
        #: Judges each action from the screen it returned. Never re-reads the
        #: screen; a test asserts that.
        self.verifier = verifier or Verifier()

    # -- perception --------------------------------------------------------

    async def observe(self) -> str:
        digest = await self.session.observe()
        self.stats.observations += 1
        self._charge(digest.to_dict())
        self.last_screen = digest.render()
        return self.last_screen

    async def find(self, text: str) -> str:
        """Search the tree, including what the digest dropped.

        Deliberately does not set `last_screen`. A find is not a screen: it
        carries no refs, and the verifier judges an action against the screen
        it produced. Letting a find overwrite that would hand the verifier a
        search result to reason about instead of a screen.
        """
        result = await self.session.find(text)
        self.stats.finds += 1
        self._charge(result.to_dict())
        return result.render()

    # -- actions -----------------------------------------------------------

    async def tap(self, target: str, *, idem_key: str) -> str:
        return await self._act(
            attempt_key("tap", target),
            lambda: self.session.tap(target=target, idem_key=idem_key),
        )

    async def type_text(self, text: str, target: str | None, *, idem_key: str) -> str:
        return await self._act(
            attempt_key("type_text", target, text),
            lambda: self.session.type_text(text, target=target, idem_key=idem_key),
        )

    async def set_value(self, value: str, target: str, *, idem_key: str) -> str:
        return await self._act(
            attempt_key("set_value", target, value),
            lambda: self.session.set_value(value, target=target, idem_key=idem_key),
        )

    async def scroll(self, direction: str, until: str | None, *, idem_key: str) -> str:
        return await self._act(
            attempt_key("scroll", direction, until),
            lambda: self.session.scroll(direction, until=until, idem_key=idem_key),  # type: ignore[arg-type]
        )

    async def press_button(self, name: str, *, idem_key: str) -> str:
        return await self._act(
            attempt_key("press_button", name),
            lambda: self.session.press_button(name, idem_key=idem_key),
        )

    async def open_app(self, name: str) -> str:
        # No idempotency key for the same reason as open_url: launching an app
        # that is already open lands on the same screen.
        return await self._act(attempt_key("open_app", name), lambda: self.session.open_app(name))

    async def open_url(self, url: str) -> str:
        # `IosSession.open_url` takes no idempotency key, so this one cannot be
        # replayed safely by key. Opening the same deep link twice lands on the
        # same screen, which is why it has never needed one.
        return await self._act(attempt_key("open_url", url), lambda: self.session.open_url(url))

    # -- approval ----------------------------------------------------------

    def approve(self, signature: str) -> None:
        """Record a human's yes for one specific action.

        Scoped to a signature rather than to the session, so approving Send
        never approves Delete. The gate enforces that; this only carries the
        answer back to it.
        """
        self.session.approve(signature)

    # -- stopping ----------------------------------------------------------

    def stop_reason(self) -> str | None:
        """Why the session says to stop, if it does.

        Halting and loop detection already exist in the policy layer, and the
        agent reads them rather than reimplementing them. An agent that keeps
        driving a halted session is the failure this guards.
        """
        if self.session.halted:
            return "the session was halted"
        if self.session.looping:
            return "the last few actions kept landing on the same screens"
        return None

    # -- internals ---------------------------------------------------------

    async def _act(self, key: Attempt, call: Callable[[], Awaitable[ActionResult]]) -> str:
        """Gate, act, judge.

        The gate runs before the call is even created, so a refused attempt
        never reaches the device and is not counted as an action. It is counted
        as a refusal instead, which keeps the waste visible rather than making
        a spinning agent look efficient.
        """
        refusal = self.verifier.check(key)
        if refusal is not None:
            self.stats.refusals += 1
            self._record(key[0], ok=False, screen_changed=False, alert=False, refused=True)
            return str(refusal.note)

        result = await call()
        # A replay from the idempotency cache did not touch the device, so it
        # is not another action. It is counted anyway when a resumed node
        # re-runs every call in the turn it was interrupted from, which is the
        # one place this happens and the one place the inflated count would be
        # read as the agent having done more work than it did. Device tokens
        # are still charged below: the payload really does enter the context.
        if not result.from_cache:
            self.stats.actions += 1
        payload = result.to_dict()
        self._charge(payload)
        self._record(
            key[0],
            ok=result.ok,
            screen_changed=result.screen_changed,
            alert=result.alert is not None,
            refused=False,
        )

        verdict = self.verifier.record(key, result)
        self.stats.changes = self.verifier.changes
        rendered = self._render(result, payload)
        return f"{rendered}\n{verdict.note}" if verdict.note else rendered

    def _record(
        self, verb: str, *, ok: bool, screen_changed: bool, alert: bool, refused: bool
    ) -> None:
        """Note what just happened, for the batch guard above the graph.

        The sequence number is the point. Three paths return a string without
        reaching here at all, and a guard that could not tell a fresh record
        from a stale one would read the previous action's success and let the
        rest of the turn run. `batch.py` explains it at length.
        """
        self._seq += 1
        self.last_action = LastAction(
            verb=verb,
            ok=ok,
            screen_changed=screen_changed,
            alert=alert,
            refused=refused,
            seq=self._seq,
        )

    def _render(self, result: ActionResult, payload: dict[str, object]) -> str:
        """Hand back the screen the action produced, not just whether it worked.

        This is the lever the whole design rests on. An agent told only "ok"
        has to spend an observation to find out what happened; an agent handed
        the resulting screen does not.
        """
        lines = [f"{result.action}: {'ok' if result.ok else 'failed'}"]
        if not result.screen_changed:
            lines.append("the screen did not change")
        # Read the note off the payload rather than the object. `to_dict`
        # derives it: a replay from the idempotency cache and a recovered
        # WebDriverAgent both set it there and leave `result.note` empty, and
        # an agent told neither would think a cached call had touched the
        # device.
        note = payload.get("note")
        if note:
            lines.append(str(note))
        if result.alert is not None:
            lines.append(str(payload.get("hint", "an alert is blocking the screen")))
        if result.delta is not None:
            lines.append(result.delta.render())
        if result.digest is not None:
            self.last_screen = result.digest.render()
            lines.append(self.last_screen)
        return "\n".join(lines)

    def _charge(self, payload: object) -> None:
        self.stats.device_tokens += len(json.dumps(payload, default=str)) // _CHARS_PER_TOKEN
