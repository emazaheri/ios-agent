#!/usr/bin/env python3
"""Render the terminal app to PNGs, so it can be looked at rather than guessed at.

    uv run python scripts/tui_screenshot.py            # all three shapes
    uv run python scripts/tui_screenshot.py fullscreen

This exists because a passing test suite says nothing about what a TUI looks
like. The first time these images were generated they showed two bugs that
every test was green through:

- the entire transcript pane was empty, because `LogPane` subclassed
  `Transcript` and `query_one(Transcript)` returned the hidden log pane, so
  every line written went somewhere invisible;
- the "screen is stale" header turned the whole digest yellow, because
  `Text(a, style=...) + Text(b)` carries the first operand's style onto the
  result.

Neither is detectable by asserting on widget state, which is what the tests do.
Both are obvious in a picture.

It runs against the scripted device, so it needs no simulator, no model and no
API key, and takes about a second.

Requires `rsvg-convert` (`brew install librsvg`) for the PNG step; the SVGs are
written either way and open in any browser.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
# The scripted phone and the scripted model live beside the tests that use
# them, which is also where the eval harness keeps them.
sys.path[:0] = [
    str(_REPO / "tests"),
    str(_REPO / "tests" / "evals"),
    str(_REPO / "tests" / "evals" / "agent"),
    str(_REPO / "tests" / "tui"),
]

from ios_tui.app import IosAgentApp  # noqa: E402
from ios_tui.events import DeviceReady, Progress  # noqa: E402
from ios_tui.runner import GoalRunner  # noqa: E402
from ios_tui.widgets import StatsBar, StatusBar  # noqa: E402
from screens import DeviceModel, build_session  # noqa: E402
from tui_harness import ScriptedModel, settings  # noqa: E402

OUT = _REPO / ".artifacts" / "tui"

#: The route every other test uses, so the picture and the numbers agree.
BOLD_TEXT = [
    [("observe", {})],
    [("tap", {"target": "Accessibility"})],
    [("tap", {"target": "Display & Text Size"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on."})],
]


class _Stub(GoalRunner):
    """A runner over the scripted phone. No pool, no device, no model."""

    def __init__(self, sink: Any, model: DeviceModel) -> None:
        session, _, _ = build_session(model, settings())
        super().__init__(sink, settings(), model=ScriptedModel(BOLD_TEXT))
        self.session = session

    async def start(self) -> Any:
        self.sink.emit(Progress(text="Booting simulator iPhone 17"))
        self.sink.emit(
            DeviceReady(
                lease={"device": {"name": "iPhone 17", "os_version": "26.5", "kind": "simulator"}}
            )
        )
        return self.session

    async def close(self) -> None:
        return None


async def capture(
    name: str,
    *,
    size: tuple[int, int] = (120, 38),
    inline: bool = False,
    manual: bool = False,
    goal: str | None = None,
    after: Callable[[IosAgentApp, Any], Awaitable[None]] | None = None,
) -> None:
    model = DeviceModel()
    app = IosAgentApp(lambda sink: _Stub(sink, model), goal=goal, manual=manual, inline=inline)
    async with app.run_test(size=size) as pilot:
        async with asyncio.timeout(30):
            while app.query_one(StatusBar).state == "starting":
                await asyncio.sleep(0.05)
        if goal:
            async with asyncio.timeout(30):
                while not app.query_one(StatsBar).elapsed_s:
                    await asyncio.sleep(0.05)
        if after is not None:
            await after(app, pilot)
        await pilot.pause()

        OUT.mkdir(parents=True, exist_ok=True)
        svg = OUT / f"{name}.svg"
        svg.write_text(app.export_screenshot(title=f"ios-agent {name}"))
        png = OUT / f"{name}.png"
        try:
            subprocess.run(
                ["rsvg-convert", "-w", "1600", str(svg), "-o", str(png)],
                check=True,
                capture_output=True,
            )
            print(f"  {png.relative_to(_REPO)}")
        except (OSError, subprocess.CalledProcessError):
            print(f"  {svg.relative_to(_REPO)}  (install librsvg for a PNG)")


async def _type_a_few(app: IosAgentApp, pilot: Any) -> None:
    for line in ("observe", "tap Accessibility"):
        app.submit(line)
        async with asyncio.timeout(30):
            while app._run_worker is not None:
                await asyncio.sleep(0.02)
    await pilot.pause()


SHAPES = {
    "fullscreen": lambda: capture("fullscreen", goal="Turn on Bold Text."),
    "manual": lambda: capture("manual", manual=True, after=_type_a_few),
    "inline": lambda: capture("inline", size=(120, 20), inline=True, goal="Turn on Bold Text."),
    "log": lambda: capture("log", goal="Turn on Bold Text.", after=_open_the_log),
}


async def _open_the_log(app: IosAgentApp, pilot: Any) -> None:
    await pilot.press("ctrl+l")
    await pilot.pause()


async def main() -> int:
    wanted = sys.argv[1:] or list(SHAPES)
    unknown = [name for name in wanted if name not in SHAPES]
    if unknown:
        print(f"unknown shape(s): {', '.join(unknown)}. Try: {', '.join(SHAPES)}")
        return 2
    for name in wanted:
        await SHAPES[name]()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
