"""Low-level WebDriverAgent HTTP client.

Owns the transport concerns only: request/response envelopes, WDA's error
vocabulary, and timeouts. Session semantics live in ``session.py``.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx

from ios_mcp.config import WdaSettings
from ios_mcp.errors import (
    AppNotFound,
    DeviceLocked,
    ElementNotFound,
    ElementNotInteractable,
    ElementStale,
    IosAutomationError,
    RunnerCrashed,
    SessionLost,
    UnexpectedAlert,
    WdaError,
)
from ios_mcp.wda.models import WdaStatus

logger = logging.getLogger(__name__)

# WDA speaks the W3C WebDriver error vocabulary, with a few additions.
_ERROR_MAP: dict[str, type[IosAutomationError]] = {
    "no such element": ElementNotFound,
    "no such alert": WdaError,
    "stale element reference": ElementStale,
    "invalid element state": ElementNotInteractable,
    "element not interactable": ElementNotInteractable,
    "element not visible": ElementNotInteractable,
    "unexpected alert open": UnexpectedAlert,
    "invalid session id": SessionLost,
    "no such window": SessionLost,
    "session not created": SessionLost,
}


class WdaClient:
    """Async HTTP client for one WebDriverAgent instance."""

    def __init__(self, base_url: str, settings: WdaSettings) -> None:
        self.base_url = base_url.rstrip("/")
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                settings.request_timeout_s,
                connect=settings.connect_timeout_s,
            ),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> WdaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- transport ---------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Issue a request and unwrap WDA's ``{"value": ...}`` envelope."""
        try:
            response = await self._http.request(
                method,
                path,
                json=json,
                params=params,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.ConnectError as exc:
            raise RunnerCrashed(
                f"Cannot reach WebDriverAgent at {self.base_url}",
                hint="The runner process is not listening. It usually needs restarting.",
                recoverable=True,
            ) from exc
        except httpx.ReadTimeout as exc:
            raise RunnerCrashed(
                f"WebDriverAgent timed out on {method} {path}",
                hint=(
                    "Common when the accessibility snapshot is huge. "
                    "Lower snapshot.max_depth, or the runner may have hung."
                ),
                recoverable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise WdaError(f"HTTP failure on {method} {path}: {exc}", recoverable=True) from exc

        return self._unwrap(response, method, path)

    def _unwrap(self, response: httpx.Response, method: str, path: str) -> Any:
        try:
            payload = response.json()
        except ValueError:
            if response.is_success:
                return response.text
            raise WdaError(
                f"WebDriverAgent returned HTTP {response.status_code} with a non-JSON body",
                details={"body": response.text[:300]},
            ) from None

        value = payload.get("value") if isinstance(payload, dict) else payload

        # W3C shape: an error is an object carrying an "error" key.
        if isinstance(value, dict) and "error" in value:
            raise self._to_error(value, method, path)

        # Legacy shape: a non-zero top-level "status".
        if isinstance(payload, dict) and payload.get("status") not in (None, 0):
            raise WdaError(
                str(_message_of(value) or payload.get("status")),
                details={"status": payload.get("status")},
            )

        if not response.is_success:
            raise WdaError(
                f"WebDriverAgent returned HTTP {response.status_code} on {method} {path}",
                details={"body": str(payload)[:300]},
            )
        return value

    def _to_error(self, value: dict[str, Any], method: str, path: str) -> IosAutomationError:
        kind = str(value.get("error", "")).strip().lower()
        message = _message_of(value) or f"{method} {path} failed"

        # A locked phone is the commonest real-device failure, and WDA reports
        # it as a wall of Apple error domains rather than anything typed.
        if _is_locked(message):
            return DeviceLocked(
                "The device is locked",
                hint=(
                    "Unlock the phone and keep it awake. For longer runs, set "
                    "Settings > Display & Brightness > Auto-Lock to Never."
                ),
                recoverable=True,
            )

        # An app that is not installed is the commonest mistake when someone
        # types a bundle id by hand, and WDA reports it as a `session_lost`
        # carrying four nested Apple error domains. Left alone it reads like a
        # crashed runner, which sends people debugging the wrong thing.
        missing = _missing_app(message)
        if missing is not None:
            return AppNotFound(
                f"No app with bundle id {missing!r} is installed on this device",
                hint=(
                    "Check the id. Many are not what you would guess: Hinge is "
                    "`co.hinge.mobile.ios`, not `com.hinge...`. List what is "
                    "installed with `ios_list_apps`, or "
                    "`xcrun devicectl device info apps --device <udid> "
                    "--include-all-apps`."
                ),
                details={"bundle_id": missing},
            )

        cls = _ERROR_MAP.get(kind, WdaError)
        recoverable = issubclass(cls, (SessionLost, ElementStale, RunnerCrashed))
        return cls(
            message,
            hint=_hint_for(kind),
            details={"wda_error": kind},
            recoverable=recoverable,
        )

    # -- convenience -------------------------------------------------------

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, json: dict[str, Any] | None = None, **kw: Any) -> Any:
        return await self.request("POST", path, json=json or {}, **kw)

    async def delete(self, path: str, **kw: Any) -> Any:
        return await self.request("DELETE", path, **kw)

    async def status(self) -> WdaStatus:
        """`GET /status` is the liveness probe and also reveals the default session."""
        response = await self._http.get("/status", timeout=self.settings.connect_timeout_s)
        return WdaStatus.from_wda(response.json())

    async def is_alive(self) -> bool:
        try:
            await self.status()
        except (httpx.HTTPError, ValueError):
            return False
        return True

    async def screenshot_png(self, session_id: str | None = None) -> bytes:
        """Screenshots come back base64-encoded regardless of endpoint."""
        path = f"/session/{session_id}/screenshot" if session_id else "/screenshot"
        encoded = await self.get(path)
        if not isinstance(encoded, str):
            raise WdaError("Screenshot response was not a base64 string")
        return base64.b64decode(encoded)


#: SpringBoard's way of saying it has never heard of that bundle id.
_MISSING_APP_MARKERS = (
    "fbsapplicationlibrary",
    "application info provider",
)


def _missing_app(message: str) -> str | None:
    """Pull the bundle id out of a failed-to-open error, if that is what it is.

    Matching on the message rather than the code because WDA reports this as
    `session_lost`, the same code a genuinely dead session uses, and the two
    need completely different responses from the caller.
    """
    lowered = message.lower()
    if "failed" not in lowered or not any(m in lowered for m in _MISSING_APP_MARKERS):
        return None

    # Not by pairing quotes. Apple nests them -- `"The request to open
    # "com.x.y" failed."` -- so a naive pair match captures the text either
    # side of the id and never the id itself. Read it from the phrase instead.
    found = re.search(r'to open "?([A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)"?', message)
    return found.group(1) if found else None


def _is_locked(message: str) -> bool:
    lowered = message.lower()
    return "could not be, unlocked" in lowered or ("passcode" in lowered and "locked" in lowered)


def _message_of(value: Any) -> str | None:
    if isinstance(value, dict):
        message = value.get("message") or value.get("description")
        if message:
            # Kept to one line, but long enough to carry the nested Apple
            # domains that `_missing_app` reads the bundle id out of.
            return str(message).splitlines()[0][:600]
    return None


def _hint_for(kind: str) -> str | None:
    return {
        "no such element": "Call ios_observe to see what is actually on screen.",
        "stale element reference": (
            "The screen changed since the last observation. Re-observe and use a fresh ref."
        ),
        "invalid element state": (
            "The element is disabled or off-screen. Scroll it into view first."
        ),
        "element not interactable": (
            "The element is covered or off-screen. Scroll it into view, or dismiss an overlay."
        ),
        "unexpected alert open": "Handle the alert with ios_handle_alert, then retry.",
        "invalid session id": "The session died. It will be recreated automatically.",
    }.get(kind)
