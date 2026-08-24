"""Tool annotations advertised to clients.

A client that knows which tools only read can offer them without a prompt, and
one that knows which are destructive can gate them. Getting these wrong is a
safety issue, so they live in one place rather than being retyped per tool.
"""

from __future__ import annotations

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

#: Changes device state, but re-running it lands in the same place.
MUTATING_IDEMPOTENT = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

#: Changes device state, and running it twice is not the same as running it once.
MUTATING = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

#: May do something the user cannot undo. The policy gate also guards these.
DESTRUCTIVE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
