"""Stable element references across observations.

The model never writes XPath and never tracks WDA element UUIDs. It gets short
refs (``e12``) from a digest and hands them back. This table remembers enough
about each ref to re-find the element after the screen has moved, which is what
makes a stale ref recoverable instead of fatal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ios_mcp.perception.digest import Digest, DigestNode
from ios_mcp.wda.models import Rect


@dataclass(slots=True, frozen=True)
class Target:
    """A resolved element, ready to be acted on."""

    ref: str
    role: str
    label: str | None
    identifier: str | None
    rect: Rect
    enabled: bool
    resolved_via: str
    #: Set when resolution was not exact, so callers can surface the ambiguity.
    alternatives: tuple[str, ...] = ()

    @property
    def point(self) -> tuple[float, float]:
        return self.rect.center

    @property
    def describe(self) -> str:
        name = self.label or self.identifier or self.ref
        return f'{self.role} "{name}"'

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ref": self.ref,
            "role": self.role,
            "resolved_via": self.resolved_via,
            "rect": self.rect.to_dict(),
        }
        if self.label:
            out["label"] = self.label
        if self.identifier:
            out["id"] = self.identifier
        if self.alternatives:
            out["alternatives"] = list(self.alternatives)
        return out


@dataclass(slots=True)
class RefTable:
    """The refs handed out by the most recent digest, plus the previous one.

    Contract: ``update`` is called only when a digest is actually returned to
    the agent, so ``current`` always describes what the agent last saw. Digests
    fetched internally (to resolve a target, or to check whether an action
    changed anything) must not update the table, or resolution loses the memory
    it needs to detect that a ref has been reassigned.

    Keeping one generation of history is what lets a ref taken just before a
    scroll still resolve afterwards.
    """

    generation: int = 0
    current: dict[str, DigestNode] = field(default_factory=dict)
    previous: dict[str, DigestNode] = field(default_factory=dict)
    fingerprint: str | None = None

    def update(self, digest: Digest) -> None:
        self.previous = self.current
        self.current = {n.ref: n for n in digest.nodes}
        self.fingerprint = digest.fingerprint
        self.generation += 1

    def get(self, ref: str) -> DigestNode | None:
        return self.current.get(ref)

    def get_stale(self, ref: str) -> DigestNode | None:
        """A node from the previous generation, used to re-resolve a stale ref."""
        return self.previous.get(ref)

    def clear(self) -> None:
        self.previous = {}
        self.current = {}
        self.fingerprint = None
