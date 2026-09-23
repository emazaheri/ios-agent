"""What each action is, as data rather than as prose in a contributing guide.

Four facts about every action have been load-bearing since the first commit and
were written down only in prose, where a human was asked to remember them:
which actions resolve an element before acting, which record themselves in the
audit trail, which accept an idempotency key, and which a client must gate.

Prose cannot be asserted. The entry below is checked against the source of
`IosSession` itself in `tests/unit/test_action_catalog.py`, so adding an action
that skips `_finish`, or one that quietly drops its idempotency key, fails a
test naming it rather than surviving until someone reads the trail and finds
most of a session missing from it.

Note that an action's *name* and the method implementing it are not one to
one. The name is what reaches the policy gate, the audit trail and the
idempotency cache, and `IosSession.type_text` and `type_secret` both record
themselves as `type`, while `open_app` finishes as `launch_app`. The table is
keyed by method, because that is the unique half.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One action, and the facts every layer above it needs to know."""

    #: What the policy gate, the audit trail and the idempotency cache call it.
    name: str
    #: The `IosSession` coroutine that implements it.
    method: str
    #: Reads the device and changes nothing on it. `wait_for` counts: it polls
    #: until a screen arrives and never touches the device, which is why it is
    #: here rather than among the mutating actions despite returning an
    #: `ActionResult` like the rest.
    read_only: bool
    #: Goes through `IosSession._act`, which resolves a target, gates it,
    #: acts, settles and re-observes. The rest do their own thing and reach
    #: `_finish` directly, because they have no single element to resolve.
    routes_through_act: bool
    #: Accepts `idem_key`, making a replayed agent node safe. Absent on the
    #: actions where a repeat is already harmless: launching an app that is
    #: running, waiting for a screen that has arrived, dismissing an alert
    #: that is gone.
    takes_idem_key: bool
    #: What the MCP layer advertises as `destructiveHint`: this may do
    #: something a person cannot undo, so a client should ask first.
    #:
    #: Deliberately *not* the same question as "will the policy gate stop it".
    #: The gate is target-driven: it reads the label of the thing about to be
    #: acted on and matches it against a word list, so a tap on "Erase All
    #: Content" is stopped and a tap on "Wi-Fi" is not, under the same action
    #: name. This flag is the coarser, client-facing claim, and the test holds
    #: it to the annotations rather than to the gate.
    destructive: bool
    #: Nothing may be chained behind this action inside one model turn,
    #: because where it lands cannot be predicted from the screen the turn was
    #: planned against.
    #:
    #: Deliberately *not* browser-use's rule, which aborts a batch whenever the
    #: screen changed (`agent/service.py:2818`). On iOS the valuable batch is
    #: navigational and a changed screen is the expected case, so the guard in
    #: `agent/ios_agent/batch.py` aborts on a screen that did *not* change and
    #: uses this flag for the cases that move the screen somewhere the model
    #: only guessed at. `scroll(until=...)` is the one that needs both: it
    #: stops wherever the content did, so the no-change rule would happily wave
    #: the next call through.
    #:
    #: Read-only verbs are all `False`. `observe` and `find` do end a turn's
    #: remaining calls, but for the other reason, that a turn which had to ask
    #: what was on screen did not know it when it chose the rest, and collapsing
    #: the two would lose the distinction.
    terminates_sequence: bool = False
    #: Implemented by calling another action rather than by acting itself, so
    #: the source check looks at the delegate for routing and audit.
    delegates_to: str | None = None


def _spec(
    name: str,
    method: str,
    *,
    read_only: bool = False,
    act: bool = False,
    idem: bool = False,
    destructive: bool = False,
    term: bool = False,
    delegates_to: str | None = None,
) -> ActionSpec:
    return ActionSpec(
        name=name,
        method=method,
        read_only=read_only,
        routes_through_act=act,
        takes_idem_key=idem,
        destructive=destructive,
        terminates_sequence=term,
        delegates_to=delegates_to,
    )


#: Every action `IosSession` offers, keyed by the method that implements it.
#: Keyed by method rather than by action name because the two are not one to
#: one: `type_text` and `type_secret` both record themselves as `type`, and
#: `open_app` finishes as `launch_app`.
CATALOG: dict[str, ActionSpec] = {
    # -- reads. Nothing here changes the device, and nothing is gated. -------
    "observe": _spec("observe", "observe", read_only=True),
    "screenshot": _spec("screenshot", "screenshot", read_only=True),
    "read_text": _spec("read_text", "read_text", read_only=True),
    "find": _spec("find", "find", read_only=True),
    "wait_for": _spec("wait_for", "wait_for", read_only=True),
    # -- acts on one resolved element ---------------------------------------
    "tap": _spec("tap", "tap", act=True, idem=True, destructive=True),
    "type_text": _spec("type", "type_text", act=True, idem=True, destructive=True),
    # Advertised as merely mutating, unlike `type`, because the value never
    # enters a prompt, a tool result or the audit trail. The gate is unaffected
    # either way: it sees the delegate's action name, which is `type`.
    "type_secret": _spec("type", "type_secret", act=True, idem=True, delegates_to="type_text"),
    "set_value": _spec("set_value", "set_value", act=True, idem=True, destructive=True),
    "press_button": _spec("press_button", "press_button", act=True, idem=True, term=True),
    # -- gestures and app control. No single element to resolve, so these do
    # their own work and record it in `_finish` themselves. -----------------
    "scroll": _spec("scroll", "scroll", idem=True, term=True),
    "swipe": _spec("swipe", "swipe", idem=True, term=True),
    "drag": _spec("drag", "drag", idem=True, term=True),
    # Destructive despite touching nothing itself: the button it presses is
    # the one granting a permission or confirming a deletion.
    "handle_alert": _spec("handle_alert", "handle_alert", destructive=True, term=True),
    "launch_app": _spec("launch_app", "launch_app", term=True),
    "open_app": _spec("launch_app", "open_app", term=True, delegates_to="launch_app"),
    "open_url": _spec("open_url", "open_url", term=True),
}

#: Action names a client may run without asking anyone. Derived here so the
#: policy gate and the MCP annotations cannot drift apart from each other.
READ_ONLY_ACTIONS: frozenset[str] = frozenset(
    spec.name for spec in CATALOG.values() if spec.read_only
)

#: Action names that always want a human in front of them.
DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset(
    spec.name for spec in CATALOG.values() if spec.destructive
)

#: Action names after which nothing may be chained inside one model turn.
#: `agent/ios_agent/batch.py` keeps its own literal of the subset the agent can
#: reach, because that module imports nothing; `tests/unit/test_batch.py` holds
#: the two together.
TERMINATING_ACTIONS: frozenset[str] = frozenset(
    spec.name for spec in CATALOG.values() if spec.terminates_sequence
)
