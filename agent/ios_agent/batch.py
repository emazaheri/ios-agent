"""When a turn asks for several actions, where to stop running them.

A model may return more than one tool call in a turn, and the graph has always
executed all of them in order. Nothing decided whether the second one still
made sense after the first had run. This module is that decision, kept apart
from the graph so it can be read and tested as a rule rather than as control
flow.

## Why this cannot be browser-use's rule

`browser-use` guards the same thing by comparing the page URL and focused
target before and after each action, and aborting the rest of the queue on any
change (`agent/service.py:2729`). That is right for a browser, where the
valuable batch is *same-page*: fill a field, fill another, submit. A navigation
mid-batch means the remaining actions were written against a page that is gone.

On iOS the valuable batch is the opposite. It is *navigational*: tap
Accessibility, tap Display & Text Size, set Bold Text on. Each target exists
only because the previous action changed the screen. Aborting on change would
abort exactly the batches worth having, and would leave the guard firing on
every case it was supposed to allow.

So the rule is inverted. A screen that changed is the expected case and the
batch continues. A screen that did **not** change is the abort, because a link
that did not move means every later link is aimed at a screen that never
arrived.

## The price of that inversion

`set_value` onto a control already in the requested state is a successful
no-op, and `verify.py` documents that it is byte-identical to a dead switch:
`ok=True`, `screen_changed=False`, an empty delta and no digest. Nothing in the
result separates "already done" from "will not move". So a correct, successful
action stops the batch, and `conditional_cleanup` is exactly that shape.

That is accepted. The cost is one wasted turn and never a wrong action, which
is the right side of the trade when CLAUDE.md names acting on the wrong control
as the worst failure this system has. It does mean the message handed to the
skipped calls has to say the screen did not arrive rather than implying the
plan was wrong, or a model will discard a route that was fine.

## Freshness, and why a `seq` is needed

Three paths return a string to the graph without the backend ever acting:
`tools.guarded` catches `IosAutomationError` and returns the message,
`tools._ask_a_human` returns a refusal when the operator says no, and the graph
itself answers an unknown tool name. In all three the backend's `_act` never
ran, so its record of the last action still describes the *previous* one. A
guard reading that record would see a passing action that already happened and
wave the rest of the batch through.

Hence `seq`, bumped every time a backend records an outcome. The caller
snapshots it before the call and a value that did not move means the call never
reached the device, whatever string came back. Stamping the tool call id
instead does not work: `open_url` and `open_app` take no idempotency key, so
there is no id at the backend to stamp.

## What terminates a sequence outright

Four verbs land somewhere that cannot be predicted from the screen the model
was looking at when it planned the batch, so nothing may be chained behind
them. `open_url` and `press_button` are obvious. `scroll` is the interesting
one: `scroll(until=...)` loops server-side and stops wherever the content did,
and because it *does* move the fingerprint the no-change rule would happily let
the next call through, aimed at a screen the model only guessed at.

`open_app` belongs here too and is listed, since the tool became callable again
in the commit before this one.

`observe` ends a batch as well, for the opposite reason and so handled
separately: it is not an action and records nothing, but calling it says the
screen was unknown, and everything queued behind it was therefore chosen
without the answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Verbs after which nothing may be chained, because where they land is not
#: predictable from the screen the batch was planned against. `scroll` is here
#: even though it changes the screen, which is the whole reason the no-change
#: rule cannot stand alone.
TERMINATES_SEQUENCE = frozenset({"open_app", "open_url", "press_button", "scroll"})

#: Verbs that touch the device, and so are expected to advance the backend's
#: record. `observe` and `find` are deliberately absent: they record no
#: outcome, so the freshness check would abort on them for the wrong reason.
#: Both end a batch all the same, handled separately in `stop_after`, because a
#: turn that had to ask what was on screen did not know it when it chose the
#: rest, and the same is true of one that had to ask where something was.
#: `done` is absent because the run being finished is checked before any of
#: this. Anything else, including a name the model invented, is left alone; the
#: graph already answers it and the rest of the batch may still be valid.
#:
#: Kept as a literal rather than derived from `Backend`, because this module
#: imports nothing. `tests/unit/test_batch.py` asserts the two agree.
DEVICE_VERBS = frozenset(
    {"tap", "type_text", "set_value", "scroll", "press_button", "open_app", "open_url"}
)

#: Read-only verbs. They cost a device round trip but record no outcome, so
#: they end a batch without going through the freshness check.
_ASKED_A_QUESTION = frozenset({"observe", "find"})


@dataclass(frozen=True, slots=True)
class LastAction:
    """What the backend recorded about the action it just ran.

    Both backends build one of these from their own shape, an `ActionResult`
    for the direct path and a JSON payload over MCP, for the same reason
    `verify.Outcome` is a protocol: a guard that judged the two transports
    differently would make the comparison between them a measurement of the
    guard.
    """

    verb: str
    ok: bool
    screen_changed: bool
    alert: bool
    refused: bool
    #: Bumped on every recorded outcome. See the freshness note above.
    seq: int


def stop_after(
    verb: str,
    last: LastAction | None,
    before_seq: int,
    *,
    finished: bool,
    stopped: str | None,
) -> str | None:
    """Decide whether the rest of a turn's calls should still run.

    `None` means carry on. Anything else is the sentence the skipped calls are
    handed, so it is written for a model to read and act on, not for a log.
    """
    if finished:
        return "the run finished when `done` was called."
    if stopped is not None:
        return f"the session stopped: {stopped}."
    if verb in _ASKED_A_QUESTION:
        # Calling one means the screen, or where something on it was, was
        # unknown, so anything queued behind it was chosen without the answer.
        # Cheap to get wrong in both directions, and the operator prompt
        # already says to ask only when you genuinely do not know, so the
        # strict reading costs a turn the model should not have been spending
        # anyway.
        return (
            f"`{verb}` was called, so the screen was not known when the rest "
            "of this turn was chosen."
        )
    if verb not in DEVICE_VERBS:
        return None
    if last is None or last.seq == before_seq:
        return (
            "the previous call never reached the device, so the screen it "
            "would have produced never arrived."
        )
    return _after_a_recorded_action(last)


def _after_a_recorded_action(last: LastAction) -> str | None:
    """The rules that read a fresh record, shared with `simulate_turns`."""
    if last.refused:
        return f"`{last.verb}` was refused before it reached the device."
    if not last.ok:
        return f"`{last.verb}` failed, so the screen it would have produced never arrived."
    if last.alert:
        return "an alert appeared and is blocking the screen."
    if not last.screen_changed:
        return (
            f"`{last.verb}` reported no change on screen, so this was aimed at a "
            "screen that never arrived."
        )
    if last.verb in TERMINATES_SEQUENCE:
        return f"`{last.verb}` lands somewhere that cannot be predicted from the previous screen."
    return None


def simulate_turns(outcomes: Sequence[LastAction]) -> int:
    """How many model turns a perfect batcher would need for this run.

    Used to give `turn_floor` a value derived from the guard rather than
    hand-written beside it. Feed it the outcomes an oracle run recorded, in
    order, and it splits them wherever `stop_after` would have aborted.

    Two turns are added that carry no action: the opening `observe`, which
    cannot be batched with anything because the point of it is that the screen
    is unknown, and the closing `done`. Both are charged separately on purpose,
    which makes this a slightly conservative ceiling rather than an optimistic
    one.

    It assumes a model that already knows the whole route and batches
    maximally, and that batching does not change which actions get chosen. It
    therefore measures the headroom the guard leaves, not the model.
    """
    batches = 0
    open_batch = False
    for last in outcomes:
        if not open_batch:
            batches += 1
            open_batch = True
        if _after_a_recorded_action(last) is not None:
            open_batch = False
    return 1 + batches + 1
