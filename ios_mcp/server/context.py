"""Server-wide state shared by the tool modules.

One device pool and at most one active IosSession per server process. The
session is created lazily so that read-only tools such as ios_doctor work
before any device has been touched.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.dependencies import get_context

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.errors import DeviceUnavailable
from ios_mcp.perception.refs import Target
from ios_mcp.policy.gate import Verdict
from ios_mcp.session import IosSession

logger = logging.getLogger(__name__)


class ServerContext:
    """Holds the device pool and the current session."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = DevicePool(settings)
        self.session: IosSession | None = None

    async def open(
        self, device: str | None = None, *, app: str | None = None, fresh: bool = False
    ) -> IosSession:
        if self.session is not None:
            await self.close()
        lease = await self.pool.acquire(device, bundle_id=app, fresh=fresh)
        self.session = IosSession(lease, self.settings, on_approval=_elicit_approval)
        return self.session

    def require(self) -> IosSession:
        """The active session, with a clear error when there is none."""
        if self.session is None:
            raise DeviceUnavailable(
                "No session is open",
                hint="Call ios_open_session first. ios_list_devices shows what is available.",
            )
        return self.session

    async def close(self) -> None:
        if self.session is None:
            return
        udid = self.session.lease.device.udid
        self.session = None
        await self.pool.release(udid)

    async def shutdown(self) -> None:
        self.session = None
        await self.pool.release_all()


async def _elicit_approval(action: str, verdict: Verdict, target: Target | None) -> bool:
    """Ask the human, through the MCP client, before doing something destructive.

    Elicitation keeps the decision with the person rather than the model. When
    the client does not support it, the action is refused rather than allowed:
    an unanswerable question is not consent.
    """
    what = target.describe if target else action
    question = (
        f"Allow {action} on {what}?\n"
        f"{verdict.reason}\n"
        f"This affects the real device and may not be reversible."
    )
    try:
        context = get_context()
    except (LookupError, RuntimeError, AttributeError):
        logger.warning("No MCP context available for approval; refusing %s", action)
        return False

    try:
        result: Any = await context.elicit(question, response_type=None)
    except Exception as exc:
        logger.warning("Elicitation failed (%s); refusing %s", exc, action)
        return False

    return getattr(result, "action", None) == "accept"
