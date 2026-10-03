"""The settle loop that replaces fixed sleeps."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ios_mcp.actions.stabilize import settle, wait_until
from ios_mcp.config import StabilizeSettings
from ios_mcp.perception.digest import Digest


def fast() -> StabilizeSettings:
    return StabilizeSettings(
        min_delay_s=0.0, poll_interval_s=0.001, max_wait_s=0.5, stable_samples=2
    )


@dataclass
class Screens:
    """Yields a scripted series of fingerprints, repeating the last forever."""

    sequence: list[str]
    calls: int = 0
    seen: list[str] = field(default_factory=list)

    async def __call__(self) -> Digest:
        fp = self.sequence[min(self.calls, len(self.sequence) - 1)]
        self.calls += 1
        self.seen.append(fp)
        return Digest(nodes=[], fingerprint=fp)


async def test_settles_once_the_screen_stops_moving() -> None:
    screens = Screens(["a", "b", "c", "c", "c"])
    outcome = await settle(screens, fast())
    assert outcome.settled is True
    assert outcome.digest.fingerprint == "c"


async def test_a_stable_screen_settles_almost_immediately() -> None:
    screens = Screens(["a"])
    outcome = await settle(screens, fast())
    assert outcome.settled is True
    assert outcome.samples <= 3, "a still screen should not be polled repeatedly"


async def test_a_never_settling_screen_gives_up_without_raising() -> None:
    """A spinner animating forever must not hang the agent."""

    class Animating:
        def __init__(self) -> None:
            self.n = 0

        async def __call__(self) -> Digest:
            self.n += 1
            return Digest(nodes=[], fingerprint=f"frame{self.n}")

    outcome = await settle(Animating(), fast())
    assert outcome.settled is False
    assert outcome.elapsed_s >= 0.4


async def test_a_baseline_keeps_polling_while_nothing_has_changed_yet() -> None:
    """A slow transition must not be mistaken for an action that did nothing."""
    screens = Screens(["start", "start", "start", "new", "new", "new"])
    outcome = await settle(screens, fast(), baseline="start")
    assert outcome.digest.fingerprint == "new"
    assert outcome.settled is True


async def test_without_a_baseline_an_unchanged_screen_settles_at_once() -> None:
    screens = Screens(["start"])
    outcome = await settle(screens, fast())
    assert outcome.digest.fingerprint == "start"


async def test_wait_until_returns_as_soon_as_the_predicate_holds() -> None:
    screens = Screens(["a", "b", "target"])
    digest, met = await wait_until(
        screens, lambda d: d.fingerprint == "target", timeout_s=1.0, poll_interval_s=0.001
    )
    assert met is True
    assert digest.fingerprint == "target"


async def test_wait_until_reports_failure_rather_than_raising() -> None:
    screens = Screens(["a"])
    digest, met = await wait_until(
        screens, lambda d: d.fingerprint == "never", timeout_s=0.05, poll_interval_s=0.001
    )
    assert met is False
    assert digest.fingerprint == "a"


# -- settling on frames ---------------------------------------------------------


def frames_mode(**overrides: float) -> StabilizeSettings:
    values: dict = dict(quiet_s=0.02, frame_timeout_s=0.3)
    values.update(overrides)
    return fast().model_copy(update={"signal": "frames", **values})


@dataclass
class Frames:
    """Yields a scripted series of screenshots, repeating the last forever."""

    sequence: list[bytes]
    calls: int = 0
    fail: bool = False

    async def __call__(self) -> bytes:
        if self.fail:
            raise ConnectionError("screenshot failed")
        png = self.sequence[min(self.calls, len(self.sequence) - 1)]
        self.calls += 1
        await asyncio.sleep(0.002)
        return png


class Animating:
    """Every frame is new, like a spinner that never stops."""

    def __init__(self) -> None:
        self.n = 0

    async def __call__(self) -> bytes:
        self.n += 1
        await asyncio.sleep(0.002)
        return f"frame{self.n}".encode()


async def test_quiet_frames_cost_one_tree_read() -> None:
    """The point of the signal: the confirming tree read is the expensive one."""
    screens = Screens(["done"])
    outcome = await settle(screens, frames_mode(), frame=Frames([b"a", b"b", b"c"]))
    assert outcome.via == "frames"
    assert outcome.settled is True
    assert screens.calls == 1
    assert outcome.digest.fingerprint == "done"


async def test_a_blinking_caret_still_reads_as_quiet() -> None:
    """Frames that repeat earlier ones are not movement, or a text field never settles."""
    caret = [b"move1", b"move2", b"on", b"off"] + [b"on", b"off"] * 200
    screens = Screens(["typed"])
    outcome = await settle(screens, frames_mode(), frame=Frames(caret))
    assert outcome.via == "frames"
    assert screens.calls == 1


async def test_frames_that_never_go_quiet_fall_back_to_polling() -> None:
    screens = Screens(["a", "a"])
    outcome = await settle(screens, frames_mode(), frame=Animating())
    assert outcome.via == "tree"
    assert outcome.settled is True
    assert screens.calls >= 2


async def test_a_failing_screenshot_falls_back_to_polling() -> None:
    screens = Screens(["a"])
    outcome = await settle(screens, frames_mode(), frame=Frames([b"x"], fail=True))
    assert outcome.via == "tree"
    assert outcome.settled is True


async def test_quiet_frames_on_the_baseline_screen_keep_polling() -> None:
    """Still frames before the effect has appeared are not a settled screen.

    The phone returned a scroll after two identical frames that preceded the
    swipe rendering at all. The baseline is what catches that case.
    """
    screens = Screens(["start", "start", "start", "new", "new", "new"])
    outcome = await settle(screens, frames_mode(), baseline="start", frame=Frames([b"still"]))
    assert outcome.via == "tree"
    assert outcome.digest.fingerprint == "new"


async def test_a_pause_shorter_than_the_window_is_not_the_end() -> None:
    """Settings search holds still for 0.42s, then animates its results in."""

    class Debounced:
        """Still for 30ms after typing, then the results arrive and stay."""

        def __init__(self) -> None:
            self.started: float | None = None

        def shown(self) -> str:
            loop = asyncio.get_running_loop()
            self.started = self.started or loop.time()
            return "typed" if loop.time() - self.started < 0.03 else "results"

        async def frame(self) -> bytes:
            await asyncio.sleep(0.002)
            return self.shown().encode()

        async def observe(self) -> Digest:
            return Digest(nodes=[], fingerprint=self.shown())

    short = Debounced()
    early = await settle(short.observe, frames_mode(quiet_s=0.01), frame=short.frame)
    assert early.digest.fingerprint == "typed", "a window inside the pause returns too soon"

    long = Debounced()
    outcome = await settle(long.observe, frames_mode(quiet_s=0.05), frame=long.frame)
    assert outcome.via == "frames"
    assert outcome.digest.fingerprint == "results"


async def test_the_tree_signal_never_takes_a_screenshot() -> None:
    frames = Frames([b"a"])
    await settle(Screens(["a"]), fast(), frame=frames)
    assert frames.calls == 0


async def test_frames_mode_without_a_frame_source_polls_the_tree() -> None:
    screens = Screens(["a"])
    outcome = await settle(screens, frames_mode())
    assert outcome.via == "tree"
