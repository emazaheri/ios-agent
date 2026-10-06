"""An action that returned is not an action that worked.

`EventBackend` used to flag a row only when the action raised. Typed text that
reads back as something else does not raise: the session returns `ok=False`
with the screen it produced, and the model is told it failed. The transcript
row was cyan with a timing, exactly like a success, which is the one place a
person watching would have looked.

The other half is the opposite case. A switch already as asked is left alone,
nothing moves, and nothing is wrong. That one was right in the verdict and
invisible on the row.
"""

from __future__ import annotations

import io

from fake_device import make_session
from fake_wda import FakeField
from ios_tui.bus import ListSink
from ios_tui.events import ActionFinished
from ios_tui.printer import Printer
from ios_tui.runner import GoalRunner
from screens import DeviceModel, build_session
from trees import settings_screen
from tui_harness import ScriptedModel, settings

TYPE_HELLO = [
    [("type_text", {"text": "hello world"})],
    [("done", {"succeeded": False, "summary": "the field lost the first characters"})],
]

#: Airplane Mode starts off on the scripted phone, so this is already as asked.
AIRPLANE_OFF = [
    [("set_value", {"value": "off", "target": "Airplane Mode"})],
    [("done", {"succeeded": True, "summary": "Airplane Mode is off"})],
]


async def _typed(field: FakeField) -> ActionFinished:
    session, fake, _ = make_session(settings_screen(), settings())
    fake.focused_field = field
    sink = ListSink()
    runner = GoalRunner(sink, settings(), model=ScriptedModel(TYPE_HELLO))
    runner.session = session
    await runner.run("Type hello world.")
    (finished,) = [e for e in sink.of_type(ActionFinished) if e.verb == "type_text"]
    return finished


async def test_text_that_did_not_land_is_a_failed_row() -> None:
    finished = await _typed(FakeField(drop_leading=4))

    assert finished.failed is True
    # Not an exception, so not `error`: the two are kept apart on purpose.
    assert finished.error == ""
    assert finished.refused is False
    assert "failed" in finished.rendered, "the model was told something else"


async def test_text_that_landed_is_not_failed() -> None:
    finished = await _typed(FakeField())

    assert finished.failed is False


async def test_a_switch_already_as_asked_says_so() -> None:
    session, _, _ = build_session(DeviceModel(), settings())
    sink = ListSink()
    runner = GoalRunner(sink, settings(), model=ScriptedModel(AIRPLANE_OFF))
    runner.session = session

    await runner.run("Turn off Airplane Mode.")

    (finished,) = sink.of_type(ActionFinished)
    assert finished.already is True
    assert finished.failed is False


async def test_a_refusal_is_not_also_a_failure() -> None:
    """A refusal records `ok=False` too, and is already said once."""
    script = [
        [("set_value", {"value": "on", "target": "Airplane Mode"})],
        *[[("set_value", {"value": "on", "target": "Airplane Mode"})] for _ in range(4)],
        [("done", {"succeeded": True, "summary": "on"})],
    ]
    session, _, _ = build_session(DeviceModel(), settings())
    sink = ListSink()
    runner = GoalRunner(sink, settings(), model=ScriptedModel(script))
    runner.session = session

    await runner.run("Turn on Airplane Mode.")

    rows = sink.of_type(ActionFinished)
    assert any(r.refused for r in rows), "nothing was refused, so this proves nothing"
    assert not any(r.refused and r.failed for r in rows)


def _printed(event: ActionFinished) -> str:
    out = io.StringIO()
    Printer(out).emit(event)
    return out.getvalue()


def test_the_plain_printer_says_failed() -> None:
    line = _printed(ActionFinished(verb="type_text", args={"text": "hi"}, failed=True))
    assert "failed" in line


def test_the_plain_printer_says_already() -> None:
    line = _printed(ActionFinished(verb="set_value", args={"target": "Wi-Fi"}, already=True))
    assert "already as asked" in line


def test_the_plain_printer_says_what_raised() -> None:
    """It used to print an errored action with a timing and nothing else."""
    line = _printed(ActionFinished(verb="tap", args={"target": "Nowhere"}, error="no such row"))
    assert "no such row" in line
