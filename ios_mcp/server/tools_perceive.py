"""Perception tools: what is on screen."""

from __future__ import annotations

import base64
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from pydantic import Field

from ios_mcp.config import Settings
from ios_mcp.server.annotations import READ_ONLY
from ios_mcp.server.context import ServerContext
from ios_mcp.server.errors import tool_errors
from ios_mcp.wda.models import Rect


def register(mcp: FastMCP, cfg: Settings, ctx: ServerContext) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_observe(
        query: Annotated[
            str | None,
            Field(description="Keep only elements whose text contains this. Use when truncated."),
        ] = None,
        region: Annotated[
            list[float] | None,
            Field(description="Restrict to a screen area as [x, y, width, height]."),
        ] = None,
        budget: Annotated[
            int | None, Field(description="Token budget for the digest. Default 1500.")
        ] = None,
        include_elements: Annotated[
            bool,
            Field(
                description=(
                    "Also return each element as structured JSON. Roughly doubles the "
                    "cost and duplicates the text form; only useful programmatically."
                )
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Read the screen as a compact list of elements with short refs.

        Each line is one element: `e7  button "Send" @(340,70)`. Pass those refs
        to the action tools rather than coordinates.

        If the result says elements were omitted, narrow with `query` or
        `region` instead of raising `budget`; a smaller answer is usually the
        more useful one.
        """
        session = ctx.require()
        rect = Rect(*region) if region else None
        digest = await session.observe(query=query, region=rect, budget=budget)
        return session.redactor.mapping(digest.to_dict(include_elements=include_elements))

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_screenshot(
        annotate_refs: Annotated[
            bool,
            Field(description="Draw numbered boxes over the elements from the last observation."),
        ] = False,
    ) -> Image:
        """Capture the screen as an image.

        Use this when an element has no accessibility label, when the layout
        matters, or when `ios_observe` cannot find something you can plainly
        see. With `annotate_refs` the boxes are labelled with the refs from the
        last observation, so you can pick one by eye.
        """
        session = ctx.require()
        png = await session.screenshot()
        if annotate_refs:
            from ios_mcp.perception.vision import annotate

            digest = session._last_digest or await session.observe()
            png = annotate(png, digest)
        return Image(data=png, format="png")

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_read_text(
        ref: Annotated[str | None, Field(description="Read only inside this element.")] = None,
        target: Annotated[
            str | None, Field(description="Read inside the element matching this description.")
        ] = None,
    ) -> dict[str, Any]:
        """Read the text of the screen, or of one element and everything inside it.

        Use this to extract content, rather than `ios_observe`, which is shaped
        for deciding what to tap.
        """
        session = ctx.require()
        text = await session.read_text(ref=ref, target=target)
        return {"text": session.redactor.text(text) or ""}

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_wait_for(
        text: Annotated[str, Field(description="Text to wait for, matched case-insensitively.")],
        timeout_s: Annotated[float, Field(description="How long to wait.", ge=0.1, le=120)] = 10.0,
        absent: Annotated[
            bool, Field(description="Wait for the text to disappear instead of appear.")
        ] = False,
    ) -> dict[str, Any]:
        """Wait for something to appear or disappear.

        Prefer this over guessing a sleep. It returns as soon as the condition
        holds, and reports failure rather than raising, so a timeout is
        something you can reason about.
        """
        session = ctx.require()
        result = await session.wait_for(text, timeout_s=timeout_s, absent=absent)
        return session.redactor.mapping(result.to_dict())

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_get_logs(
        since_s: Annotated[float, Field(description="How far back to read.", ge=1, le=3600)] = 60,
        predicate: Annotated[
            str | None,
            Field(description="Filter: an NSPredicate on a Simulator, else a substring."),
        ] = None,
        limit: Annotated[int, Field(description="Maximum lines to return.", ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        """Read recent device logs. Useful when an app misbehaves and the UI does not say why."""
        session = ctx.require()
        lines: list[str] = []
        async for line in session.lease.adapter.system_log(since_s, predicate):
            lines.append(line)
            if len(lines) >= limit:
                break
        return {"count": len(lines), "lines": [session.redactor.text(x) for x in lines]}

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    async def ios_export_trace() -> dict[str, Any]:
        """Return the ordered record of everything this session has done.

        Useful for explaining what happened, and for turning a successful run
        into a regression test.
        """
        session = ctx.require()
        return session.redactor.mapping(session.audit.to_dict())

    @mcp.resource("ios://session/screenshot", mime_type="image/png")
    async def screenshot_resource() -> bytes:
        """The current screen, as a resource for clients that prefer to pull it."""
        session = ctx.require()
        return base64.b64encode(await session.screenshot())
