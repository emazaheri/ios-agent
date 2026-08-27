"""Driving by hand: the parser, and that it reaches the same backend.

Manual mode exists so a person can debug perception on an app nobody has
pointed this at before, without a model turn between each attempt. What makes
that worth anything is that it is the *same* eight verbs producing the *same*
events and the same counters, so what you learn by hand transfers to what the
agent will do.
"""

from __future__ import annotations

import pytest
from ios_agent import SessionBackend
from ios_tui.bus import ListSink
from ios_tui.events import ActionFinished, Observed
from ios_tui.manual import USAGE, Unknown, parse
from ios_tui.stream import EventBackend
from screens import DeviceModel, build_session
from tui_harness import settings


@pytest.mark.parametrize(
    ("line", "verb"),
    [
        ("observe", "observe"),
        ("o", "observe"),
        ("tap Accessibility", "tap"),
        ("t Accessibility", "tap"),
        ("type hello", "type_text"),
        ("type hello > Search", "type_text"),
        ("set on > Bold Text", "set_value"),
        ("scroll down", "scroll"),
        ("scroll down > Wi-Fi", "scroll"),
        ("press home", "press_button"),
        ("open App-prefs:root=WIFI", "open_url"),
    ],
)
def test_it_parses_the_eight_verbs(line: str, verb: str) -> None:
    assert parse(line).verb == verb


@pytest.mark.parametrize("line", ["", "   ", "nonsense", "tap", "set on", "press", "open", "type"])
def test_anything_else_asks_for_help_rather_than_guessing(line: str) -> None:
    """A mis-parse would tap something. Refusing to guess is the whole point."""
    with pytest.raises(Unknown) as raised:
        parse(line)
    assert str(raised.value) == USAGE


def test_a_target_full_of_punctuation_survives() -> None:
    """Targets are arbitrary UI strings, which is why the split is on `>`.

    Real labels contain commas, quotes and parentheses (`Larger Text, Off` is
    a real Settings label). A quoted grammar would mean escaping exactly the
    strings this exists to let you type quickly.
    """
    assert parse("tap Larger Text, Off (default)").verb == "tap"
    assert parse('set 0.5 > Volume, "media"').verb == "set_value"


async def test_a_typed_command_produces_the_same_events_as_the_agents() -> None:
    """The point of manual mode: same verbs, same seam, same numbers."""
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    sink = ListSink()
    backend = EventBackend(SessionBackend(session), sink)

    await parse("observe").run(backend)
    await parse("tap Accessibility").run(backend)
    await parse("tap Display & Text Size").run(backend)
    await parse("set on > Bold Text").run(backend)

    assert model.switches["bold_text"] is True
    assert [e.verb for e in sink.of_type(ActionFinished)] == ["tap", "tap", "set_value"]
    assert len(sink.of_type(Observed)) == 1
    # The same counters the agent would have produced on the same route.
    assert backend.stats.actions == 3
    assert backend.stats.observations == 1


async def test_two_identical_commands_both_run() -> None:
    """By hand there is no node to replay, so the second is a new intent.

    Every command takes a fresh idempotency key for that reason. Reusing one
    would make the second typed command replay from the cache and report
    success without touching the device.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, settings())
    backend = EventBackend(SessionBackend(session), ListSink())

    await parse("tap Accessibility").run(backend)
    await parse("tap Display & Text Size").run(backend)
    await parse("set on > Bold Text").run(backend)
    await parse("set off > Bold Text").run(backend)

    assert model.switches["bold_text"] is False
    assert backend.stats.actions == 4
