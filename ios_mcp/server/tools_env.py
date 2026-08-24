"""Device environment tools: permissions, clipboard, and simulated state."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ios_mcp.config import Settings
from ios_mcp.errors import InvalidArgument, NotSupported
from ios_mcp.server.annotations import MUTATING_IDEMPOTENT
from ios_mcp.server.context import ServerContext
from ios_mcp.server.errors import ok, tool_errors


def register(mcp: FastMCP, cfg: Settings, ctx: ServerContext) -> None:
    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_set_permission(
        bundle_id: Annotated[str, Field(description="App to grant or revoke for.")],
        service: Annotated[
            str,
            Field(description="location, photos, camera, microphone, contacts, calendar, or all."),
        ],
        grant: Annotated[bool, Field(description="True to grant, False to revoke.")] = True,
    ) -> dict[str, Any]:
        """Set a privacy permission without going through the alert.

        Simulator only. On a real phone there is no API for this: drive the
        Settings app, or answer the permission alert with ios_handle_alert.
        """
        session = ctx.require()
        await session.set_permission(bundle_id, service, grant)
        return ok(bundle_id=bundle_id, service=service, granted=grant)

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_clipboard(
        action: Annotated[str, Field(description="'get' or 'set'.")] = "get",
        text: Annotated[
            str | None, Field(description="Text to write when action is 'set'.")
        ] = None,
    ) -> dict[str, Any]:
        """Read or write the device clipboard.

        Writing is often faster and more reliable than typing a long string,
        which the on-screen keyboard can mangle with autocorrect.
        """
        session = ctx.require()
        if action == "get":
            return {"text": session.redactor.text(await session.get_clipboard()) or ""}
        if action == "set":
            if text is None:
                raise InvalidArgument("action='set' needs `text`", hint="Pass the text to write.")
            await session.set_clipboard(text)
            return ok(length=len(text))
        raise InvalidArgument(f"Unknown action {action!r}", hint="Use 'get' or 'set'.")

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_set_device_state(
        orientation: Annotated[str | None, Field(description="'portrait' or 'landscape'.")] = None,
        appearance: Annotated[str | None, Field(description="'light' or 'dark'.")] = None,
        latitude: Annotated[float | None, Field(description="Simulated latitude.")] = None,
        longitude: Annotated[float | None, Field(description="Simulated longitude.")] = None,
        clean_status_bar: Annotated[
            bool,
            Field(description="Freeze the status bar so screenshots are reproducible."),
        ] = False,
    ) -> dict[str, Any]:
        """Change device-level state. Appearance, location, and status bar are Simulator only."""
        session = ctx.require()
        applied: dict[str, Any] = {}

        if orientation:
            await session.wda.set_orientation(orientation)
            applied["orientation"] = orientation

        simulator_only = appearance or latitude is not None or clean_status_bar
        if simulator_only and session.lease.device.kind != "simulator":
            raise NotSupported(
                "Appearance, location, and status bar overrides are Simulator only",
                hint="On a real device, change these in Settings.",
            )

        adapter = session.lease.adapter
        if appearance:
            await adapter.set_appearance(appearance)  # type: ignore[attr-defined]
            applied["appearance"] = appearance
        if latitude is not None and longitude is not None:
            await adapter.set_location(latitude, longitude)  # type: ignore[attr-defined]
            applied["location"] = [latitude, longitude]
        if clean_status_bar:
            await adapter.set_status_bar(  # type: ignore[attr-defined]
                time="9:41", batteryState="charged", batteryLevel=100, cellularBars=4
            )
            applied["status_bar"] = "clean"

        return ok(applied=applied)
