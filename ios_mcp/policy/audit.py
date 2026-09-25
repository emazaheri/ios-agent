"""Ordered record of everything a session did.

Serves three purposes at once: forensics after something goes wrong, replay as
a regression test, and few-shot examples for the future agent. Secrets never
enter it, and redaction is applied before anything is stored.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ios_mcp.policy.faults import attribute
from ios_mcp.policy.redact import Redactor


@dataclass(slots=True)
class AuditEntry:
    seq: int
    at: float
    action: str
    args: dict[str, Any]
    ok: bool
    resolved_via: str | None = None
    target: str | None = None
    fingerprint: str | None = None
    screen_changed: bool | None = None
    elapsed_ms: int | None = None
    error: str | None = None
    code: str | None = None
    details: dict[str, Any] | None = None
    recovered: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class AuditTrail:
    entries: list[AuditEntry] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    #: Applied as each entry is recorded, which is what the module docstring
    #: always claimed and what did not happen: targets were stored as the raw
    #: label, so a card number on a button reached the trail, the exported trace
    #: and the terminal `ios-agent run` prints the trail to. None leaves an entry
    #: exactly as given, for a trail built outside a session.
    redactor: Redactor | None = field(default=None, repr=False)

    def record(
        self,
        action: str,
        args: dict[str, Any],
        *,
        ok: bool,
        resolved_via: str | None = None,
        target: str | None = None,
        fingerprint: str | None = None,
        screen_changed: bool | None = None,
        elapsed_ms: int | None = None,
        error: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        recovered: bool | None = None,
    ) -> AuditEntry:
        scrub = self.redactor
        if scrub is not None:
            args = scrub.payload(args)
            target = scrub.text(target)
            error = scrub.text(error)
            details = scrub.payload(details) if details is not None else None
        entry = AuditEntry(
            seq=len(self.entries) + 1,
            at=time.time(),
            action=action,
            args=args,
            ok=ok,
            resolved_via=resolved_via,
            target=target,
            fingerprint=fingerprint,
            screen_changed=screen_changed,
            elapsed_ms=elapsed_ms,
            error=error,
            code=code,
            details=details,
            recovered=recovered,
        )
        self.entries.append(entry)
        return entry

    @property
    def failures(self) -> list[AuditEntry]:
        return [e for e in self.entries if not e.ok]

    def summary(self) -> dict[str, Any]:
        by_tier: dict[str, int] = {}
        for entry in self.entries:
            if entry.resolved_via:
                by_tier[entry.resolved_via] = by_tier.get(entry.resolved_via, 0) + 1
        return {
            "steps": len(self.entries),
            "failures": len(self.failures),
            "duration_s": round(time.time() - self.started_at, 1),
            "resolution_tiers": by_tier,
            # Which failures were whose. Sums to "failures", which is what
            # makes it readable at a glance.
            "faults": attribute(self.entries),
            # A device fault the auto-heal absorbed. Kept out of the histogram
            # above precisely so that sum holds: the action succeeded, and a
            # session that recovered from four runner crashes is still not a
            # healthy one.
            "absorbed_device_faults": sum(1 for e in self.entries if e.ok and e.recovered),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "steps": [e.to_dict() for e in self.entries]}

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path
