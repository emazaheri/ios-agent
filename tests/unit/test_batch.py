"""The rule that decides where a multi-call turn stops.

Kept apart from the loop test because it is a pure function over six fields
and deserves to be read as a table. The loop test asserts that the graph
*obeys* it against a real device; this one asserts what it says.
"""

from __future__ import annotations

import pytest
from ios_agent.batch import (
    DEVICE_VERBS,
    TERMINATES_SEQUENCE,
    LastAction,
    simulate_turns,
    stop_after,
)


def _ran(verb: str = "tap", *, seq: int = 1, **overrides: object) -> LastAction:
    """A clean, successful action that moved the screen."""
    fields: dict[str, object] = {
        "verb": verb,
        "ok": True,
        "screen_changed": True,
        "alert": False,
        "refused": False,
        "seq": seq,
    }
    fields.update(overrides)
    return LastAction(**fields)  # type: ignore[arg-type]


def _continues(last: LastAction, verb: str | None = None, before: int = 0) -> str | None:
    return stop_after(verb or last.verb, last, before, finished=False, stopped=None)


# -- the ordinary case ------------------------------------------------------


def test_a_clean_navigation_carries_on() -> None:
    """The batch this whole design exists to allow.

    Tap Accessibility, tap Display & Text Size, set Bold Text on. Each target
    exists only because the previous action changed the screen, which is why
    browser-use's abort-on-change rule would have killed it.
    """
    assert _continues(_ran("tap")) is None


def test_observe_ends_a_batch_without_being_judged_as_an_action() -> None:
    """Two things at once, and they pull in opposite directions.

    It records no outcome, so the freshness rule must not fire on it and
    report that a call never reached the device. But calling it at all says
    the screen was unknown, so whatever was queued behind it was chosen
    without the answer, and that is a batch that should not continue.
    """
    reason = stop_after("observe", None, 0, finished=False, stopped=None)
    assert reason is not None
    assert "not known" in reason
    assert "never reached the device" not in reason, "judged as a failed action"


def test_an_unknown_tool_name_is_left_alone() -> None:
    """The graph already answers it, and the rest of the batch may be fine."""
    assert stop_after("teleport", None, 0, finished=False, stopped=None) is None


# -- the rules, one per row -------------------------------------------------


def test_done_stops_the_rest_of_the_turn() -> None:
    """`done` as call 1 of 3 used to leave calls 2 and 3 running."""
    reason = stop_after("tap", _ran(), 0, finished=True, stopped=None)
    assert reason is not None and "`done`" in reason


def test_a_halted_session_stops_the_rest_of_the_turn() -> None:
    """`next_step` checks this between turns; nothing checked it inside one."""
    reason = stop_after("tap", _ran(), 0, finished=False, stopped="the session was halted")
    assert reason is not None and "halted" in reason


def test_a_record_that_did_not_advance_stops_the_batch() -> None:
    """The correctness hole a naive `last_action` would leave.

    `guarded` returns a string when resolution fails, and the backend never
    recorded anything. Without the sequence number the guard would read the
    *previous* action's passing record and wave the rest through.
    """
    stale = _ran(seq=7)
    reason = stop_after("tap", stale, 7, finished=False, stopped=None)
    assert reason is not None and "never reached the device" in reason


def test_a_refusal_stops_the_batch() -> None:
    reason = _continues(_ran("set_value", refused=True))
    assert reason is not None and "refused" in reason


def test_a_failure_stops_the_batch() -> None:
    reason = _continues(_ran("tap", ok=False))
    assert reason is not None and "failed" in reason


def test_an_alert_stops_the_batch() -> None:
    """An alert is always a surprise and always blocks the screen."""
    reason = _continues(_ran("tap", alert=True))
    assert reason is not None and "alert" in reason


def test_a_screen_that_did_not_change_stops_the_batch() -> None:
    reason = _continues(_ran("tap", screen_changed=False))
    assert reason is not None and "never arrived" in reason


