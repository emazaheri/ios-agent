"""Resources and the operator prompt.

Resources are cheaper than tool calls for things a client may want to poll, and
the prompt ships the operating instructions with the server rather than leaving
each client to reinvent them.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from ios_mcp.config import Settings
from ios_mcp.devices.discovery import list_devices
from ios_mcp.server.context import ServerContext

OPERATOR_PROMPT = """\
You are driving an iOS device through the ios-automation MCP server.

## The loop

1. `ios_open_session` once, then `ios_launch_app` or `ios_open_url` to arrive.
   A deep link (`prefs:root=WIFI`, `maps://?q=...`) is far cheaper than tapping
   through navigation.
2. `ios_observe` gives a compact list of elements, each with a ref like `e7`.
3. Act with `ios_tap`, `ios_type`, `ios_scroll`, `ios_set_value`, passing refs.
4. Actions return the resulting screen, so do not call `ios_observe` again
   straight afterwards. Read the `change` field instead.
5. Verify before declaring success. Confirm the state you intended is on screen.

## Choosing targets

- Prefer a `ref` over a `target` description. Refs are exact.
- Refs come from the most recent observation. If one is rejected, observe again.
- If two elements share a label, the server refuses rather than guessing. Pass a
  ref or narrow with `role`.
- If an element has no accessibility label, call `ios_screenshot` with
  `annotate_refs=true` and choose from the image.

## Getting around

- `ios_scroll(until="...")` beats scrolling a fixed number of times.
- `ios_wait_for` beats guessing how long something takes.
- Use `ios_set_value` for switches, not `ios_tap`. Tapping a switch that is
  already on turns it off.

## Safety

- Anything resembling Send, Pay, Buy, Delete, Confirm, or Sign Out needs
  approval. You will get an `action_requires_approval` error carrying a
  signature; confirm with the user, then repeat the call with `approve=<sig>`.
- Never put a real password or one-time code in `ios_type`. Use
  `ios_type_secret` with a stored reference.
- If an action returns an `alert`, read its text before answering. Do not accept
  permission prompts the user did not ask for.
- If you find yourself back on a screen you have already seen several times,
  stop and say what is blocking you rather than trying again.

## When stuck

`ios_doctor` explains setup failures. `ios_get_logs` shows what the app is
doing. `ios_export_trace` shows what you have already tried.
"""


def register(mcp: FastMCP, cfg: Settings, ctx: ServerContext) -> None:
    @mcp.resource("ios://devices", mime_type="application/json")
    async def devices_resource() -> str:
        """Every simulator and attached iPhone, with readiness."""
        devices = await list_devices(cfg)
        return json.dumps([d.to_dict() for d in devices], indent=2)

    @mcp.resource("ios://session", mime_type="application/json")
    async def session_resource() -> str:
        """The current session, or a note that none is open."""
        if ctx.session is None:
            return json.dumps({"open": False})
        return json.dumps(
            {"open": True, "lease": ctx.session.lease.to_dict()}, indent=2, default=str
        )

    @mcp.resource("ios://session/screen", mime_type="application/json")
    async def screen_resource() -> str:
        """The last observed screen, without spending a tool call."""
        session = ctx.require()
        digest = session._last_digest or await session.observe()
        return json.dumps(session.redactor.payload(digest.to_dict()), indent=2)

    @mcp.resource("ios://capabilities", mime_type="application/json")
    async def capabilities_resource() -> str:
        """Which tools work on the attached device.

        Several capabilities are Simulator-only, and an agent that knows this
        up front does not waste a turn discovering it.
        """
        kind = ctx.session.lease.device.kind if ctx.session else None
        simulator = kind == "simulator"
        payload: dict[str, Any] = {
            "device_kind": kind,
            "supported": {
                "ios_set_permission": simulator,
                "ios_set_device_state.appearance": simulator,
                "ios_set_device_state.location": simulator,
                "ios_set_device_state.clean_status_bar": simulator,
                "ios_set_device_state.orientation": True,
                "ios_install_app": True,
                "ios_get_logs": True,
            },
            "note": (
                None
                if simulator
                else "Privacy permissions and simulated state need a Simulator; "
                "on a real device drive Settings or answer the alert."
            ),
        }
        return json.dumps(payload, indent=2)

    @mcp.prompt
    def ios_operator() -> str:
        """How to drive an iOS device well: the observe, act, verify loop and its rules."""
        return OPERATOR_PROMPT
