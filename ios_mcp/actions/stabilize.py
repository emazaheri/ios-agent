"""Wait for the screen to stop moving after an action.

Fixed sleeps are the classic source of flaky UI automation: too short and the
next observation catches a half-finished transition, too long and every step
pays for the worst case. Polling the screen fingerprint until it repeats costs
the minimum each time and adapts to whatever the app is actually doing.

Proving a repeat costs a second tree read, though, and a tree read is the
expensive call: 1.3 to 1.8s on a phone against 0.13 to 0.17s for a screenshot.
With ``signal="frames"`` the loop watches screenshots instead and reads the
tree once, after the frames have stopped producing anything new. See ADR 0019.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ios_mcp.config import StabilizeSettings
from ios_mcp.perception.digest import Digest

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SettleOutcome:
    digest: Digest
    settled: bool
    samples: int
    elapsed_s: float
    #: What decided the screen had settled: ``"frames"`` when the screenshots
    #: did and one tree read followed, ``"tree"`` when the polling loop did.
    via: str = "tree"


async def settle(
    observe: Callable[[], Awaitable[Digest]],
    settings: StabilizeSettings,
    *,
    baseline: str | None = None,
    frame: Callable[[], Awaitable[bytes]] | None = None,
) -> SettleOutcome:
    """Wait for the screen to stop moving, then return what it shows.

    ``baseline`` is the fingerprint before the action. When it is supplied the
    loop keeps going while the screen still matches it, so an action whose
    effect is slow to appear is not mistaken for one that did nothing.

    ``frame`` takes a screenshot. It is used only when ``settings.signal`` is
    ``"frames"``, and every way the frames can fail to answer, whether they
    never go quiet, the capture raises, or the one tree read still matches the
    baseline, falls through to the polling loop rather than guessing.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + settings.max_wait_s

    if settings.min_delay_s > 0:
        await asyncio.sleep(settings.min_delay_s)

    if settings.signal == "frames" and frame is not None:
        if await _frames_go_quiet(frame, settings):
            digest = await observe()
            if baseline is None or digest.fingerprint != baseline:
                return SettleOutcome(digest, True, 1, loop.time() - started, via="frames")
        else:
            logger.debug("Frames never went quiet; falling back to tree polling")

    digest = await observe()
    samples = 1
    stable_run = 1
    last = digest.fingerprint

    while loop.time() < deadline:
        unchanged_from_baseline = baseline is not None and digest.fingerprint == baseline
        if stable_run >= settings.stable_samples and not unchanged_from_baseline:
            return SettleOutcome(digest, True, samples, loop.time() - started)

        await asyncio.sleep(settings.poll_interval_s)
        digest = await observe()
        samples += 1
        stable_run = stable_run + 1 if digest.fingerprint == last else 1
        last = digest.fingerprint

    settled = stable_run >= settings.stable_samples
    if not settled:
        logger.debug("Screen never settled within %.1fs", settings.max_wait_s)
    return SettleOutcome(digest, settled, samples, loop.time() - started)


async def _frames_go_quiet(
    frame: Callable[[], Awaitable[bytes]], settings: StabilizeSettings
) -> bool:
    """True once ``quiet_s`` passes without a frame not already seen.

    Only a novel frame counts as movement. A blinking caret cycles through the
    same few frames forever, so "identical to the previous frame" never holds
    for long in a text field, while "nothing new" does. The window has to
    outlast a pause inside a transition: Settings search holds still for 0.42s
    between the last keystroke and its results animating in, and a 0.4s window
    of identical frames returned before them on every run.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.frame_timeout_s
    seen: set[bytes] = set()
    last_new = loop.time()
    while loop.time() < deadline:
        try:
            png = await frame()
        except Exception:  # a capture that fails is no evidence either way
            logger.debug("Screenshot failed during settle", exc_info=True)
            return False
        key = hashlib.blake2b(png, digest_size=16).digest()
        now = loop.time()
        if key not in seen:
            seen.add(key)
            last_new = now
        elif now - last_new >= settings.quiet_s:
            return True
    return False


async def wait_until(
    observe: Callable[[], Awaitable[Digest]],
    predicate: Callable[[Digest], bool],
    *,
    timeout_s: float,
    poll_interval_s: float = 0.3,
) -> tuple[Digest, bool]:
    """Poll until ``predicate`` holds. Returns the last digest and whether it did."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    digest = await observe()
    while True:
        if predicate(digest):
            return digest, True
        if loop.time() >= deadline:
            return digest, False
        await asyncio.sleep(poll_interval_s)
        digest = await observe()
