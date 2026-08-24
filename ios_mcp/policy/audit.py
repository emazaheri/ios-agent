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

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class AuditTrail:
    entries: list[AuditEntry] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

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
    ) -> AuditEntry:
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
        }

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "steps": [e.to_dict() for e in self.entries]}

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path
