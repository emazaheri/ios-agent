"""What survives a narrow terminal.

Every one of these was found by rendering the app to an image and looking at
it, not by a failing assertion, which is the point: widget state can be
entirely correct while what a person sees is wrong. `scripts/tui_screenshot.py`
is how they were found; these are how they stay found.

64 columns is the width used throughout. It is a split pane inside a small
terminal, which leaves the transcript about 39 columns, and every fixed-width
assumption in the layout broke there.
"""

from __future__ import annotations

import asyncio

import pytest
from ios_tui.app import IosAgentApp
from ios_tui.events import ActionFinished, DeviceReady, Observed, Progress, StatsSnapshot
from ios_tui.runner import GoalRunner
from ios_tui.widgets import ScreenPane, StatusBar
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings

NARROW = (64, 24)
WIDE = (120, 38)


class _Stub(GoalRunner):
    def __init__(self, sink: object, model: DeviceModel) -> None:
        session, _, _ = build_session(model, settings())
        super().__init__(sink, settings(), model=ScriptedModel([]))  # type: ignore[arg-type]
        self.session = session

    async def start(self) -> object:
        self.sink.emit(Progress(text="Booting simulator iPhone 17"))
        self.sink.emit(
            DeviceReady(
                lease={"device": {"name": "iPhone 17", "os_version": "26.5", "kind": "simulator"}}
            )
        )
        return self.session

    async def close(self) -> None:
        return None


def _app() -> IosAgentApp:
    model = DeviceModel()
    return IosAgentApp(lambda sink: _Stub(sink, model))


async def _ready(app: IosAgentApp) -> None:
    async with asyncio.timeout(10):
        while app.query_one(StatusBar).state == "starting":
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("size", [NARROW, WIDE])
async def test_no_transcript_line_is_wider_than_the_pane(size: tuple[int, int]) -> None:
    """A line that overflows wraps, and a wrapped column is unreadable.

    At 64 columns the target column was fixed at 38 against a pane of 39, so
    every action wrapped and the tail of a refusal landed on its own line as an
    orange fragment reading "reached the device".
    """
    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        transcript = app.transcript
        for i in range(12):
            app._apply(
                ActionFinished(
                    verb="set_value",
                    args={"target": "Differentiate Without Colour", "value": "on"},
                    elapsed_ms=3721,
                    stats=StatsSnapshot(actions=i + 1),
                )
            )
        app._apply(Observed(stats=StatsSnapshot(observations=1, device_tokens=1275)))
        await pilot.pause()

        # Two columns of headroom for the scrollbar, which appears once there
        # is history and is not deducted from `size.width`.
        budget = transcript.size.width - 2
        too_wide = [line.text for line in transcript.lines if len(line.text.rstrip()) > budget]
        assert too_wide == [], f"lines wider than {budget} columns:\n" + "\n".join(too_wide)


async def test_a_refusal_stays_visible_when_the_columns_are_squeezed() -> None:
    """It used to be a note tacked on the end, so it was the first thing cut.

    The stats bar would say nine refused while not one transcript row showed
    which. A refusal never reached the device, so it belongs in the verb column
    where nothing can truncate it away.
    """
    app = _app()
    async with app.run_test(size=NARROW) as pilot:
        await _ready(app)
        app._apply(
            ActionFinished(
                verb="scroll",
                args={"direction": "down"},
                refused=True,
                stats=StatsSnapshot(refusals=1),
            )
        )
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "refused" in written


@pytest.mark.parametrize("size", [NARROW, WIDE])
async def test_the_status_bar_never_drops_the_state(size: tuple[int, int]) -> None:
    """The state is the field that says whether anything is happening.

    Laid out left to right it was last, so a narrow terminal cut exactly the
    one thing worth keeping and left the device name intact.
    """
    app = _app()
    async with app.run_test(size=size) as pilot:
        await _ready(app)
        bar = app.query_one(StatusBar)
        bar.model = "openai:gpt-5.6-sol"
        bar.state = "working"
        await pilot.pause()

        rendered = bar.render()
        assert "working" in rendered.plain
        assert rendered.cell_len <= size[0], "the status bar overflowed its row"


async def test_the_screen_pane_says_so_before_anything_has_been_read() -> None:
    """An empty pane and a pane showing an empty screen look the same."""
    app = _app()
    async with app.run_test(size=WIDE) as pilot:
        await _ready(app)
        await pilot.pause()

        assert "nothing read yet" in app.query_one(ScreenPane).displayed_text
