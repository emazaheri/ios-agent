"""WebDriverAgent session lifecycle, tuned settings, and auto-heal.

Auto-heal matters more here than in a test harness: an agent driving a phone
for several minutes will outlive at least one runner crash, and an agent that
has to reason about infrastructure failures wastes turns. A recovered call
reports ``recovered=True`` rather than raising.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from ios_mcp.config import Settings
from ios_mcp.errors import DeviceLocked, RunnerCrashed, SessionLost, WdaError
from ios_mcp.wda.client import WdaClient
from ios_mcp.wda.models import AlertInfo, Rect, SnapshotNode

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: The home screen, as an app. Activating it after backgrounding another
#: app is what stops XCTest blocking on the one that went away.
SPRINGBOARD_BUNDLE_ID = "com.apple.springboard"

# Where WDA needs the value under a different key than our config uses.
_SETTINGS_KEYS = {
    "snapshotMaxDepth": "max_depth",
    "snapshotMaxChildren": "max_children",
    "customSnapshotTimeout": "custom_snapshot_timeout_s",
    "waitForIdleTimeout": "wait_for_idle_timeout_s",
    "useFirstMatch": "use_first_match",
}


class WdaSession:
    """One live automation session against one device."""

    def __init__(
        self,
        client: WdaClient,
        settings: Settings,
        *,
        relaunch: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self._relaunch = relaunch
        self.session_id: str | None = None
        self.bundle_id: str | None = None
        self.recovered_count = 0

    # -- lifecycle ---------------------------------------------------------

    async def open(self, bundle_id: str | None = None, *, fresh: bool = False) -> str:
        """Create a session, optionally launching an app into the foreground."""
        capabilities: dict[str, Any] = {
            "alwaysMatch": {
                # Without this WDA terminates whatever app is running on session
                # teardown, which is hostile on someone's real phone.
                "shouldTerminateApp": False,
                "eventloopIdleDelaySec": 0,
            },
            "firstMatch": [{}],
        }
        if bundle_id:
            capabilities["alwaysMatch"]["bundleId"] = bundle_id
            capabilities["alwaysMatch"]["shouldWaitForQuiescence"] = True
            if fresh:
                capabilities["alwaysMatch"]["forceAppLaunch"] = True

        value = await self.client.post("/session", {"capabilities": capabilities})
        session_id = _session_id_of(value)
        if not session_id:
            raise SessionLost(
                "WebDriverAgent created no session id",
                hint="Restart the runner, then retry.",
            )
        self.session_id = session_id
        self.bundle_id = bundle_id
        await self.apply_settings()
        logger.info("WDA session %s open (app=%s)", session_id, bundle_id or "<none>")
        return session_id

    async def apply_settings(self) -> dict[str, Any]:
        """Push the snapshot tuning that keeps observation affordable.

        Defaults produce accessibility trees far too large and slow for an agent
        loop, so this runs on every new or recovered session.
        """
        snap = self.settings.snapshot
        payload = {key: getattr(snap, attr) for key, attr in _SETTINGS_KEYS.items()}
        try:
            return await self._call(
                lambda sid: self.client.post(
                    f"/session/{sid}/appium/settings", {"settings": payload}
                )
            )
        except WdaError:
            # Older runners reject unknown keys wholesale; degraded but usable.
            logger.warning("WDA rejected session settings; continuing with its defaults")
            return {}

    async def close(self) -> None:
        if not self.session_id:
            return
        try:
            await self.client.delete(f"/session/{self.session_id}")
        except (WdaError, RunnerCrashed):
            pass  # a dead session needs no closing
        finally:
            self.session_id = None

    async def ensure_open(self) -> str:
        if self.session_id is None:
            await self.open(self.bundle_id)
        assert self.session_id is not None
        return self.session_id

    # -- auto-heal ---------------------------------------------------------

    async def _call(self, fn: Callable[[str], Awaitable[T]]) -> T:
        """Run a session-scoped call, recovering once from the failures we can fix."""
        session_id = await self.ensure_open()
        try:
            return await fn(session_id)
        except DeviceLocked:
            # A phone that has merely slept is the commonest interruption of a
            # long run, and waking it is something we can just do.
            if not self.settings.wda.auto_heal or not await self.wake():
                raise
            logger.info("Woke the device and retried")
            self.recovered_count += 1
            return await fn(await self.ensure_open())
        except (SessionLost, RunnerCrashed) as exc:
            if not self.settings.wda.auto_heal:
                raise
            # A sleeping phone stops answering and looks exactly like a hung
            # runner, but waking it costs a second where restarting costs a
            # minute. Try the cheap explanation first.
            if isinstance(exc, RunnerCrashed) and await self.wake():
                logger.info("Device was asleep, not crashed; woke it and retried")
                self.recovered_count += 1
                return await fn(await self.ensure_open())

            logger.warning("Recovering WDA session after: %s", exc)
            await self._heal(exc)
            self.recovered_count += 1
            return await fn(await self.ensure_open())

    async def wake(self) -> bool:
        """Wake the screen. Returns whether the device ended up unlocked.

        This can only dismiss sleep, not a passcode: WebDriverAgent has no way
        to type one. On a passcode-locked phone it returns False so the caller
        surfaces the original error rather than retrying forever.
        """
        try:
            session_id = await self.ensure_open()
            await self.client.post(f"/session/{session_id}/wda/unlock")
            await asyncio.sleep(1.0)
            locked = await self.client.get(f"/session/{session_id}/wda/locked")
            return not bool(locked)
        except (WdaError, RunnerCrashed, SessionLost):
            return False

    async def is_locked(self) -> bool:
        value = await self._call(lambda sid: self.client.get(f"/session/{sid}/wda/locked"))
        return bool(value)

    async def _heal(self, exc: Exception) -> None:
        """Rebuild the session, restarting the runner first if it died."""
        self.session_id = None
        if isinstance(exc, RunnerCrashed):
            if self._relaunch is None:
                raise RunnerCrashed(
                    "WebDriverAgent died and no relaunch hook is configured",
                    hint="Open the session through the device pool so the runner can be restarted.",
                ) from exc
            base_url = await self._relaunch()
            if base_url != self.client.base_url:
                await self.client.aclose()
                self.client = WdaClient(base_url, self.settings.wda)
        # Restore the app that was in the foreground before the crash.
        await self.open(self.bundle_id)

    # -- observation -------------------------------------------------------

    async def source(self) -> SnapshotNode:
        """Fetch the raw accessibility tree. The perception layer compacts it.

        `format=json` on purpose. `format=description` is 30% faster and 4x
        smaller and was rejected in `SnapshotSettings`, which also records why
        `pageSourceExcludedAttributes` is no longer sent: on this endpoint it
        does nothing, measured at 750 ms against 743 ms.
        """
        value = await self._call(
            lambda sid: self.client.get(f"/session/{sid}/source", params={"format": "json"})
        )
        if isinstance(value, dict) and "value" in value and "type" not in value:
            value = value["value"]
        if not isinstance(value, dict):
            raise WdaError("Accessibility source was not a JSON tree")
        return SnapshotNode.from_wda(value)

    async def screenshot(self) -> bytes:
        return await self._call(lambda sid: self.client.screenshot_png(sid))

    async def window_size(self) -> Rect:
        value = await self._call(lambda sid: self.client.get(f"/session/{sid}/window/size"))
        return Rect(0, 0, float(value.get("width", 0)), float(value.get("height", 0)))

    async def active_app(self) -> dict[str, Any]:
        value = await self._call(lambda sid: self.client.get(f"/session/{sid}/wda/activeAppInfo"))
        return value if isinstance(value, dict) else {}

    async def alert(self) -> AlertInfo | None:
        """The alert currently blocking the screen, if any."""
        try:
            text = await self._call(lambda sid: self.client.get(f"/session/{sid}/alert/text"))
        except WdaError:
            return None
        if text is None:
            return None
        try:
            buttons = await self._call(
                lambda sid: self.client.get(f"/session/{sid}/wda/alert/buttons")
            )
        except WdaError:
            buttons = []
        return AlertInfo(text=str(text), buttons=tuple(str(b) for b in buttons or ()))

    # -- interaction -------------------------------------------------------

    async def tap(self, x: float, y: float) -> None:
        await self._call(lambda sid: self.client.post(f"/session/{sid}/wda/tap", {"x": x, "y": y}))

    async def double_tap(self, x: float, y: float) -> None:
        await self._call(
            lambda sid: self.client.post(f"/session/{sid}/wda/doubleTap", {"x": x, "y": y})
        )

    async def touch_and_hold(self, x: float, y: float, duration_s: float) -> None:
        await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/touchAndHold", {"x": x, "y": y, "duration": duration_s}
            )
        )

    async def drag(
        self, from_x: float, from_y: float, to_x: float, to_y: float, duration_s: float = 0.5
    ) -> None:
        await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/dragfromtoforduration",
                {
                    "fromX": from_x,
                    "fromY": from_y,
                    "toX": to_x,
                    "toY": to_y,
                    "duration": duration_s,
                },
            )
        )

    async def send_keys(self, text: str, *, frequency: int = 60) -> None:
        """Type into whatever currently holds keyboard focus."""
        await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/keys", {"value": list(text), "frequency": frequency}
            )
        )

    async def press_button(self, name: str) -> None:
        await self._call(
            lambda sid: self.client.post(f"/session/{sid}/wda/pressButton", {"name": name})
        )

    async def home(self) -> None:
        """Go to the home screen.

        Two things here are not obvious, both measured on a physical iPhone
        running iOS 26.6.

        The gesture goes through /wda/pressButton because /wda/homescreen
        returns 404 on WebDriverAgent 16.8.

        Afterwards WDA is pointed at SpringBoard. Without that, XCTest keeps
        waiting on the app that was just backgrounded and the next
        accessibility snapshot blocks for 61 seconds; with it, the same
        snapshot takes about 5. No WDA setting avoids this, and the stall is
        long enough that the session gives up and restarts the runner, turning
        a trivial action into a minute of downtime.
        """
        await self.press_button("home")
        try:
            await self.activate_app(SPRINGBOARD_BUNDLE_ID)
        except (WdaError, RunnerCrashed):
            # Not fatal: the next snapshot is merely slow, not wrong.
            logger.debug("Could not activate SpringBoard after pressing home")

    async def handle_alert(self, action: str, button: str | None = None) -> None:
        if action not in ("accept", "dismiss"):
            raise WdaError(f"Unknown alert action: {action}")
        payload = {"name": button} if button else {}
        await self._call(lambda sid: self.client.post(f"/session/{sid}/alert/{action}", payload))

    # -- app control -------------------------------------------------------

    async def launch_app(self, bundle_id: str, *, fresh: bool = False) -> None:
        payload: dict[str, Any] = {"bundleId": bundle_id, "shouldWaitForQuiescence": True}
        if fresh:
            payload["forceAppLaunch"] = True
        await self._call(lambda sid: self.client.post(f"/session/{sid}/wda/apps/launch", payload))
        self.bundle_id = bundle_id

    async def activate_app(self, bundle_id: str) -> None:
        await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/apps/activate", {"bundleId": bundle_id}
            )
        )
        self.bundle_id = bundle_id

    async def terminate_app(self, bundle_id: str) -> bool:
        value = await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/apps/terminate", {"bundleId": bundle_id}
            )
        )
        return bool(value)

    async def app_state(self, bundle_id: str) -> int:
        """XCUIApplicationState: 1 not running, 2 background suspended, 4 foreground."""
        value = await self._call(
            lambda sid: self.client.post(f"/session/{sid}/wda/apps/state", {"bundleId": bundle_id})
        )
        return int(value) if isinstance(value, (int, str)) else 0

    async def open_url(self, url: str) -> None:
        await self._call(lambda sid: self.client.post(f"/session/{sid}/url", {"url": url}))

    # -- device state ------------------------------------------------------

    async def get_pasteboard(self) -> str:
        import base64

        value = await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/getPasteboard", {"contentType": "plaintext"}
            )
        )
        return base64.b64decode(str(value)).decode(errors="replace") if value else ""

    async def set_pasteboard(self, text: str) -> None:
        import base64

        encoded = base64.b64encode(text.encode()).decode()
        await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/wda/setPasteboard",
                {"content": encoded, "contentType": "plaintext"},
            )
        )

    async def set_orientation(self, orientation: str) -> None:
        await self._call(
            lambda sid: self.client.post(
                f"/session/{sid}/orientation", {"orientation": orientation.upper()}
            )
        )


def _session_id_of(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("sessionId") or (value.get("capabilities") or {}).get("sessionId")
    return None
