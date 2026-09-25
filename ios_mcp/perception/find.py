"""Search the raw accessibility tree, including what the digest dropped.

`ios_observe(query=...)` already filters the digest, and for most questions
that is the right tool: what comes back carries refs and can be tapped. But it
can only answer out of what the digest kept, and the digest's whole job is to
throw most of the tree away. Three of the hardest bugs this project has had
were the same shape -- something was on screen, the agent could not name it,
and nobody could tell whether perception had dropped it or the agent had
invented it. Answering that took a human reading a tree by hand every time.

This module answers it directly. It walks the tree WDA returned, before noise
removal, dedupe, collapsing and the token budget run, and reports each match
with whether the digest kept it. A match tagged ``hidden`` is the diagnosis:
the screen really does say that, and perception is why the agent cannot reach
it.

Deliberately returns no refs. `RefTable.update()` is called only when a digest
is returned to the agent, because that memory is what lets a reassigned ref be
detected, and handing back refs here would force an update and make this an
observation wearing a different name. A caller acts on a result by passing the
label or id reported here as an ordinary ``target``, which resolution already
handles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ios_mcp.perception.digest import Digest, Scrubber, scrubbed
from ios_mcp.perception.roles import role_of
from ios_mcp.wda.models import Rect, SnapshotNode

#: How many matches come back when the caller does not say.
DEFAULT_LIMIT = 20


@dataclass(slots=True, frozen=True)
class FindMatch:
    """One node of the raw tree whose text matched."""

    role: str
    label: str | None
    value: str | None
    identifier: str | None
    rect: Rect
    #: WDA's own visibility flag. Trustworthy where the rects around it are
    #: not: on a virtualised list every offscreen row can report the same `y`.
    visible: bool
    #: Whether the digest kept this node. False means the screen carries the
    #: text and perception is what stands between the agent and it.
    shown: bool
    #: Which field matched: label, value, id or placeholder. The distinction
    #: matters because only some of them are targetable at every tier.
    matched_on: str

    def render(self) -> str:
        where = "shown " if self.shown else "hidden"
        parts = [f"  {where} {self.role:12}"]
        if self.label:
            parts.append(f'"{self.label}"')
        if self.value and self.value != self.label:
            parts.append(f"={self.value!r}")
        if self.identifier:
            parts.append(f"id={self.identifier}")
        if not self.visible:
            parts.append("offscreen")
        x, y = self.rect.center
        parts.append(f"@({x:.0f},{y:.0f})")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "role": self.role,
            "shown": self.shown,
            "matched_on": self.matched_on,
            "visible": self.visible,
            "rect": self.rect.to_dict(),
        }
        if self.label:
            out["label"] = self.label
        if self.value:
            out["value"] = self.value
        if self.identifier:
            out["id"] = self.identifier
        return out


@dataclass(slots=True, frozen=True)
class FindResult:
    text: str
    matches: tuple[FindMatch, ...]
    #: Every match in the tree, including any past ``limit``.
    total: int
    #: How many of ``total`` the digest did not keep.
    hidden: int
    #: Carried from the digest so a screen with no readable tree says so
    #: rather than merely looking empty. See ADR 0007.
    notes: tuple[str, ...] = ()
    #: See `Digest.scrub`. A find reads the raw tree, which is exactly where a
    #: value the digest trimmed would still be sitting in full.
    scrub: Scrubber | None = field(default=None, compare=False, repr=False)

    def render(self) -> str:
        return scrubbed(self.scrub, self._render())

    def _render(self) -> str:
        if not self.total:
            lines = [f"find {self.text!r}: nothing in the accessibility tree matches"]
            lines.extend(f"note: {note}" for note in self.notes)
            return "\n".join(lines)
        head = f"find {self.text!r}: {self.total} match{'' if self.total == 1 else 'es'}"
        if self.hidden:
            head += f", {self.hidden} not in the digest"
        lines = [head]
        lines.extend(match.render() for match in self.matches)
        if len(self.matches) < self.total:
            lines.append(f"  ... {self.total - len(self.matches)} more, raise limit to see them")
        if self.hidden:
            lines.append(
                "note: a hidden match is on screen but the digest dropped it. "
                "Name it by its id, or by the text shown above."
            )
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "text": self.text,
            "matches": [match.to_dict() for match in self.matches],
            "total": self.total,
            "hidden": self.hidden,
            "rendered": self.render(),
            "notes": list(self.notes),
        }
        return self.scrub.mapping(out) if self.scrub is not None else out


def find_in_tree(
    root: SnapshotNode, text: str, digest: Digest, *, limit: int = DEFAULT_LIMIT
) -> FindResult:
    """Every node of ``root`` whose text contains ``text``, tagged by visibility.

    ``digest`` must be the digest built from this same ``root``; it is what
    ``shown`` is decided against. Passing a stale one silently mislabels every
    match, which is why `IosSession.find` builds both from one fetch.
    """
    needle = _normalise(text)
    if not needle:
        return FindResult(text=text, matches=(), total=0, hidden=0, notes=tuple(digest.notes))

    matches: list[FindMatch] = []
    seen: set[tuple[Any, ...]] = set()
    for node in root.walk():
        matched = _matched_field(node, needle)
        if matched is None:
            continue
        field, text_that_matched = matched
        key = (node.type, node.label, node.value, node.identifier, node.rect)
        if key in seen:
            # The raw tree repeats one control several times; a reader asking
            # what is on screen does not need the same answer twice.
            continue
        seen.add(key)
        matches.append(
            FindMatch(
                role=role_of(node.type),
                label=node.label,
                value=node.value,
                identifier=node.identifier,
                rect=node.rect,
                visible=node.visible,
                shown=_is_shown(node, digest, _normalise(text_that_matched)),
                matched_on=field,
            )
        )

    matches.sort(key=lambda m: (round(m.rect.y), round(m.rect.x)))
    hidden = sum(1 for m in matches if not m.shown)
    return FindResult(
        text=text,
        matches=tuple(matches[:limit]),
        total=len(matches),
        hidden=hidden,
        notes=tuple(digest.notes),
    )


def _matched_field(node: SnapshotNode, needle: str) -> tuple[str, str] | None:
    """Which of a node's strings contains ``needle``, and the whole of it.

    The string comes back as well as the field name because `_is_shown` has to
    ask about this element rather than about the query. A needle of "contact 0"
    matches ten rows, and if one of them survives compaction, asking whether
    the digest contains the *needle* answers yes for all ten.

    The four fields are the ones `digest._searchable_text` narrows by, so a
    find and `observe(query=...)` agree about what "matches" means. They are
    kept as separate cases here rather than shared, because which field
    matched is itself the answer: a hit on a value is a different problem from
    a hit on an id, and only the caller can tell which one it can act on.
    """
    for name, value in (
        ("label", node.label),
        ("value", node.value),
        ("id", node.identifier),
        ("placeholder", node.placeholder),
    ):
        if value and needle in _normalise(value):
            return name, value
    return None


def _is_shown(node: SnapshotNode, digest: Digest, matched_text: str) -> bool:
    """Whether this element's own matched text is reachable through the digest.

    Not whether this node survived, which is a different and much less useful
    question. iOS reports one control several times and the digest folds them
    together, so a node being gone says nothing about whether its words are.
    Three rules, and every one of them was put here by a screen that broke the
    rule before it.

    **Same id.** The strongest claim available, and the one an unlabelled drawn
    control is reached by.

    **Same text exactly.** A Settings row emits a `StaticText` echoing the
    cell's label; the digest keeps the switch at the trailing edge and drops
    both. `_dedupe_colocated` gave the survivor the switch's tight rect, so the
    two do not overlap at all, and any position-based test calls those words
    hidden while they sit in the digest one line above. Exact text is what
    survives perception moving the geometry around.

    **Text contained, at overlapping coordinates.** A button's label often
    contains its child text's, and the child is genuinely reachable through the
    parent. But containment alone is not enough, and real Settings says why: at
    a tight budget the row labelled `Siri` is dropped while `Optimizing Search
    and Siri` 468 points up survives. Containment alone calls the dropped row
    reachable, and an agent that then targets "Siri" resolves by substring onto
    the wrong row. Requiring the two to overlap keeps the parent case and
    refuses the coincidence.

    The comparison is against ``matched_text``, this node's whole field, not
    against the query. Judging by the query makes the answer a property of the
    search rather than of the element, so one surviving row would vouch for
    every row that happens to share its prefix.

    Hidden then means what it says: these words are on screen, and naming them
    will not reach this element.
    """
    for element in digest.nodes:
        if node.identifier and element.identifier == node.identifier:
            return True
        texts = [_normalise(t) for t in (element.label, element.value, element.identifier) if t]
        if any(matched_text == text for text in texts):
            return True
        if any(matched_text in text for text in texts) and _overlaps(node.rect, element.rect):
            return True
    return False


def _overlaps(a: Rect, b: Rect) -> bool:
    """Whether either rect's centre falls inside the other.

    Proportional rather than strict, because real iOS rects overhang their own
    parents: a toggle at `x=305 w=63` sits inside a row at `x=36 w=330`.
    """
    return b.contains(*a.center) or a.contains(*b.center)


def _normalise(text: str) -> str:
    return " ".join(text.split()).strip().lower()
