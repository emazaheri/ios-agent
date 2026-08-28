#!/usr/bin/env python3
"""Render the terminal app to PNGs, so it can be looked at rather than guessed at.

    uv run python scripts/tui_screenshot.py            # every shape
    uv run python scripts/tui_screenshot.py narrow

This exists because a passing test suite says nothing about what a TUI looks
like. Seven bugs have been found by looking at these images, none of which
failed a test:

- the entire transcript pane was empty, because `LogPane` subclassed
  `Transcript` and `query_one(Transcript)` returned the hidden log pane, so
  every line written went somewhere invisible;
- the "screen is stale" header turned the whole digest yellow, because
  `Text(a, style=...) + Text(b)` carries the first operand's style onto the
  result;
- the log pane was empty when opened, because a `display: none` RichLog has no
  width and drops what it is told to write;
- the transcript's fixed 38-column target field was wider than the pane on a
  narrow terminal, so every row wrapped and the tail of a refusal appeared
  alone in orange reading "reached the device";
- the refusal marker was tacked on the end of a row, so it was the first thing
  truncated: the stats bar said nine refused while no row showed which;
- the status bar put the state last, so a narrow terminal cut the one field
  saying whether anything was happening and kept the device name;
- the screen pane rendered blank before the first read, which looks the same as
  a screen with nothing on it;
- the approval modal was fixed at 70 columns and painted three columns past the
  edge of a 64-column terminal, clipping the sentence saying why the action was
  flagged. That one is a safety bug rather than a cosmetic one: approving an
  action whose justification ran off the screen is consent to something nobody
  read.

None is detectable by asserting on widget state, which is what the tests do.
All are obvious in a picture. `tests/tui/test_layout.py` keeps the last five,
and each of those was checked by reintroducing the bug and confirming the test
fails: three of them passed against the bug they were written for on the first
attempt, because they asserted on the wrong box (`size` excludes the border) or
on stored text rather than on what a pane can actually show.

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
from ios_tui.approval import ApprovalModal  # noqa: E402
from ios_tui.events import DeviceReady, Failed, Progress  # noqa: E402
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


#: Twelve scrolls, of which the verifier refuses the last nine. The state that
#: showed the layout breaking: a long history, a narrow pane, and a marker that
#: has to survive both.
SCROLLING = (
    [[("observe", {})]]
    + [[("scroll", {"direction": "down"})]] * 12
    + [[("done", {"succeeded": True, "summary": "Reached the end of the list."})]]
)


class _Stub(GoalRunner):
    """A runner over the scripted phone. No pool, no device, no model."""

    def __init__(self, sink: Any, model: DeviceModel, script: list[Any] = BOLD_TEXT) -> None:
        session, _, _ = build_session(model, settings())
        super().__init__(sink, settings(), model=ScriptedModel(script))
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
    script: list[Any] = BOLD_TEXT,
    after: Callable[[IosAgentApp, Any], Awaitable[None]] | None = None,
) -> None:
    model = DeviceModel()
    app = IosAgentApp(
        lambda sink: _Stub(sink, model, script), goal=goal, manual=manual, inline=inline
    )
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
            while app._busy:
                await asyncio.sleep(0.02)
    await pilot.pause()


async def _open_the_log(app: IosAgentApp, pilot: Any) -> None:
    await pilot.press("ctrl+l")
    await pilot.pause()


async def _ask_for_approval(app: IosAgentApp, pilot: Any) -> None:
    app.run_worker(
        app.push_screen_wait(
            ApprovalModal(
                {
                    "action": "tap",
                    "signature": "tap:Delete All",
                    "reason": "tap on 'Delete All' matches the destructive rule 'delete'",
                    "matched": "delete",
                    "goal": "clear my messages",
                }
            )
        ),
        group="screenshot",
    )
    async with asyncio.timeout(10):
        while not isinstance(app.screen, ApprovalModal):
            await asyncio.sleep(0.02)
    await pilot.pause()


async def _pick_a_device(app: IosAgentApp, pilot: Any) -> None:
    from ios_tui.devices import DevicePicker

    picker = DevicePicker(settings())
    app.push_screen(picker)
    await pilot.pause()
    async with asyncio.timeout(60):
        while not picker.devices:
            await asyncio.sleep(0.05)
    await pilot.pause()


async def _slash(app: IosAgentApp, pilot: Any) -> None:
    from textual.widgets import Input

    app.query_one("#goal-input", Input).value = "/"
    await pilot.pause()


async def _slash_filtered(app: IosAgentApp, pilot: Any) -> None:
    from textual.widgets import Input

    app.query_one("#goal-input", Input).value = "/s"
    await pilot.pause()


async def _fail(app: IosAgentApp, pilot: Any) -> None:
    app._apply(
        Failed(
            where="acquire",
            message=(
                "Cannot reach WebDriverAgent at http://127.0.0.1:8100. "
                "The runner process is not listening. It usually needs restarting."
            ),
        )
    )
    await pilot.pause()


SHAPES = {
    "fullscreen": lambda: capture("fullscreen", goal="Turn on Bold Text."),
    "manual": lambda: capture("manual", manual=True, after=_type_a_few),
    "inline": lambda: capture("inline", size=(120, 20), inline=True, goal="Turn on Bold Text."),
    "log": lambda: capture("log", goal="Turn on Bold Text.", after=_open_the_log),
    "approval": lambda: capture("approval", after=_ask_for_approval),
    "failure": lambda: capture("failure", after=_fail),
    "commands": lambda: capture("commands", goal="Turn on Bold Text.", after=_slash),
    "commands-filtered": lambda: capture(
        "commands-filtered", goal="Turn on Bold Text.", after=_slash_filtered
    ),
    # Reads the real device list, so this one needs a machine with devices.
    "picker": lambda: capture("picker", after=_pick_a_device),
    # The two that found the layout bugs: a long history in a small terminal.
    "narrow": lambda: capture(
        "narrow", size=(64, 24), goal="Scroll to the bottom.", script=SCROLLING
    ),
    "refusals": lambda: capture(
        "refusals", goal="Scroll to the bottom of the contacts list.", script=SCROLLING
    ),
}


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
