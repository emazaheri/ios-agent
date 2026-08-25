"""What an action reports back, and how screens are diffed.

Every action returns the resulting screen. That halves the round-trips of an
observe/act/observe loop, which is the single biggest lever on both latency
and token cost. When the screen is structurally similar a delta is returned
instead of a full digest, so long flows stay cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ios_mcp.perception.digest import Digest, DigestNode
from ios_mcp.perception.refs import Target
from ios_mcp.wda.models import AlertInfo


@dataclass(slots=True)
class DigestDelta:
    """What changed between two observations of the same screen."""

    added: list[DigestNode] = field(default_factory=list)
    removed: list[DigestNode] = field(default_factory=list)
    changed: list[tuple[DigestNode, DigestNode]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def render(self) -> str:
        if self.empty:
            return "no visible change"
        lines: list[str] = []
        for node in self.added:
            lines.append(f"+ {node.render()}")
        for node in self.removed:
            lines.append(f"- {node.render()}")
        for before, after in self.changed:
            lines.append(
                f"~ {after.render()}   (was "
                f"{before.value if before.value is not None else 'unset'})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """The rendered form only, for the same reason as ``Digest.to_dict``."""
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
            "text": self.render(),
        }


@dataclass(slots=True)
class ActionResult:
    """The outcome of one action, plus the screen it produced."""

    action: str
    ok: bool
    screen_changed: bool
    elapsed_ms: int
    target: Target | None = None
    digest: Digest | None = None
    delta: DigestDelta | None = None
    alert: AlertInfo | None = None
    recovered: bool = False
    from_cache: bool = False
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.action,
            "ok": self.ok,
            "screen_changed": self.screen_changed,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.target is not None:
            out["target"] = self.target.to_dict()
        if self.alert is not None:
            out["alert"] = self.alert.to_dict()
            out["hint"] = (
                "An alert is blocking the screen. Resolve it with ios_handle_alert "
                "before continuing."
            )
        if self.recovered:
            out["recovered"] = True
            out["note"] = "WebDriverAgent was restarted mid-action and the session restored."
        if self.from_cache:
            out["from_cache"] = True
            out["note"] = "Replayed from the idempotency cache; the device was not touched."
        if self.note and "note" not in out:
            out["note"] = self.note
        if self.delta is not None:
            out["change"] = self.delta.to_dict()
        if self.digest is not None:
            out["screen"] = self.digest.to_dict()
        return out


def diff_digests(before: Digest, after: Digest) -> DigestDelta:
    """Match elements by identity, not by ref, since refs are reassigned."""
    before_map = {_identity(n): n for n in before.nodes}
    after_map = {_identity(n): n for n in after.nodes}

    delta = DigestDelta()
    for key, node in after_map.items():
        previous = before_map.get(key)
        if previous is None:
            delta.added.append(node)
        elif _state_of(previous) != _state_of(node):
            delta.changed.append((previous, node))
    for key, node in before_map.items():
        if key not in after_map:
            delta.removed.append(node)
    return delta


def _identity(node: DigestNode) -> str:
    """A key that survives ref reassignment and small position changes."""
    if node.identifier:
        return f"id:{node.identifier}"
    return f"{node.role}:{node.label or ''}:{int(node.rect.x) // 8}"


def _state_of(node: DigestNode) -> tuple[Any, ...]:
    return (node.value, node.enabled, node.selected, int(node.rect.y) // 4)
