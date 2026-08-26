"""Judging an action from the screen it already returned.

The measured problem this exists for: on a device whose switch accepts a tap,
reports success and never moves, the skeleton retried the same `set_value` up
to 22 times before its step budget stopped it. Every other task ran at 1.08x a
hand-written oracle, so this is the one thing the loop could not do, and it is
not navigation. It could not tell that the device was lying to it.

**Verification here must cost zero observations.** An action already folds the
resulting screen into its response, and the S1 baseline measured the agent
spending exactly one observation per run, the oracle's floor. There is nothing
left to save on that axis, so a verification step that re-reads the screen
would spend the project's scarcest resource to re-learn what it was already
told. That constraint is asserted by a test.

## Why this counts repeats rather than reading values

The obvious design is to check whether the switch reached the value it was
asked for. It cannot be done from the response, and finding that out is what
shaped this module. A dead switch and a switch that was *already in the
requested state* produce byte-identical results:

    dead switch   ok=True  screen_changed=False  delta empty  digest=None
    already on    ok=True  screen_changed=False  delta empty  digest=None

`set_value` is state-aware, so asking for a state the control already holds is
correctly a no-op. One of those two is success and the other is failure, and
nothing in the payload separates them. The value that would separate them
appears only in the rendered screen, and no screen comes back precisely because
nothing changed.

Counting repeats sidesteps the ambiguity instead of trying to resolve it, and
resolves both cases with the same signal:

- Already satisfied: one no-op is all the evidence needed. If the agent tries
  again, being told the attempt has now changed nothing twice points at the
  goal already being met.
- Dead switch: the same message points at a device that will not comply.

Either way the correct next move is to stop repeating, which is exactly what
the escalation says. No values are parsed and no screen is re-read.

## Measured

Against the S1 skeleton, 21 runs on `gpt-5.6-sol`: the dead-switch task fell
from a median of 21 actions to 6, total actions across all seven tasks from 85
to 53, and cost from $1.21 to $0.74, with success unchanged at 21/21 and
observations unchanged at exactly one per run. The other six tasks went from
1.08x the oracle to 0.92x, so nothing was traded away to get it.

One honest caveat about which half of this module did that. `refusals` was
**zero** across all 21 runs: the hard block never fired. The escalating notes
alone produced the drop, because a model told an attempt changed nothing, and
then that it had changed nothing twice, stops repeating and tries something
else. A traced run goes `set_value` -> `tap` -> `open_url`, three different
approaches, then concludes correctly.

The block is therefore an untriggered bound rather than a measured improvement.
It is kept because S1 demonstrated the 22-attempt failure it exists to cap, and
it is unit-tested, but it should not be described as having earned its place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ios_mcp.actions.result import ActionResult

#: Consecutive no-ops on one action before it is refused outright. One can be a
#: slow transition the settle loop called early. Three, after the fingerprint
#: has stopped moving each time, is a device that will not comply. WorldGUI
#: caps its retries at 3 as well, which is corroboration rather than a reason.
DEFAULT_MAX_ATTEMPTS = 3


class Judgement(StrEnum):
    """What the returned screen says about the action that produced it."""

    #: The screen moved. Nothing to say; saying it anyway is noise.
    PROGRESSED = "progressed"
    #: Nothing changed, for the first time. Worth naming, not worth alarm.
    NO_OP = "no_op"
    #: Nothing changed again, from the same action. The agent is repeating
    #: itself and has not noticed.
    REPEATED_NO_OP = "repeated_no_op"
    #: Enough. The next attempt is refused before it reaches the device.
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class Verdict:
    judgement: Judgement
    #: Appended to what the agent is told. None when there is nothing to add.
    note: str | None = None

    @property
    def allows_acting(self) -> bool:
        return self.judgement is not Judgement.EXHAUSTED


#: Identifies one intent. The value matters: `set_value("on")` and
#: `set_value("off")` on one switch are opposite requests, and counting them
#: together would refuse a legitimate correction after a mistaken one.
Attempt = tuple[str, str, str]


def attempt_key(action: str, target: str | None, argument: str | None = None) -> Attempt:
    return (action, (target or "").strip().lower(), (argument or "").strip().lower())


@dataclass
class Verifier:
    """Remembers which attempts have already done nothing.

    Counting is per attempt rather than globally consecutive: navigating away
    and back does not clear the record, because the question is whether *this
    exact request* has ever worked, not whether something else happened in
    between. A single success clears it, since a control that moved once may
    legitimately be asked again.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    _no_ops: dict[Attempt, int] = field(default_factory=dict)

    def check(self, key: Attempt) -> Verdict | None:
        """Called before touching the device. None means go ahead.

        Refusing here rather than advising afterwards is deliberate. Advice the
        model may ignore leaves the failure mode intact, and the whole point of
        the slice is to bound it.
        """
        if self._no_ops.get(key, 0) < self.max_attempts:
            return None
        action, target, _ = key
        return Verdict(
            Judgement.EXHAUSTED,
            f"Not run. {action} on {target!r} has already been tried "
            f"{self.max_attempts} times and changed nothing each time. Repeating it "
            f"will not work: either this device will not do it, or it is already "
            f"done. Do something else, or finish with `done` and say which it was.",
        )

    def record(self, key: Attempt, result: ActionResult) -> Verdict:
        """Judge a completed action from what it returned."""
        if self._changed(result):
            self._no_ops.pop(key, None)
            return Verdict(Judgement.PROGRESSED)

        seen = self._no_ops[key] = self._no_ops.get(key, 0) + 1
        if seen == 1:
            return Verdict(
                Judgement.NO_OP,
                "Nothing on screen changed, so this may not have done anything. "
                "Check the screen before assuming it worked.",
            )

        remaining = self.max_attempts - seen
        closing = (
            f"{remaining} more attempt(s) will be allowed before it is refused."
            if remaining > 0
            else "Further attempts will be refused."
        )
        return Verdict(
            Judgement.REPEATED_NO_OP,
            f"Nothing changed again. That is {seen} attempts at this with no effect, "
            f"so repeating it is not going to work. Either this device will not do it, "
            f"or it was already done before you started. {closing}",
        )

    @staticmethod
    def _changed(result: ActionResult) -> bool:
        """Did the device move?

        `screen_changed` is the fingerprint comparison and is the primary
        signal. A full digest means the screen was replaced outright rather
        than diffed, which is a navigation and therefore a change. A
        non-empty delta means something moved even when the fingerprint
        rounding did not register it.
        """
        if result.screen_changed or result.digest is not None:
            return True
        return result.delta is not None and not result.delta.empty
