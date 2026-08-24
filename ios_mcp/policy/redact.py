"""Strip sensitive strings before anything leaves the server.

An accessibility tree contains whatever is on screen, which on a real phone
means message bodies, card numbers, and email addresses. Those go into the
model's context, the transcript, and the audit trail unless removed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

from ios_mcp.config import PolicySettings

_PLACEHOLDER = "[redacted]"


@dataclass
class Redactor:
    """Applies the configured patterns to any text on its way out."""

    settings: PolicySettings
    _compiled: list[re.Pattern[str]] = field(default_factory=list)
    redactions: int = 0

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p) for p in self.settings.redact_patterns]

    @property
    def active(self) -> bool:
        return bool(self._compiled)

    def text(self, value: str | None) -> str | None:
        if not value or not self._compiled:
            return value
        out = value
        for pattern in self._compiled:
            out, count = pattern.subn(_PLACEHOLDER, out)
            self.redactions += count
        return out

    def payload(self, value: Any) -> Any:
        """Walk a JSON-shaped structure, redacting every string in it."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.payload(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.payload(v) for v in value]
        return value

    def mapping(self, value: dict[str, Any]) -> dict[str, Any]:
        """``payload`` for a dict, typed so tool return values stay checkable."""
        return cast("dict[str, Any]", self.payload(value))
