"""Turn internal errors into tool errors that keep their structure.

An agent recovers from a typed code plus a suggested next step; it cannot
recover from a stack trace. This decorator preserves both.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.exceptions import ToolError

from ios_mcp.errors import IosAutomationError

logger = logging.getLogger(__name__)


def tool_errors[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap a tool so IosAutomationError arrives as structured JSON."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except IosAutomationError as exc:
            logger.info("Tool %s failed: %s", fn.__name__, exc)
            raise ToolError(json.dumps(exc.to_dict())) from exc

    return wrapper


def ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}
