"""Wait for the screen to stop moving after an action.

Fixed sleeps are the classic source of flaky UI automation: too short and the
next observation catches a half-finished transition, too long and every step
pays for the worst case. Polling the screen fingerprint until it repeats costs
the minimum each time and adapts to whatever the app is actually doing.
"""

from __future__ import annotations

import asyncio
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


async def settle(
    observe: Callable[[], Awaitable[Digest]],
    settings: StabilizeSettings,
    *,
    baseline: str | None = None,
) -> SettleOutcome:
    """Poll until the fingerprint repeats ``stable_samples`` times in a row.

    ``baseline`` is the fingerprint before the action. When it is supplied the
    loop keeps going while the screen still matches it, so an action whose
    effect is slow to appear is not mistaken for one that did nothing.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + settings.max_wait_s

    if settings.min_delay_s > 0:
        await asyncio.sleep(settings.min_delay_s)

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