@pytest.mark.parametrize("verb", sorted(TERMINATES_SEQUENCE))
def test_an_unpredictable_landing_stops_the_batch(verb: str) -> None:
    """Including `scroll`, which is why the no-change rule cannot stand alone.

    `scroll(until=...)` loops server-side and stops wherever the content did.
    It moves the fingerprint, so every other rule here would let the next call
    through, aimed at a screen the model only guessed at.
    """
    reason = _continues(_ran(verb))
    assert reason is not None and "cannot be predicted" in reason


# -- the accepted false positive --------------------------------------------


def test_setting_a_switch_already_in_that_state_stops_the_batch() -> None:
    """A successful no-op aborts, and the message must not blame the plan.

    `verify.py` documents that "already on" and a dead switch are
    byte-identical in the result. Nothing can separate them here, so the safe
    reading wins and the batch stops. `conditional_cleanup` is this shape.
    """
    reason = _continues(_ran("set_value", screen_changed=False))
    assert reason is not None
    assert "never arrived" in reason
    assert "wrong" not in reason.lower(), "the message blames the model for a correct action"


# -- ordering ---------------------------------------------------------------


def test_a_failure_is_reported_before_an_unpredictable_landing() -> None:
    """A `scroll` that failed should say so, not talk about where it landed."""
    reason = _continues(_ran("scroll", ok=False))
    assert reason is not None and "failed" in reason


# -- the literal agrees with the protocol -----------------------------------


def test_the_device_verbs_are_the_backend_verbs() -> None:
    """`batch.py` imports nothing, so its verb list is a literal.

    This is what keeps the literal honest: a verb added to the backend and not
    here would be executed without ever being guarded.

    The read-only verbs are subtracted rather than ignored, so a new one has to
    be classified as read-only on purpose instead of by forgetting.
    """
    from ios_agent.backend import Backend
    from ios_agent.batch import _ASKED_A_QUESTION

    not_verbs = {"approve", "stop_reason", "stats", "last_screen", "last_action"}
    verbs = set(Backend.__protocol_attrs__) - not_verbs - _ASKED_A_QUESTION
    assert verbs == DEVICE_VERBS


def test_every_terminator_is_a_device_verb() -> None:
    assert TERMINATES_SEQUENCE <= DEVICE_VERBS


# -- the turn floor ---------------------------------------------------------


def test_a_route_with_no_actions_costs_two_turns() -> None:
    """One to observe, one to finish. `read_a_card_answer` is this shape."""
    assert simulate_turns([]) == 2


def test_a_clean_chain_collapses_into_one_turn() -> None:
    """The whole point. Nine actions across three panes, one batch."""
    assert simulate_turns([_ran(seq=i) for i in range(9)]) == 3


def test_a_terminator_splits_the_route() -> None:
    """`open_url` mid-route costs a turn, because nothing may follow it."""
    route = [_ran("tap", seq=1), _ran("open_url", seq=2), _ran("tap", seq=3)]
    assert simulate_turns(route) == 4


def test_a_no_op_splits_the_route() -> None:
    route = [_ran("set_value", seq=1, screen_changed=False), _ran("tap", seq=2)]
    assert simulate_turns(route) == 4


def test_a_terminator_in_last_place_costs_nothing_extra() -> None:
    """Nothing follows it, so there is no batch to abort."""
    assert simulate_turns([_ran("tap", seq=1), _ran("scroll", seq=2)]) == 3


def test_a_batch_stops_after_a_find() -> None:
    """Asking where something is means the rest of the turn was chosen blind.

    Same argument as `observe`, and it has to be the same answer: a find that
    reports a hidden match changes what the next call should be, so a call
    queued behind it was decided without the one fact it asked for.
    """
    reason = stop_after("find", None, before_seq=0, finished=False, stopped=None)
    assert reason is not None
    assert "`find` was called" in reason
