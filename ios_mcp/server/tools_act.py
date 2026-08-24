"""Action tools. Every one returns the screen it produced."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from ios_mcp.config import Settings
from ios_mcp.server.annotations import DESTRUCTIVE, MUTATING, MUTATING_IDEMPOTENT
from ios_mcp.server.context import ServerContext
from ios_mcp.server.errors import ok, tool_errors

RefArg = Annotated[
    str | None, Field(description="A ref from the last observation, such as 'e7'. Preferred.")
]
TargetArg = Annotated[
    str | None,
    Field(description="Plain description of the element, e.g. 'the Send button'. Used if no ref."),
]
IdemArg = Annotated[
    str | None,
    Field(
        description=(
            "Optional key making a retry a no-op. Pass the same key when repeating "
            "an action you are unsure completed."
        )
    ),
]
ApproveArg = Annotated[
    str | None,
    Field(description="Signature returned by a previous action_requires_approval error."),
]


def register(mcp: FastMCP, cfg: Settings, ctx: ServerContext) -> None:
    def _approve(approve: str | None) -> None:
        if approve:
            ctx.require().approve(approve)

    @mcp.tool(annotations=DESTRUCTIVE)
    @tool_errors
    async def ios_tap(
        ref: RefArg = None,
        target: TargetArg = None,
        role: Annotated[
            str | None, Field(description="Narrow by role, e.g. 'button', 'cell', 'switch'.")
        ] = None,
        double: Annotated[bool, Field(description="Double tap.")] = False,
        long_press_s: Annotated[
            float | None, Field(description="Press and hold for this many seconds.", ge=0.1, le=10)
        ] = None,
        approve: ApproveArg = None,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Tap an element and return the resulting screen.

        Prefer `ref` over `target`; it is exact and cannot be misread. Taps on
        things like Send, Pay, or Delete need approval first.
        """
        session = ctx.require()
        _approve(approve)
        result = await session.tap(
            ref=ref,
            target=target,
            role=role,
            double=double,
            long_press_s=long_press_s,
            idem_key=idem_key,
        )
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=DESTRUCTIVE)
    @tool_errors
    async def ios_type(
        text: Annotated[
            str, Field(description="Text to type. Never a password; see ios_type_secret.")
        ],
        ref: RefArg = None,
        target: TargetArg = None,
        clear_first: Annotated[bool, Field(description="Replace the field's contents.")] = False,
        submit: Annotated[bool, Field(description="Press return afterwards.")] = False,
        approve: ApproveArg = None,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Type into a field, focusing it first when a ref or target is given."""
        session = ctx.require()
        _approve(approve)
        result = await session.type_text(
            text, ref=ref, target=target, clear_first=clear_first, submit=submit, idem_key=idem_key
        )
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=MUTATING)
    @tool_errors
    async def ios_type_secret(
        secret_ref: Annotated[
            str,
            Field(description="Name of a stored secret, e.g. 'icloud-password'. Not the value."),
        ],
        ref: RefArg = None,
        target: TargetArg = None,
        submit: Annotated[bool, Field(description="Press return afterwards.")] = False,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Type a stored secret without ever seeing its value.

        The value is read from the host keychain and sent straight to the
        device. Use this for every password and one-time code; never put a
        real credential into `ios_type`, where it would enter the transcript.
        """
        session = ctx.require()
        result = await session.type_secret(
            secret_ref, ref=ref, target=target, submit=submit, idem_key=idem_key
        )
        return ok(**result.to_dict())

    @mcp.tool(annotations=DESTRUCTIVE)
    @tool_errors
    async def ios_set_value(
        value: Annotated[str, Field(description="'on'/'off' for switches, otherwise the value.")],
        ref: RefArg = None,
        target: TargetArg = None,
        approve: ApproveArg = None,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Set a switch, slider, stepper, or picker to a value.

        Prefer this over tapping a switch: it checks the current state first,
        so asking for 'on' when it is already on does nothing rather than
        turning it off.
        """
        session = ctx.require()
        _approve(approve)
        result = await session.set_value(value, ref=ref, target=target, idem_key=idem_key)
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=MUTATING)
    @tool_errors
    async def ios_scroll(
        direction: Annotated[str, Field(description="up, down, left, or right.")] = "down",
        ref: RefArg = None,
        target: TargetArg = None,
        until: Annotated[
            str | None, Field(description="Keep scrolling until this text appears.")
        ] = None,
        max_scrolls: Annotated[
            int, Field(description="Cap on repeats when `until` is set.", ge=1, le=50)
        ] = 10,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Scroll, optionally until some text comes into view.

        With `until`, this stops as soon as the text appears and gives up when
        the content stops moving, so it will not spin at the end of a list.
        """
        session = ctx.require()
        result = await session.scroll(
            direction,  # type: ignore[arg-type]
            ref=ref,
            target=target,
            until=until,
            max_scrolls=max_scrolls,
            idem_key=idem_key,
        )
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=MUTATING)
    @tool_errors
    async def ios_swipe(
        direction: Annotated[str, Field(description="up, down, left, or right.")],
        ref: RefArg = None,
        target: TargetArg = None,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Swipe once, for carousels, page views, and swipe-to-reveal rows."""
        session = ctx.require()
        result = await session.swipe(
            direction,  # type: ignore[arg-type]
            ref=ref,
            target=target,
            idem_key=idem_key,
        )
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=MUTATING)
    @tool_errors
    async def ios_drag(
        from_ref: Annotated[str, Field(description="Ref of the element to drag.")],
        to_ref: Annotated[str, Field(description="Ref of the element to drop onto.")],
        duration_s: Annotated[float, Field(description="Gesture duration.", ge=0.1, le=10)] = 0.6,
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Drag one element onto another, for reordering lists and moving items."""
        session = ctx.require()
        result = await session.drag(
            from_ref=from_ref, to_ref=to_ref, duration_s=duration_s, idem_key=idem_key
        )
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_press_button(
        name: Annotated[
            str,
            Field(
                description=(
                    "Hardware: home, volumeUp, volumeDown, siri. "
                    "Keyboard: enter, tab, delete, space, dismiss_keyboard."
                )
            ),
        ],
        idem_key: IdemArg = None,
    ) -> dict[str, Any]:
        """Press a hardware button or a keyboard key."""
        session = ctx.require()
        result = await session.press_button(name, idem_key=idem_key)
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=DESTRUCTIVE)
    @tool_errors
    async def ios_handle_alert(
        action: Annotated[str, Field(description="'accept' or 'dismiss'.")],
        button: Annotated[
            str | None, Field(description="Exact button label, when the alert has several.")
        ] = None,
    ) -> dict[str, Any]:
        """Answer a system alert.

        Read the alert text before choosing. Accepting blindly is how an agent
        grants a permission or confirms a deletion nobody wanted.
        """
        session = ctx.require()
        result = await session.handle_alert(action, button)  # type: ignore[arg-type]
        return session.redactor.mapping(ok(**result.to_dict()))

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_halt(
        reason: Annotated[str, Field(description="Why the session is being stopped.")] = "stopped",
    ) -> dict[str, Any]:
        """Stop this session from taking further action."""
        ctx.require().halt(reason)
        return ok(halted=True, reason=reason)

    @mcp.tool(annotations=MUTATING_IDEMPOTENT)
    @tool_errors
    async def ios_resume() -> dict[str, Any]:
        """Clear a halt after a human has decided it is safe to continue."""
        ctx.require().resume()
        return ok(halted=False)
