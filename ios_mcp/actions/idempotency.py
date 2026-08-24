"""Act-once semantics for retried actions.

LangGraph reruns the node an ``interrupt()`` was raised from, so a node that
tapped Send before pausing for approval would tap Send twice on resume. The
same hazard exists for any client that retries after a timeout. An idempotency
key makes a repeat a no-op that returns the original result.

This has to exist from the first action, not be retrofitted: once callers are
written without keys there is no safe way to add them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class _Entry:
    value: Any
    stored_at: float


@dataclass(slots=True)
class IdempotencyCache:
    """Remembers results by key for the life of a session."""

    ttl_s: float = 900.0
    max_entries: int = 512
    _entries: dict[str, _Entry] = field(default_factory=dict)
    hits: int = 0

    def get(self, key: str | None) -> Any | None:
        if key is None:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.stored_at > self.ttl_s:
            del self._entries[key]
            return None
        self.hits += 1
        return entry.value

    def put(self, key: str | None, value: Any) -> None:
        if key is None:
            return
        if len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].stored_at)
            del self._entries[oldest]
        self._entries[key] = _Entry(value=value, stored_at=time.monotonic())

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
