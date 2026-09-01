"""FastMCP server assembly.

Layers 1 through 4 are a plain async library with no MCP imports; this module
is the only place that knows about the protocol. That separation is what lets a
future LangGraph agent import the same code in-process instead of paying MCP
round-trip cost for latency-critical steps.
"""

from __future__ import annotations

import logging
from importlib import metadata

from fastmcp import FastMCP

from ios_mcp.config import Settings, get_settings
from ios_mcp.server.context import ServerContext

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Drive an iOS device or Simulator to accomplish a user's task.

Core loop: observe -> act -> verify.

1. Call `ios_doctor` once if anything looks misconfigured; it tells you exactly
   what to fix.
2. `ios_open_session` to attach to a device, then `ios_launch_app` or
   `ios_open_url` to get to the right place. Deep links are far cheaper than
   tapping through navigation.
3. `ios_observe` returns a compact digest of the screen where each element has a
   short ref like `e12`. Pass those refs to the action tools.
4. Action tools already return the resulting screen, so you rarely need to call
   `ios_observe` again right after acting.
5. If an element has no accessibility label, call `ios_screenshot` with
   `annotate_refs=true` and work from the image.

Never guess coordinates when a ref exists. Never retype a password into
`ios_type`; use `ios_type_secret` with a keychain reference.
"""


def _version() -> str:
    """This package's version, or a placeholder when it is not installed."""
    try:
        return metadata.version("ios-mcp")
    except metadata.PackageNotFoundError:  # running from a source checkout
        return "0.0.0+unknown"


def build_server(settings: Settings | None = None) -> FastMCP:
    """Construct the MCP server with every tool module registered."""
    cfg = settings or get_settings()
    ctx = ServerContext(cfg)

    # Without a version FastMCP reports its own, so a client asking what it is
    # talking to was told "4.0.0", which is the framework rather than this.
    mcp: FastMCP = FastMCP(name="ios-automation", version=_version(), instructions=INSTRUCTIONS)

    from ios_mcp.server import (
        resources,
        tools_act,
        tools_app,
        tools_env,
        tools_perceive,
        tools_session,
    )

    tools_session.register(mcp, cfg)
    tools_app.register(mcp, cfg, ctx)
    tools_perceive.register(mcp, cfg, ctx)
    tools_act.register(mcp, cfg, ctx)
    tools_env.register(mcp, cfg, ctx)
    resources.register(mcp, cfg, ctx)

    # Stash the context so tests and embedders can reach the pool.
    mcp.ios_context = ctx  # type: ignore[attr-defined]
    return mcp
