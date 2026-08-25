"""Session lifecycle and app control tools."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ios_mcp.config import Settings
from ios_mcp.server.annotations import MUTATING, MUTATING_IDEMPOTENT, READ_ONLY
from ios_mcp.server.context import ServerContext
from ios_mcp.server.errors import ok, tool_errors


def register(mcp: FastMCP, cfg: Settings, ctx: ServerContext) -> None:
    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_open_session(
        device: Annotated[
            str | None,
            Field(
                description="UDID, exact name, or part of a name. Omit to pick the best default."
            ),
        ] = None,
        app: Annotated[
            str | None, Field(description="Bundle id to open, e.g. com.apple.Preferences.")
        ] = None,
        fresh: Annotated[
            bool, Field(description="Relaunch the app from scratch rather than resuming it.")
        ] = False,
    ) -> dict[str, Any]:
        """Attach to a device and start driving it.

        Boots the simulator or verifies the phone, starts WebDriverAgent, and
        opens a tuned session. Omitting `device` prefers an already-booted
        simulator over a connected iPhone, so acting on a real device is always
        a deliberate choice.
        """
        session = await ctx.open(device, app=app, fresh=fresh)
        digest = await session.observe()
        return ok(
            device=session.lease.device.to_dict(),
            screen=digest.to_dict(),
            note=(
                "Refs in `screen` are valid until the next observation. "
                "Action tools return the resulting screen, so you rarely need "
                "to call ios_observe again straight after acting."
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_session_status() -> dict[str, Any]:
        """Report the current session: device, foreground app, and safety state."""
        if ctx.session is None:
            return {"open": False, "hint": "Call ios_open_session to start."}
        session = ctx.session
        return {
            "open": True,
            "lease": session.lease.to_dict(),
            "halted": session.halted,
            "halted_reason": session.gate.halted_reason,
            "looping": session.looping,
            "audit": session.audit.summary(),
        }

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_close_session() -> dict[str, Any]:
        """Release the device and shut down its WebDriverAgent runner."""
        trail = ctx.session.audit.summary() if ctx.session else None
        await ctx.close()
        return ok(closed=True, audit=trail)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_list_apps(
        kind: Annotated[str, Field(description="'user', 'system', or 'all'.")] = "user",
    ) -> dict[str, Any]:
        """List apps installed on the device, with their bundle identifiers."""
        session = ctx.require()
        apps = await session.lease.adapter.list_apps(kind)  # type: ignore[arg-type]
        return {"count": len(apps), "apps": [a.to_dict() for a in apps]}

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_launch_app(
        bundle_id: Annotated[str, Field(description="e.g. com.apple.Preferences")],
        fresh: Annotated[
            bool, Field(description="Restart the app instead of resuming where it was.")
        ] = False,
    ) -> dict[str, Any]:
        """Bring an app to the foreground and return its screen."""
        session = ctx.require()
        result = await session.launch_app(bundle_id, fresh=fresh)
        return ok(**result.to_dict())

    @mcp.tool(annotations=MUTATING)
    @tool_errors
    async def ios_terminate_app(
        bundle_id: Annotated[str, Field(description="Bundle id of the app to kill.")],
    ) -> dict[str, Any]:
        """Force-quit an app."""
        session = ctx.require()
        return ok(terminated=await session.terminate_app(bundle_id))

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_open_url(
        url: Annotated[
            str,
            Field(
                description=(
                    "A URL or deep link, e.g. App-prefs:root=WIFI (iOS 26 Settings) "
                    "or https://example.com"
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Open a URL or deep link.

        Usually the cheapest way to reach a screen: a deep link skips the
        navigation an agent would otherwise have to tap through.
        """
        session = ctx.require()
        result = await session.open_url(url)
        return ok(**result.to_dict())

    @mcp.tool(annotations=MUTATING)
    @tool_errors
    async def ios_install_app(
        path: Annotated[str, Field(description="Path to a .app bundle or .ipa file.")],
    ) -> dict[str, Any]:
        """Install an app from a local .app or .ipa."""
        session = ctx.require()
        installed = await session.lease.adapter.install_app(Path(path))
        return ok(installed=installed)
