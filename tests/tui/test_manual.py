"""Driving by hand: the parser, and that it reaches the same backend.

Manual mode exists so a person can debug perception on an app nobody has
pointed this at before, without a model turn between each attempt. What makes
that worth anything is that it is the *same* eight verbs producing the *same*
events and the same counters, so what you learn by hand transfers to what the
agent will do.
"""

from __future__ import annotations

import asyncio

import pytest
from ios_agent import SessionBackend
from ios_tui.app import IosAgentApp
from ios_tui.bus import ListSink
from ios_tui.events import ActionFinished, Observed
from ios_tui.manual import Help, Unknown, parse
from ios_tui.runner import GoalRunner
from ios_tui.stream import EventBackend
from ios_tui.widgets import StatusBar
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
def test_anything_else_is_refused_rather_than_guessed_at(line: str) -> None:
    """A mis-parse would tap something. Refusing to guess is the whole point."""
    with pytest.raises(Unknown):
        parse(line)


@pytest.mark.parametrize("line", ["help", "?", "HELP"])
def test_help_is_a_command_and_not_a_mistake(line: str) -> None:
    """It is listed in `USAGE` as a command.

    Typing it used to answer "I do not understand 'help'", which is the tool
    disagreeing with its own documentation, in the least forgivable place to
    do it: someone typing `help` has already said they are lost.
    """
    with pytest.raises(Help):
        parse(line)


@pytest.mark.parametrize(
    ("typo", "meant"),
    [("tpa Wi-Fi", "tap"), ("obserev", "observe"), ("scrol down", "scroll")],
)
def test_a_near_miss_is_offered_the_verb_it_nearly_was(typo: str, meant: str) -> None:
    """One line beats ten. The grammar is what `help` is for, and printing all
    of it for a typo buries the one thing that was actually wrong."""
    with pytest.raises(Unknown) as raised:
        parse(typo)

    assert raised.value.suggestion == meant
    assert meant in str(raised.value)


@pytest.mark.parametrize("line", ["hi", "xyzzy", "turn on bold text"])
def test_nothing_is_suggested_for_something_that_resembles_no_verb(line: str) -> None:
    """Suggesting `press` for `xyzzy` reads as though the tool understood.

    Saying nothing and naming `help` is the honest answer. Note that `hello`
    is not in this list: it is close enough to `help` that offering it is
    right, and someone typing `hello` at a prompt they do not understand is
    exactly who wants to be pointed at `help`.
    """
    with pytest.raises(Unknown) as raised:
        parse(line)

    assert raised.value.suggestion is None
    assert "help" in str(raised.value)


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


# -- the app's side of it --------------------------------------------------


async def test_typing_help_prints_the_grammar_rather_than_an_objection() -> None:
    """The screenshot that prompted this: `help` answered "I do not understand
    'help'" above the very list that names it."""
    from ios_tui.app import IosAgentApp
    from ios_tui.events import DeviceReady
    from ios_tui.runner import GoalRunner
    from ios_tui.widgets import StatusBar

    class _Runner(GoalRunner):
        def __init__(self, sink: object) -> None:
            super().__init__(sink, settings())  # type: ignore[arg-type]

        async def start(self) -> object:
            session, _, _ = build_session(DeviceModel(), settings())
            self.session = session
            self.sink.emit(
                DeviceReady(
                    lease={
                        "device": {
                            "name": "iPhone 17",
                            "os_version": "26.5",
                            "kind": "simulator",
                        }
                    }
                )
            )
            return session

        async def close(self) -> None:
            return None

    app = IosAgentApp(_Runner, manual=True)
    async with app.run_test(size=(110, 30)) as pilot:
        async with asyncio.timeout(10):
            while app.query_one(StatusBar).state != "ready":
                await asyncio.sleep(0.02)

        app.submit("help")
        async with asyncio.timeout(10):
            while app._busy:
                await asyncio.sleep(0.02)
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "do not understand" not in written
        assert "tap <target>" in written, "the grammar was not printed"


def test_the_manual_prompt_asks_for_a_verb_not_a_sentence() -> None:
    """"what should it do?" invites a sentence, and a sentence is the one thing
    manual mode does not take. Asking the wrong question is how someone ends up
    typing "hi" and being told off for it."""
    from ios_tui.widgets import GoalInput

    assert "verb" in GoalInput(manual=True).placeholder
    assert GoalInput().placeholder == "what should it do?"


async def test_a_fast_command_does_not_wedge_the_input() -> None:
    """The bug the `help` screenshot actually exposed.

    `submit` set its busy marker from the worker's return value. A worker that
    finishes quickly, which `help` and every parse error do, cleared the marker
    in its own `finally` *before* that assignment ran, so the stale handle sat
    there forever and every later line was silently ignored. The app looked
    frozen after one keystroke's worth of input.

    Three commands that each fail fast, then a real one: the fourth has to
    reach the device.
    """
    from ios_tui.app import IosAgentApp
    from ios_tui.events import DeviceReady
    from ios_tui.runner import GoalRunner
    from ios_tui.widgets import StatsBar, StatusBar

    class _Runner(GoalRunner):
        def __init__(self, sink: object) -> None:
            super().__init__(sink, settings())  # type: ignore[arg-type]

        async def start(self) -> object:
            session, _, _ = build_session(DeviceModel(), settings())
            self.session = session
            self.sink.emit(
                DeviceReady(
                    lease={
                        "device": {
                            "name": "iPhone 17",
                            "os_version": "26.5",
                            "kind": "simulator",
                        }
                    }
                )
            )
            return session

        async def close(self) -> None:
            return None

    app = IosAgentApp(_Runner, manual=True)
    async with app.run_test(size=(110, 30)) as pilot:
        async with asyncio.timeout(10):
            while app.query_one(StatusBar).state != "ready":
                await asyncio.sleep(0.02)

        for line in ("hi", "help", "frobnicate", "observe"):
            app.submit(line)
            async with asyncio.timeout(10):
                while app._busy:
                    await asyncio.sleep(0.02)
        await pilot.pause()

        assert app.query_one(StatsBar).stats.observations == 1, (
            "the input wedged: a fast command left the app looking busy forever"
        )


