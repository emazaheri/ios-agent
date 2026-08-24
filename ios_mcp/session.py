"""IosSession: the automation API, with no MCP dependency.

This is the seam the architecture is built around. The MCP server is one
consumer; a future LangGraph agent can import this directly and skip the
protocol round-trip on latency-critical steps.

Every action follows the same shape: resolve, act, settle, re-observe. The
re-observation is not a convenience. An observe/act/observe loop costs two MCP
round-trips per step; folding the observation into the action halves that, and
returning a delta rather than a full digest keeps long flows cheap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal

from ios_mcp.actions.idempotency import IdempotencyCache
from ios_mcp.actions.result import ActionResult, DigestDelta, diff_digests
from ios_mcp.actions.stabilize import settle, wait_until
from ios_mcp.config import Settings
from ios_mcp.devices.pool import Lease
from ios_mcp.errors import (
    ElementNotInteractable,
    InvalidArgument,
    NotSupported,
)
from ios_mcp.perception.digest import Digest, build_digest
from ios_mcp.perception.refs import RefTable, Target
from ios_mcp.perception.resolve import resolve as resolve_target
from ios_mcp.perception.roles import SETTABLE_ROLES
from ios_mcp.wda.models import AlertInfo, Rect
from ios_mcp.wda.session import WdaSession

logger = logging.getLogger(__name__)

Direction = Literal["up", "down", "left", "right"]

#: Fraction of a scrollable area traversed by one scroll gesture.
_SCROLL_FRACTION = 0.6
#: Below this identity overlap the screen is a new one, so send a full digest.
_DELTA_OVERLAP_THRESHOLD = 0.5


class IosSession:
    """One automation session against one device."""

    def __init__(self, lease: Lease, settings: Settings) -> None:
        self.lease = lease
        self.settings = settings
        self.refs = RefTable()
        self.idempotency = IdempotencyCache()
        self._last_digest: Digest | None = None
        self.halted = False
        self.consecutive_failures = 0
        self._fingerprint_history: list[str] = []

    @property
    def wda(self) -> WdaSession:
        return self.lease.session

    @property
    def device_name(self) -> str:
        return self.lease.device.name

    # -- observation -------------------------------------------------------

    async def snapshot(
        self, *, query: str | None = None, region: Rect | None = None, budget: int | None = None
    ) -> Digest:
        """Build a digest without recording it as what the agent has seen.

        Used internally for resolution and settle polling. Recording these
        would destroy the ref table's memory of what the agent was actually
        shown, which is what lets a reassigned ref be detected.
        """
        root = await self.wda.source()
        app = (await self._active_bundle_id()) or self.wda.bundle_id
        digest_settings = self.settings.digest
        if budget is not None:
            digest_settings = digest_settings.model_copy(update={"token_budget": budget})
        digest = build_digest(root, digest_settings, app=app, query=query, region=region)
        self._last_digest = digest
        return digest

    async def observe(
        self, *, query: str | None = None, region: Rect | None = None, budget: int | None = None
    ) -> Digest:
        """Observe the screen and record the refs as what the agent has seen."""
        digest = await self.snapshot(query=query, region=region, budget=budget)
        self.refs.update(digest)
        self._remember_fingerprint(digest.fingerprint)
        return digest

    async def screenshot(self) -> bytes:
        return await self.wda.screenshot()

    async def alert(self) -> AlertInfo | None:
        return await self.wda.alert()

    async def read_text(self, ref: str | None = None, target: str | None = None) -> str:
        """Full text of the screen, or of one element and its descendants."""
        digest = self._last_digest or await self.observe()
        if ref is None and target is None:
            return "\n".join(t for t in (n.text for n in digest.nodes) if t)
        resolved = self.resolve(digest, ref=ref, target=target, actionable_only=False)
        inside = [n.text for n in digest.nodes if n.text and _within(n.rect, resolved.rect)]
        return "\n".join(t for t in inside if t)

    def resolve(
        self,
        digest: Digest,
        *,
        ref: str | None = None,
        target: str | None = None,
        role: str | None = None,
        prefer_roles: frozenset[str] | None = None,
        actionable_only: bool = True,
    ) -> Target:
        return resolve_target(
            digest,
            self.refs,
            ref=ref,
            target=target,
            role=role,
            prefer_roles=prefer_roles,
            actionable_only=actionable_only,
        )

    # -- actions -----------------------------------------------------------

    async def tap(
        self,
        *,
        ref: str | None = None,
        target: str | None = None,
        role: str | None = None,
        double: bool = False,
        long_press_s: float | None = None,
        idem_key: str | None = None,
    ) -> ActionResult:
        async def do(resolved: Target) -> None:
            x, y = resolved.point
            if long_press_s is not None:
                await self.wda.touch_and_hold(x, y, long_press_s)
            elif double:
                await self.wda.double_tap(x, y)
            else:
                await self.wda.tap(x, y)

        return await self._act("tap", do, ref=ref, target=target, role=role, idem_key=idem_key)

    async def type_text(
        self,
        text: str,
        *,
        ref: str | None = None,
        target: str | None = None,
        clear_first: bool = False,
        submit: bool = False,
        idem_key: str | None = None,
        _redact: bool = False,
    ) -> ActionResult:
        """Type into a field, focusing it first if a target was given."""

        async def do(resolved: Target | None) -> None:
            if resolved is not None:
                x, y = resolved.point
                await self.wda.tap(x, y)
            if clear_first:
                # Select-all then overwrite; WDA has no reliable clear-by-coordinate.
                await self.wda.send_keys("a")
            await self.wda.send_keys(text)
            if submit:
                await self.wda.send_keys("\n")

        return await self._act(
            "type",
            do,
            ref=ref,
            target=target,
            role=None,
            idem_key=idem_key,
            require_target=False,
            note="typed a secret" if _redact else None,
        )

    async def set_value(
        self,
        value: str,
        *,
        ref: str | None = None,
        target: str | None = None,
        idem_key: str | None = None,
    ) -> ActionResult:
        """Set a switch, slider, stepper, or picker rather than tapping at it."""

        async def do(resolved: Target) -> None:
            if resolved.role == "switch":
                current = self._last_digest.by_ref(resolved.ref) if self._last_digest else None
                wanted = value.strip().lower() in ("1", "on", "true", "yes")
                if current is not None and (current.value == "1") == wanted:
                    return  # already in the requested state; tapping would undo it
                x, y = resolved.point
                await self.wda.tap(x, y)
                return
            if resolved.role not in ("slider", "stepper", "picker", "segmented"):
                raise ElementNotInteractable(
                    f"Cannot set a value on a {resolved.role}",
                    hint="Use ios_tap for buttons and cells, or ios_type for text fields.",
                )
            await self.wda.send_keys(value)

        return await self._act(
            "set_value",
            do,
            ref=ref,
            target=target,
            role=None,
            prefer_roles=SETTABLE_ROLES,
            idem_key=idem_key,
        )

    async def scroll(
        self,
        direction: Direction = "down",
        *,
        ref: str | None = None,
        target: str | None = None,
        until: str | None = None,
        max_scrolls: int = 10,
        idem_key: str | None = None,
    ) -> ActionResult:
        """Scroll, optionally repeating until some text appears.

        ``until`` is bounded by ``max_scrolls`` and stops early when the screen
        stops changing, so a list that has reached its end does not spin.
        """
        started = time.monotonic()
        cached = self.idempotency.get(idem_key)
        if cached is not None:
            return _from_cache(cached)

        before = self._last_digest or await self.snapshot()
        area = await self._scroll_area(before, ref=ref, target=target)

        found = False
        scrolls = 0
        digest = before
        for _ in range(max_scrolls if until else 1):
            previous_fp = digest.fingerprint
            await self._swipe_within(area, direction)
            outcome = await settle(self.snapshot, self.settings.stabilize)
            digest = outcome.digest
            scrolls += 1
            if until and _contains_text(digest, until):
                found = True
                break
            if until and digest.fingerprint == previous_fp:
                break  # reached the end of the content

        note = None
        if until:
            note = (
                f"found {until!r} after {scrolls} scroll(s)"
                if found
                else f"{until!r} not found after {scrolls} scroll(s); the list may have ended"
            )

        result = await self._finish(
            "scroll", before, digest, target=None, started=started, note=note
        )
        self.idempotency.put(idem_key, result)
        return result

    async def swipe(
        self,
        direction: Direction,
        *,
        ref: str | None = None,
        target: str | None = None,
        idem_key: str | None = None,
    ) -> ActionResult:
        started = time.monotonic()
        cached = self.idempotency.get(idem_key)
        if cached is not None:
            return _from_cache(cached)

        before = self._last_digest or await self.snapshot()
        area = await self._scroll_area(before, ref=ref, target=target)
        await self._swipe_within(area, direction)
        outcome = await settle(self.snapshot, self.settings.stabilize, baseline=before.fingerprint)
        result = await self._finish("swipe", before, outcome.digest, target=None, started=started)
        self.idempotency.put(idem_key, result)
        return result

    async def drag(
        self,
        *,
        from_ref: str,
        to_ref: str,
        duration_s: float = 0.6,
        idem_key: str | None = None,
    ) -> ActionResult:
        started = time.monotonic()
        cached = self.idempotency.get(idem_key)
        if cached is not None:
            return _from_cache(cached)

        before = self._last_digest or await self.snapshot()
        source = self.resolve(before, ref=from_ref)
        destination = self.resolve(before, ref=to_ref, actionable_only=False)
        sx, sy = source.point
        dx, dy = destination.point
        await self.wda.drag(sx, sy, dx, dy, duration_s)
        outcome = await settle(self.snapshot, self.settings.stabilize, baseline=before.fingerprint)
        result = await self._finish("drag", before, outcome.digest, target=source, started=started)
        self.idempotency.put(idem_key, result)
        return result

    async def press_button(self, name: str, *, idem_key: str | None = None) -> ActionResult:
        normalised = name.strip().lower()

        async def do(_: Target | None) -> None:
            if normalised == "home":
                await self.wda.home()
            elif normalised in ("volumeup", "volume_up", "volumedown", "volume_down", "siri"):
                await self.wda.press_button(_camel(normalised))
            else:
                raise InvalidArgument(
                    f"Unknown button {name!r}",
                    hint="Supported: home, volumeUp, volumeDown, siri.",
                )

        return await self._act(
            "press_button",
            do,
            ref=None,
            target=None,
            role=None,
            idem_key=idem_key,
            require_target=False,
        )

    async def handle_alert(
        self, action: Literal["accept", "dismiss"], button: str | None = None
    ) -> ActionResult:
        started = time.monotonic()
        before = self._last_digest or await self.snapshot()
        await self.wda.handle_alert(action, button)
        outcome = await settle(self.snapshot, self.settings.stabilize, baseline=before.fingerprint)
        return await self._finish(
            f"handle_alert:{action}", before, outcome.digest, target=None, started=started
        )

    async def wait_for(
        self,
        condition: str,
        *,
        timeout_s: float = 10.0,
        absent: bool = False,
    ) -> ActionResult:
        """Wait until some text appears on screen, or disappears when ``absent``."""
        started = time.monotonic()
        before = self._last_digest or await self.snapshot()

        def predicate(digest: Digest) -> bool:
            present = _contains_text(digest, condition)
            return not present if absent else present

        digest, met = await wait_until(self.snapshot, predicate, timeout_s=timeout_s)
        verb = "disappeared" if absent else "appeared"
        note = (
            f"{condition!r} {verb}"
            if met
            else f"{condition!r} did not {verb.rstrip('ed')} within {timeout_s:.0f}s"
        )
        result = await self._finish(
            "wait_for", before, digest, target=None, started=started, note=note
        )
        result.ok = met
        return result

    # -- app control -------------------------------------------------------

    async def launch_app(self, bundle_id: str, *, fresh: bool = False) -> ActionResult:
        started = time.monotonic()
        before = self._last_digest
        await self.wda.launch_app(bundle_id, fresh=fresh)
        outcome = await settle(self.snapshot, self.settings.stabilize)
        return await self._finish(
            f"launch_app:{bundle_id}", before, outcome.digest, target=None, started=started
        )

    async def terminate_app(self, bundle_id: str) -> bool:
        return await self.wda.terminate_app(bundle_id)

    async def open_url(self, url: str) -> ActionResult:
        """Deep links skip navigation entirely and are the cheapest way to arrive."""
        started = time.monotonic()
        before = self._last_digest
        if self.lease.device.kind == "simulator":
            await self.lease.adapter.open_url(url)
        else:
            await self.wda.open_url(url)
        outcome = await settle(self.snapshot, self.settings.stabilize)
        return await self._finish("open_url", before, outcome.digest, target=None, started=started)

    async def app_state(self, bundle_id: str) -> int:
        return await self.wda.app_state(bundle_id)

    # -- device state ------------------------------------------------------

    async def get_clipboard(self) -> str:
        return await self.wda.get_pasteboard()

    async def set_clipboard(self, text: str) -> None:
        await self.wda.set_pasteboard(text)

    async def set_permission(self, bundle_id: str, service: str, grant: bool) -> None:
        if self.lease.device.kind != "simulator":
            raise NotSupported(
                "Privacy permissions can only be set programmatically on a Simulator",
                hint=(
                    "On a real device, drive the Settings app or accept the "
                    "permission alert with ios_handle_alert."
                ),
            )
        await self.lease.adapter.set_permission(bundle_id, service, grant)

    # -- internals ---------------------------------------------------------

    async def _act(
        self,
        name: str,
        do: Callable[[Any], Awaitable[None]],
        *,
        ref: str | None,
        target: str | None,
        role: str | None,
        idem_key: str | None,
        prefer_roles: frozenset[str] | None = None,
        require_target: bool = True,
        note: str | None = None,
    ) -> ActionResult:
        """Resolve, act, settle, re-observe, and report the change."""
        started = time.monotonic()
        cached = self.idempotency.get(idem_key)
        if cached is not None:
            return _from_cache(cached)

        before = self._last_digest or await self.snapshot()

        resolved: Target | None = None
        if require_target or ref or target:
            resolved = self.resolve(
                before, ref=ref, target=target, role=role, prefer_roles=prefer_roles
            )
            if not resolved.enabled:
                raise ElementNotInteractable(
                    f"{resolved.describe} is disabled",
                    hint="Something else on the screen has to change before this becomes active.",
                    details={"ref": resolved.ref},
                )

        recovered_before = self.wda.recovered_count
        await do(resolved)
        outcome = await settle(self.snapshot, self.settings.stabilize, baseline=before.fingerprint)
        result = await self._finish(
            name,
            before,
            outcome.digest,
            target=resolved,
            started=started,
            note=note,
            recovered=self.wda.recovered_count > recovered_before,
        )
        self.idempotency.put(idem_key, result)
        return result

    async def _finish(
        self,
        name: str,
        before: Digest | None,
        after: Digest,
        *,
        target: Target | None,
        started: float,
        note: str | None = None,
        recovered: bool = False,
    ) -> ActionResult:
        """Record the new screen and decide between a delta and a full digest."""
        self.refs.update(after)
        self._remember_fingerprint(after.fingerprint)
        alert = await self.wda.alert()

        changed = before is None or before.fingerprint != after.fingerprint
        delta: DigestDelta | None = None
        digest: Digest | None = after
        if before is not None and _overlap(before, after) >= _DELTA_OVERLAP_THRESHOLD:
            delta = diff_digests(before, after)
            digest = None  # the delta already says everything that changed

        self.consecutive_failures = 0
        return ActionResult(
            action=name,
            ok=True,
            screen_changed=changed,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            target=target,
            digest=digest,
            delta=delta,
            alert=alert,
            recovered=recovered,
            note=note,
        )

    async def _scroll_area(self, digest: Digest, *, ref: str | None, target: str | None) -> Rect:
        """The rect to gesture within: a named element, else the largest scrollable."""
        if ref or target:
            return self.resolve(digest, ref=ref, target=target, actionable_only=False).rect
        scrollables = [n for n in digest.nodes if n.scrollable]
        if scrollables:
            return max(scrollables, key=lambda n: n.rect.area).rect
        return await self.wda.window_size()

    async def _swipe_within(self, area: Rect, direction: Direction) -> None:
        """Swipe across the middle of an area.

        The gesture is inset from the edges because a swipe that starts at the
        very edge of the screen triggers system gestures (back navigation,
        Control Centre) instead of scrolling the content.
        """
        cx, cy = area.center
        span_y = area.height * _SCROLL_FRACTION / 2
        span_x = area.width * _SCROLL_FRACTION / 2
        # Content moves opposite to the finger: scrolling down drags upward.
        deltas: dict[str, tuple[float, float, float, float]] = {
            "down": (cx, cy + span_y, cx, cy - span_y),
            "up": (cx, cy - span_y, cx, cy + span_y),
            "left": (cx + span_x, cy, cx - span_x, cy),
            "right": (cx - span_x, cy, cx + span_x, cy),
        }
        if direction not in deltas:
            raise InvalidArgument(
                f"Unknown direction {direction!r}", hint="Use up, down, left, or right."
            )
        x1, y1, x2, y2 = deltas[direction]
        await self.wda.drag(x1, y1, x2, y2, 0.4)

    async def _active_bundle_id(self) -> str | None:
        try:
            info = await self.wda.active_app()
        except Exception:
            return None
        value = info.get("bundleId")
        return str(value) if value else None

    def _remember_fingerprint(self, fingerprint: str) -> None:
        window = self.settings.policy.loop_detection_window
        self._fingerprint_history.append(fingerprint)
        if len(self._fingerprint_history) > window * 2:
            del self._fingerprint_history[: -window * 2]

    @property
    def looping(self) -> bool:
        """True when the last N observations cycled among very few screens.

        An agent bouncing between two screens will otherwise keep going until
        it runs out of budget.
        """
        window = self.settings.policy.loop_detection_window
        recent = self._fingerprint_history[-window:]
        return len(recent) >= window and len(set(recent)) <= 2


# --- helpers ---------------------------------------------------------------


def _from_cache(result: ActionResult) -> ActionResult:
    """Replay a stored result without touching the device."""
    return replace(result, from_cache=True)


def _contains_text(digest: Digest, needle: str) -> bool:
    lowered = needle.strip().lower()
    return any(lowered in (n.text or "").lower() for n in digest.nodes)


def _overlap(before: Digest, after: Digest) -> float:
    """Fraction of the earlier screen's elements still present."""
    if not before.nodes:
        return 0.0
    before_keys = {(n.role, n.label, n.identifier) for n in before.nodes}
    after_keys = {(n.role, n.label, n.identifier) for n in after.nodes}
    return len(before_keys & after_keys) / len(before_keys)


def _within(inner: Rect, outer: Rect) -> bool:
    return (
        inner.x >= outer.x - 1
        and inner.y >= outer.y - 1
        and inner.x + inner.width <= outer.x + outer.width + 1
        and inner.y + inner.height <= outer.y + outer.height + 1
    )


def _camel(name: str) -> str:
    return {
        "volumeup": "volumeUp",
        "volume_up": "volumeUp",
        "volumedown": "volumeDown",
        "volume_down": "volumeDown",
        "siri": "siri",
    }.get(name, name)
