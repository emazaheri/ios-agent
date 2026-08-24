"""The settle loop that replaces fixed sleeps."""

from __future__ import annotations

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