# -- when there is no device -----------------------------------------------


class _Broken(GoalRunner):
    """A runner whose device never arrives, which is an ordinary Tuesday.

    A leftover WebDriverAgent, an unbuilt runner bundle, a phone that went to
    sleep: the app stays up and keeps accepting input, so everything below has
    to work with `session` still None.
    """

    def __init__(self, sink: object) -> None:
        super().__init__(sink, settings())  # type: ignore[arg-type]

    async def start(self) -> object:
        from ios_tui.events import Failed

        from ios_mcp.errors import RunnerCrashed

        # The real runner reports before it raises, so the stub does too.
        failure = RunnerCrashed("no WebDriverAgent available for the simulator")
        self.sink.emit(Failed(where="acquire", message=str(failure)))
        raise failure

    async def close(self) -> None:
        return None


async def _broken_app() -> IosAgentApp:
    return IosAgentApp(_Broken, manual=True)


async def test_typing_with_no_device_says_so_instead_of_crashing() -> None:
    """It used to `assert`, which took the whole app down with a traceback
    printed over the transcript that explained what had gone wrong.

    An assert is for an invariant a programmer controls. A failed acquire is
    not one: it is reachable by anyone whose simulator is busy.
    """
    app = await _broken_app()
    async with app.run_test(size=(110, 30)) as pilot:
        async with asyncio.timeout(10):
            while app.query_one(StatusBar).state != "failed":
                await asyncio.sleep(0.02)

        app.submit("tap Wi-Fi")
        async with asyncio.timeout(10):
            while app._busy:
                await asyncio.sleep(0.02)
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "no device is attached" in written
        assert "/device" in written, "an error with no way forward is a dead end"


async def test_help_still_answers_with_no_device() -> None:
    """`help` and a typo are what someone types when they are stuck, and a
    failed acquire is exactly that state. Both used to hit the assert first,
    because it ran before the line was even parsed."""
    app = await _broken_app()
    async with app.run_test(size=(110, 30)) as pilot:
        async with asyncio.timeout(10):
            while app.query_one(StatusBar).state != "failed":
                await asyncio.sleep(0.02)

        for line in ("help", "hi"):
            app.submit(line)
            async with asyncio.timeout(10):
                while app._busy:
                    await asyncio.sleep(0.02)
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "tap <target>" in written, "help did not answer"
        assert "'hi' is not a verb here" in written


async def test_the_input_is_not_wedged_by_a_missing_device() -> None:
    """The refusal takes an early return out of `_one_command`, which is the
    shape that wedged the app once already."""
    app = await _broken_app()
    async with app.run_test(size=(110, 30)) as pilot:
        async with asyncio.timeout(10):
            while app.query_one(StatusBar).state != "failed":
                await asyncio.sleep(0.02)

        for _ in range(3):
            app.submit("tap Wi-Fi")
            async with asyncio.timeout(10):
                while app._busy:
                    await asyncio.sleep(0.02)
        await pilot.pause()

        refusals = "\n".join(line.text for line in app.transcript.lines).count(
            "no device is attached"
        )
        assert refusals == 3, "the input stopped accepting lines after the first refusal"


async def test_an_error_and_its_hint_arrive_in_that_order() -> None:
    """The advice used to land above the problem it was advice about.

    The row travels through the event queue and a direct `note` does not, so
    manual mode's own handler always won the race. The hint rides on the event
    instead, which makes the ordering a property of the data rather than of
    two code paths finishing in the right sequence.
    """
    from ios_tui.app import IosAgentApp
    from ios_tui.events import DeviceReady
    from ios_tui.runner import GoalRunner
    from ios_tui.widgets import StatusBar

    class _Runner(GoalRunner):
        def __init__(self, sink: object) -> None:
            super().__init__(sink, settings())  # type: ignore[arg-type]

        async def start(self) -> object:
            session, _, _ = build_session(DeviceModel(), settings())
            self.session = session
            self.sink.emit(
                DeviceReady(
                    lease={
                        "device": {
                            "name": "iPhone 17",
                            "os_version": "26.5",
                            "kind": "simulator",
                        }
                    }
                )
            )
            return session

        async def close(self) -> None:
            return None

    app = IosAgentApp(_Runner, manual=True)
    async with app.run_test(size=(110, 30)) as pilot:
        async with asyncio.timeout(10):
            while app.query_one(StatusBar).state != "ready":
                await asyncio.sleep(0.02)

        app.submit("tap Nonexistent Control")
        async with asyncio.timeout(10):
            while app._busy:
                await asyncio.sleep(0.02)
        await pilot.pause()

        rows = [line.text for line in app.transcript.lines]
        failed = next(i for i, r in enumerate(rows) if "tap" in r and "Nonexistent" in r)
        hint = next(i for i, r in enumerate(rows) if "ios_observe" in r or "annotate_refs" in r)
        assert hint > failed, "the hint arrived before the problem it explains"

        # And it is said once, not once by the row and once by the handler.
        assert sum("Nothing on screen matches" in r for r in rows) == 1
