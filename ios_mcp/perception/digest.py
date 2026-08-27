"""Turn a raw accessibility tree into a compact UI Digest.

A stock Settings screen produces roughly 40,000 tokens of WDA page source. An
agent loop that re-reads that after every tap is unusable: it exhausts the
context window in a handful of steps and pays for it in latency too. This
module is the compaction stage that makes the loop affordable, and it is where
most of this project's leverage sits.

The pipeline is: prune -> classify -> collapse repeats -> score -> budget ->
assign refs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

from ios_mcp.config import DigestSettings
from ios_mcp.perception.roles import (
    ALWAYS_COLLAPSE,
    COLLAPSE_WHEN_EMPTY,
    CONTAINER_ROLES,
    DECORATIVE_LABELS,
    DRAWN_CONTROL_ROLES,
    INTERACTIVE_ROLES,
    ROLE_PRECEDENCE,
    SCROLLABLE_ROLES,
    STATEFUL_ROLES,
    role_of,
)
from ios_mcp.wda.models import Rect, SnapshotNode

#: Rough characters-per-token for English UI labels.
_CHARS_PER_TOKEN = 4

#: How much of a control must sit inside another before they are judged the
#: same thing. Not 1.0: real iOS rects overhang their own parents slightly.
_CONTAINMENT_RATIO = 0.8


@dataclass(slots=True, frozen=True)
class DigestNode:
    """One element the agent can see and, usually, act on."""

    ref: str
    role: str
    label: str | None
    value: str | None
    identifier: str | None
    rect: Rect
    enabled: bool
    actionable: bool
    scrollable: bool
    selected: bool
    depth: int
    repeat_count: int = 1

    @property
    def text(self) -> str | None:
        return self.label or self.value or self.identifier

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ref": self.ref, "role": self.role}
        for key, value in (
            ("label", self.label),
            ("value", self.value),
            ("id", self.identifier),
        ):
            if value:
                out[key] = value
        out["rect"] = self.rect.to_dict()
        if not self.enabled:
            out["enabled"] = False
        if self.selected:
            out["selected"] = True
        if self.scrollable:
            out["scrollable"] = True
        if self.repeat_count > 1:
            out["repeat_count"] = self.repeat_count
        return out

    def render(self, *, coordinates: bool = True) -> str:
        parts = [f"{self.ref:<4}", f"{self.role:<12}"]
        if self.label:
            parts.append(f'"{_truncate(self.label)}"')
        if self.value and self.role in STATEFUL_ROLES:
            parts.append(f"={_truncate(self.value, 40)}")
        elif _is_content_value(self.value, self.label):
            # A read-only node whose value is prose, not state. iOS apps use
            # this to separate a field's name from its content: a Hinge prompt
            # arrives as label "Date prompt:" with value "Let's get together",
            # and showing only the label hands the agent the question while
            # hiding the answer.
            parts.append(f'"{_truncate(str(self.value))}"')
        if self.identifier and self.identifier != self.label:
            parts.append(f"id={_truncate(self.identifier, 32)}")
        if self.selected:
            parts.append("selected")
        if not self.enabled:
            parts.append("disabled")
        if self.scrollable:
            parts.append("scrollable")
        if self.repeat_count > 1:
            parts.append(f"(+{self.repeat_count - 1} more like this)")
        if coordinates:
            parts.append(f"@({int(self.rect.center[0])},{int(self.rect.center[1])})")
        return " ".join(parts)


@dataclass(slots=True)
class Digest:
    """Everything the agent needs to know about the current screen."""

    nodes: list[DigestNode]
    fingerprint: str
    app: str | None = None
    title: str | None = None
    total_nodes: int = 0
    truncated: bool = False
    #: Things the agent cannot learn from the elements themselves, chiefly that
    #: this screen's content is somewhere the accessibility tree does not go.
    #: Rendered rather than kept in `meta`, because the point is to reach the
    #: model, and the rendered text is what the model reads.
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def by_ref(self, ref: str) -> DigestNode | None:
        return next((n for n in self.nodes if n.ref == ref), None)

    @property
    def actionable(self) -> list[DigestNode]:
        return [n for n in self.nodes if n.actionable]

    def render(self, *, coordinates: bool = True) -> str:
        header = f"screen: {self.app or 'unknown'}"
        if self.title:
            header += f' / "{self.title}"'
        header += f"  fp={self.fingerprint[:8]}"
        lines = [header]
        lines.extend(n.render(coordinates=coordinates) for n in self.nodes)
        if self.truncated:
            lines.append(
                f"... {self.total_nodes - len(self.nodes)} more elements omitted "
                f"(narrow with ios_observe(query=...) or region=...)"
            )
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)

    def to_dict(self, *, include_elements: bool = False) -> dict[str, Any]:
        """Serialise for the wire.

        ``elements`` is omitted by default because it duplicates ``text``:
        every ref, role, label and coordinate the structured list carries is
        already in the rendered form, at roughly twice the tokens. Since both
        would be pushed into the model's context, sending both means paying
        twice for one screen. Programmatic consumers that want the structure
        can ask for it.
        """
        payload: dict[str, Any] = {
            "app": self.app,
            "title": self.title,
            "fingerprint": self.fingerprint[:8],
            "element_count": len(self.nodes),
            "total_elements": self.total_nodes,
            "truncated": self.truncated,
            "text": self.render(),
            "notes": list(self.notes),
            **self.meta,
        }
        if include_elements:
            payload["elements"] = [n.to_dict() for n in self.nodes]
        return payload

    def estimated_tokens(self) -> int:
        return len(self.render()) // _CHARS_PER_TOKEN


# --- internal working node -------------------------------------------------


@dataclass(slots=True)
class _Candidate:
    node: SnapshotNode
    role: str
    depth: int
    repeat_count: int = 1
    score: float = 0.0


def build_digest(
    root: SnapshotNode,
    settings: DigestSettings,
    *,
    app: str | None = None,
    query: str | None = None,
    region: Rect | None = None,
) -> Digest:
    """Compact a raw accessibility tree into a Digest.

    ``query`` keeps only elements whose text matches, and ``region`` only those
    intersecting a screen area. Both exist so an agent that hits the budget on a
    dense screen can zoom in without paying for the whole tree again.
    """
    screen = region or root.rect
    title, title_is_chrome = _screen_title(root)
    # Seed the echo filter with the screen title, but only when the title came
    # from a navigation bar: that bar's StaticText always repeats it, and the
    # digest header already reports it.
    #
    # A drawn header is the opposite case and seeding it deleted content. Real
    # Settings shows search results under no navigation bar at all, so
    # `No Results for "Airplane"` was promoted to the title and then dropped as
    # an echo of itself, taking the only node carrying that text with it. The
    # integration suite caught it. Duplicating a header line costs a few tokens;
    # deleting the screen's one piece of content costs the answer.
    candidates = _collect(
        root,
        settings,
        screen=screen,
        depth=0,
        inherited_text=title if title_is_chrome else None,
    )
    candidates = _dedupe_colocated(candidates)
    candidates = _collapse_repeats(candidates, settings)

    if query:
        # Match everything the rendered line shows, not just the label. The
        # digest prints `id=` and a content value; narrowing by a string the
        # digest itself printed has to find it.
        needle = query.lower()
        candidates = [
            c
            for c in candidates
            if any(needle in text.lower() for text in _searchable_text(c.node))
        ]
    if region:
        candidates = [c for c in candidates if _intersects(c.node.rect, region)]

    total = len(candidates)
    for c in candidates:
        c.score = _salience(c, screen)

    kept, truncated = _apply_budget(candidates, settings)
    kept.sort(key=lambda c: (round(c.node.rect.y), round(c.node.rect.x)))

    nodes = [
        DigestNode(
            ref=f"e{i + 1}",
            role=c.role,
            label=c.node.label,
            value=_value_of(c),
            identifier=c.node.identifier,
            rect=c.node.rect,
            enabled=c.node.enabled,
            actionable=_is_actionable(c),
            scrollable=c.role in SCROLLABLE_ROLES,
            selected=_is_selected(c.node),
            depth=c.depth,
            repeat_count=c.repeat_count,
        )
        for i, c in enumerate(kept)
    ]

    return Digest(
        nodes=nodes,
        fingerprint=fingerprint_of(nodes, app, title),
        app=app or root.name,
        title=title,
        total_nodes=total,
        truncated=truncated,
        notes=_unreachable_content_notes(nodes, screen),
    )


#: A screen this empty is either genuinely bare or unreadable, and the
#: detectors below are what tell those apart.
_SPARSE = 3


def _unreachable_content_notes(nodes: list[DigestNode], screen: Rect) -> list[str]:
    """Say when the content is somewhere the accessibility tree does not go.

    Neither case is fixable here. Web content is a second tree behind a context
    switch this stack has no concept of, and a canvas has no tree at all. What
    is fixable is the silence: a WebView screen returns a navigation bar and a
    scroll container, which is indistinguishable from a screen that genuinely
    has two things on it. An agent that cannot tell those apart taps at nothing
    and reports the app is broken.

    Both detectors are deliberately narrow, and are asserted silent on every
    screen this project already reads. A warning that cries wolf is worse than
    no warning, because the agent learns to ignore it.
    """
    notes: list[str] = []
    readable = [n for n in nodes if n.label or n.value]

    webviews = [n for n in nodes if n.role == "webview"]
    if webviews and not any(_encloses(w.rect, n.rect) for w in webviews for n in readable):
        notes.append(
            "This screen's content is inside a WebView, which the accessibility "
            "tree does not reach. Use ios_screenshot to see it."
        )

    # Only when the first detector found nothing: web content is not drawn
    # content, and saying both says neither.
    if not notes and len(nodes) <= _SPARSE and not readable and _covers(nodes, screen):
        notes.append(
            "This screen exposes no accessibility data; it is drawn rather than "
            "composed. Use ios_screenshot with annotate_refs=true."
        )
    return notes


def _encloses(outer: Rect, inner: Rect) -> bool:
    """Whether ``inner`` sits inside ``outer`` and is not ``outer`` itself."""
    return outer.area > inner.area and _overlap_ratio(inner, outer) >= 0.8


def _covers(nodes: list[DigestNode], screen: Rect) -> bool:
    """Whether one element takes up most of the screen on its own."""
    return any(n.rect.area >= screen.area * 0.5 for n in nodes)


def fingerprint_of(
    nodes: list[DigestNode], app: str | None = None, title: str | None = None
) -> str:
    """A stable hash of screen structure and state.

    Used for three things: deciding whether an action changed anything, driving
    the post-action settle loop, and detecting an agent looping between the same
    two screens. Positions are rounded to 4px so animation jitter does not
    register as a change, but real layout shifts still do.

    The title is part of the hash because a split view (any iPad, and iPhone
    landscape) keeps most of the screen identical while navigating: without the
    title, a real navigation looks like an action that did nothing.
    """
    parts = [app or "", title or ""]
    for n in sorted(nodes, key=lambda n: (n.role, n.label or "", n.identifier or "")):
        parts.append(
            f"{n.role}|{n.label or ''}|{n.identifier or ''}|"
            f"{n.value if n.role in STATEFUL_ROLES else ''}|"
            f"{int(n.rect.x) // 4}|{int(n.rect.y) // 4}|{int(n.selected)}"
        )
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


# --- pipeline stages -------------------------------------------------------


def _collect(
    node: SnapshotNode,
    settings: DigestSettings,
    *,
    screen: Rect,
    depth: int,
    inherited_text: str | None = None,
) -> list[_Candidate]:
    """Depth-first prune. A container survives only if it is a useful target.

    ``inherited_text`` carries the label of the nearest kept ancestor so that a
    StaticText child echoing its cell's label can be dropped. iOS emits that
    echo for nearly every list row, so on a list-heavy screen this alone halves
    the digest.
    """
    role = role_of(node.type)
    keep = not _is_noise(node, role, settings, screen, inherited_text) and not _is_wrapper(
        node, role
    )
    own_text = _text_of(node)
    child_inherited = own_text if (keep and own_text) else inherited_text

    children: list[_Candidate] = []
    for child in node.children:
        children.extend(
            _collect(
                child,
                settings,
                screen=screen,
                depth=depth + (1 if keep else 0),
                inherited_text=child_inherited,
            )
        )

    if not keep:
        return children
    return [_Candidate(node=node, role=role, depth=depth), *children]


def _is_wrapper(node: SnapshotNode, role: str) -> bool:
    """True when the node exists only to hold other nodes.

    A container carrying its own text is not a wrapper, it is content. Only
    `application` and `window` collapse unconditionally, because their labels
    are the app name and noise.

    `other` and `group` used to collapse unconditionally as well, which no
    Apple app exposes as wrong: Settings keeps its text in `StaticText` and
    `Cell`. Third-party apps hang the readable version of a card on the
    wrapping `Other`, and on a real Hinge profile that habit made the whole
    prompt invisible to the agent.
    """
    if role in SCROLLABLE_ROLES:
        return False  # the agent needs these as scroll targets
    if role in ALWAYS_COLLAPSE:
        return True
    if role in COLLAPSE_WHEN_EMPTY:
        # An `other` or a `group` is where an app hangs its own meaning, so it
        # survives on anything that names it. `_text_of` was too narrow: it
        # reads label, value and placeholder and never the accessibility id,
        # so a container named only by a `testID` collapsed to nothing and
        # took its contents with it.
        return not _identity_text(node)
    if role in CONTAINER_ROLES:
        # The rest is chrome. A navigation bar's label is the screen title and
        # the digest header already reports it.
        return not _text_of(node)
    return False


def _is_noise(
    node: SnapshotNode,
    role: str,
    settings: DigestSettings,
    screen: Rect,
    inherited_text: str | None = None,
) -> bool:
    if not node.visible:
        return True
    rect = node.rect
    if rect.width < settings.min_element_size_px or rect.height < settings.min_element_size_px:
        return True
    if not _intersects(rect, screen):
        return True
    text = _text_of(node)
    # An unlabelled decoration the agent can neither read nor act on. An
    # accessibility id is not a label, but it is a handle, and outside Apple's
    # own apps it is frequently the only one a drawn control has: a heart icon
    # rendered into an image arrives with a name and nothing else. Dropping
    # both the icon and the spacer beside it leaves the agent unable to see the
    # one it needs, and keeping both floods the budget, so the id is the line.
    #
    # Cost of drawing it here, measured across the eleven golden flows: 8,348
    # tokens to 8,610, +3.1%, worst step 433 to 445 against a ceiling of 900,
    # and no movement at all in the resolution-tier distribution. Most of that
    # was paid back by the decorative-glyph rule below, which this change is
    # what exposed as too narrow.
    if role in ("image", "text") and not text and not node.identifier:
        return True
    # A disclosure arrow drawn at the end of a row. Matched on identity rather
    # than label, and without asking whether it is enabled: on iOS 26 these
    # arrive as enabled images whose only name is `chevron.forward` on the id,
    # so both halves of the older check missed them.
    if (_identity_text(node) or "").strip().lower() in DECORATIVE_LABELS:
        return True
    # A label already reported by the ancestor that owns it.
    return role == "text" and text is not None and text == inherited_text


def _dedupe_colocated(candidates: list[_Candidate]) -> list[_Candidate]:
    """Collapse several nodes that describe one on-screen thing into one.

    UIKit reports a table row twice: once as the Cell and again as the control
    inside it. Sometimes both carry the label, sometimes only the inner one
    does. Either way, showing both inflates the digest by roughly 40% on a list
    screen and makes every row an ambiguous target, so resolution refuses to
    act on any of them.
    """
    kept: list[_Candidate] = []
    for candidate in candidates:
        duplicate_at = _find_coincident(kept, candidate)
        if duplicate_at is None:
            kept.append(candidate)
        else:
            kept[duplicate_at] = _merge(kept[duplicate_at], candidate)
    return kept


def _merge(incumbent: _Candidate, candidate: _Candidate) -> _Candidate:
    """Fuse two nodes describing one control into the most useful single node.

    Semantics come from whichever node carries the label. Geometry comes from
    the smaller node, but only when both describe the same kind of control:
    iOS reports a switch row twice, once row-wide with the label and once as
    the toggle at the trailing edge, and tapping the row's centre hits the text
    where a switch ignores it, so set_value would report success while changing
    nothing. Between different roles the tighter rect is usually the label's,
    which is not a hit target at all, so the control keeps its own.
    """
    winner = candidate if _beats(candidate, incumbent) else incumbent
    # A bet, stated as one: that two nodes of *different* roles describing the
    # same thing are a control and its label, never a control and a tighter
    # rect for the same control. True of every UIKit pairing seen so far. It
    # would be falsified by an app that draws a control as an `image` inside a
    # `Cell` of the same extent, where the image is the hit target and this
    # would hand the tap to the cell instead.
    if incumbent.role != candidate.role:
        return winner

    tightest = min((incumbent, candidate), key=lambda c: c.node.rect.area)
    if tightest is winner or tightest.node.rect.area <= 0:
        return winner
    return _Candidate(
        node=replace(winner.node, rect=tightest.node.rect),
        role=winner.role,
        depth=winner.depth,
        repeat_count=max(winner.repeat_count, 1),
    )


def _find_coincident(kept: list[_Candidate], candidate: _Candidate) -> int | None:
    """Locate an already-kept node describing the same thing as ``candidate``.

    Two nodes are the same thing when either holds:

    * their rects mutually contain each other's centre, which catches the
      unlabelled Cell wrapping a labelled Button that iOS emits for every
      navigation row; or
    * they carry the same text and one contains the other's centre, which
      catches a row and the switch sitting at its right-hand edge.

    Mutual containment is deliberately required in the first case. Testing a
    single direction would make a table coincide with each of its own rows and
    delete the scroll container the agent needs.
    """
    text = _text_of(candidate.node)
    for index, existing in enumerate(kept):
        rect, other = candidate.node.rect, existing.node.rect
        if _mutually_centred(rect, other):
            return index
        if text and _text_of(existing.node) == text and _either_covers(rect, other):
            return index
        if _wraps_bare_control(existing, candidate) or _wraps_bare_control(candidate, existing):
            return index
    return None


def _wraps_bare_control(outer: _Candidate, inner: _Candidate) -> bool:
    """A labelled row and the unlabelled control sitting inside it.

    Requires the same role and near-total overlap, and never eats a scroll
    container: a table encloses every one of its rows, and collapsing that pair
    would delete the thing the agent scrolls.

    Overlap is proportional rather than strict containment because real iOS
    geometry does not nest cleanly. A Settings switch row reports x=36 w=330
    while its own toggle reports x=305 w=63, so the toggle overhangs the row it
    lives in by two points and any containment test fails on it.
    """
    # Same bet as `_merge`, same falsifier: it only fuses a bare control into
    # its wrapper when both report the same role.
    if outer.role != inner.role or outer.role in SCROLLABLE_ROLES:
        return False
    if _identity_text(inner.node) or not _identity_text(outer.node):
        return False
    return _overlap_ratio(inner.node.rect, outer.node.rect) >= _CONTAINMENT_RATIO


def _overlap_ratio(inner: Rect, outer: Rect) -> float:
    """How much of ``inner`` lies within ``outer``, from 0 to 1."""
    if inner.area <= 0:
        return 0.0
    width = min(inner.x + inner.width, outer.x + outer.width) - max(inner.x, outer.x)
    height = min(inner.y + inner.height, outer.y + outer.height) - max(inner.y, outer.y)
    if width <= 0 or height <= 0:
        return 0.0
    return (width * height) / inner.area


def _mutually_centred(a: Rect, b: Rect) -> bool:
    ax, ay = a.center
    bx, by = b.center
    return a.contains(bx, by) and b.contains(ax, ay)


def _either_covers(a: Rect, b: Rect) -> bool:
    ax, ay = a.center
    bx, by = b.center
    return a.contains(bx, by) or b.contains(ax, ay)


def _beats(candidate: _Candidate, incumbent: _Candidate) -> bool:
    """Which of two coincident nodes the agent should be shown.

    Text wins first: an unlabelled Cell wrapping a labelled Button says nothing
    the Button does not, and iOS emits that pairing for every Settings row.
    Otherwise the more specific role wins, since the inner control is the more
    precise thing to act on.
    """
    has_text = bool(_identity_text(candidate.node))
    incumbent_has_text = bool(_identity_text(incumbent.node))
    if has_text != incumbent_has_text:
        return has_text
    return _precedence(candidate.role) < _precedence(incumbent.role)


def _precedence(role: str) -> int:
    try:
        return ROLE_PRECEDENCE.index(role)
    except ValueError:
        return len(ROLE_PRECEDENCE)


def _collapse_repeats(candidates: list[_Candidate], settings: DigestSettings) -> list[_Candidate]:
    """Fold runs of structurally identical siblings into one representative.

    A 200-row list costs 200 lines and tells the agent nothing the first three
    rows did not, unless the rows have distinct labels, in which case they are
    the content and must be kept.
    """
    out: list[_Candidate] = []
    run: list[_Candidate] = []

    def flush() -> None:
        if not run:
            return
        if len(run) > settings.collapse_repeats_after:
            head = run[: settings.collapse_repeats_after]
            out.extend(head)
            out[-1] = _Candidate(
                node=out[-1].node,
                role=out[-1].role,
                depth=out[-1].depth,
                repeat_count=len(run) - settings.collapse_repeats_after + 1,
            )
        else:
            out.extend(run)
        run.clear()

    for c in candidates:
        if run and _same_shape(run[-1], c):
            run.append(c)
        else:
            flush()
            run.append(c)
    flush()
    return out


def _same_shape(a: _Candidate, b: _Candidate) -> bool:
    """Identical role, identical size, and neither carries distinguishing text."""
    if a.role != b.role or a.depth != b.depth:
        return False
    if _text_of(a.node) or _text_of(b.node):
        return False
    return (
        abs(a.node.rect.width - b.node.rect.width) < 2
        and abs(a.node.rect.height - b.node.rect.height) < 2
    )


def _salience(c: _Candidate, screen: Rect) -> float:
    """How much an agent would miss this element if it were dropped."""
    score = 0.0
    if _is_actionable(c):
        score += 100.0
    if c.node.label:
        score += 40.0
    elif c.node.value:
        score += 20.0
    if c.node.identifier:
        score += 10.0
    if c.role in STATEFUL_ROLES:
        score += 30.0
    if c.role in SCROLLABLE_ROLES:
        score += 15.0
    if not c.node.enabled:
        score -= 20.0
    # Prefer things nearer the top of the screen: iOS puts primary actions there
    # and content flows downward.
    if screen.height > 0:
        score += 20.0 * (1.0 - min(1.0, max(0.0, c.node.rect.y) / screen.height))
    # A very large element is usually a background, not a target.
    if screen.area > 0 and c.node.rect.area > 0.7 * screen.area:
        score -= 25.0
    return score


def _apply_budget(
    candidates: list[_Candidate], settings: DigestSettings
) -> tuple[list[_Candidate], bool]:
    """Drop the least salient elements until the digest fits its budget."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[_Candidate] = []
    used = 0
    truncated = False
    for c in ranked:
        if len(kept) >= settings.max_nodes:
            truncated = True
            break
        cost = _estimated_cost(c)
        if used + cost > settings.token_budget * _CHARS_PER_TOKEN:
            truncated = True
            break
        kept.append(c)
        used += cost
    return kept, truncated


def _estimated_cost(c: _Candidate) -> int:
    return 24 + len(_text_of(c.node) or "") + len(c.node.identifier or "")


# --- small helpers ---------------------------------------------------------


def _is_actionable(c: _Candidate) -> bool:
    """Can a tap meaningfully land here?

    `role in INTERACTIVE_ROLES` is the Apple answer and it is the last of the
    role-shaped bets. A control that was drawn rather than composed is an
    `image` or an `other`, so on a third-party screen the thing the agent most
    needs to tap is the one thing marked inert, and resolution filters to
    actionable elements first: the control sits in the digest, visible and
    unreachable.

    The widening is deliberately narrow. A node qualifies only when it is
    **unlabelled and carries an accessibility id**, because a developer who
    named something nobody can read named it so that something could find it.
    Anything with a label is content until proven otherwise, which keeps a
    progress view and a card that merely wraps a control out of it.
    """
    if not c.node.enabled:
        return False
    if c.role in INTERACTIVE_ROLES:
        return True
    if c.role not in DRAWN_CONTROL_ROLES or c.node.label or not c.node.identifier:
        return False
    # The wrapper around a control is not itself the control. Marking both
    # makes every card an ambiguous target and puts the tap at the card's
    # centre, which is the switch-row failure wearing different clothes.
    return not _contains_a_control(c.node)


def _contains_a_control(node: SnapshotNode) -> bool:
    for descendant in node.walk()[1:]:
        if not descendant.enabled:
            continue
        role = role_of(descendant.type)
        if role in INTERACTIVE_ROLES:
            return True
        if role in DRAWN_CONTROL_ROLES and descendant.identifier and not descendant.label:
            return True
    return False


def _is_selected(node: SnapshotNode) -> bool:
    return str(node.value or "").strip().lower() in ("1", "true", "selected")


def _value_of(c: _Candidate) -> str | None:
    """Keep a value where it is state, or where it is content in its own right.

    Stateful controls keep their value because it *is* their state: a switch
    reporting "0" is the whole point of reading it.

    Everything else keeps a value only when the value is prose the label does
    not already carry. iOS apps use the pair to separate a field's name from
    its contents, and a Hinge prompt arrives as label "Date prompt:" with value
    "Let's get together". Dropping the value there hands the agent the question
    and hides the answer, which is what made a real profile unreadable.
    """
    value = c.node.value
    if value is None or value == c.node.label:
        return None
    if c.role in STATEFUL_ROLES:
        return value
    return value if _is_content_value(value, c.node.label) else None


def _text_of(node: SnapshotNode) -> str | None:
    return node.label or node.value or node.placeholder


def _searchable_text(node: SnapshotNode) -> tuple[str, ...]:
    """Every string a reader of the digest could reasonably narrow by."""
    return tuple(
        text for text in (node.label, node.value, node.identifier, node.placeholder) if text
    )


#: Values that are state rather than content. A switch says "0", a progress
#: view says "45%"; neither is worth a line of the agent's context.
_STATE_VALUES = frozenset({"0", "1", "true", "false", "on", "off", "yes", "no"})


def _is_content_value(value: str | None, label: str | None) -> bool:
    """Is this value prose the agent needs, rather than a control's state?

    Only asked for roles that are not stateful, where a value is unusual
    enough to be meaningful. Three exclusions, each one measured against real
    screens: pure state tokens, a value that merely repeats its own label, and
    anything short enough to be a counter or a percentage.
    """
    if not value:
        return False
    text = value.strip()
    if len(text) < 4 or text.lower() in _STATE_VALUES:
        return False
    if text.rstrip("%").replace(".", "", 1).isdigit():
        return False
    return text.casefold() != (label or "").strip().casefold()


def _identity_text(node: SnapshotNode) -> str | None:
    """Text that names the element, excluding its value.

    A switch reports value "0" or "1", which is state rather than identity.
    Counting it as text makes an unlabelled toggle look like a named element,
    so it never merges with the labelled row it belongs to.
    """
    return node.label or node.identifier or node.placeholder


def _intersects(a: Rect, b: Rect) -> bool:
    return not (
        a.x + a.width <= b.x
        or b.x + b.width <= a.x
        or a.y + a.height <= b.y
        or b.y + b.height <= a.y
    )


#: How far down the screen a drawn header can sit and still be the title.
#:
#: Measured, not guessed, and the first value was guessed and wrong. A
#: synthetic fixture put its header at 7% and 0.15 looked generous; a real
#: third-party Discover screen puts a filter row above the title, so the
#: header sits at **197 of 956 points, 21%**, and the fallback silently did
#: nothing on the one screen it existed for. The next text down is at 24%,
#: which is uncomfortably close, but the topmost line wins the tie and the
#: alternative is a title-less fingerprint.
#:
#: A second real screen then set the upper limit from the other side. Settings
#: search results carry `No Results for "Airplane"` at 212 of 852, **24.9%**,
#: with no navigation bar above it. So the usable window is 21% to 24.9% and
#: the band sits at the top of it, on two data points with a 4-point margin.
#: That margin is the fragility here, and `test_a_drawn_header_stays_in_the_
#: elements_it_titles` pins the tight end of it.
#:
#: Note what promoting that string makes the title: body text, not chrome. That
#: is accepted rather than overlooked. It reads the way a person would name the
#: screen, and it makes the fingerprint tell "no results for Airplane" apart
#: from "no results for Bluetooth", which are genuinely different screens.
#:
#: Two screens is not enough to derive a better rule, and inventing one against
#: fixtures is exactly what produced the wrong 15%. This is the number to
#: revisit first if a title ever comes back wrong, and the thing to replace
#: with a real signal once there are more real screens to derive one from.
_HEADER_BAND = 0.25


def _screen_title(root: SnapshotNode) -> tuple[str | None, bool]:
    """How a person would name this screen.

    The navigation bar first, because where there is one it is authoritative.
    Where there is not, the topmost line of text stands in for it.

    The fallback is not cosmetic. The title is part of the fingerprint, which
    is what tells a real navigation apart from an action that did nothing, and
    an app that draws its own header instead of using a `UINavigationBar`
    leaves two structurally similar screens hashing to the same value. That is
    the split-view failure already recorded in CLAUDE.md, reached by a
    different route.

    Returns the title and whether it came from chrome. The caller needs the
    second half: a navigation bar's title is *also* reported as a StaticText
    inside it, so suppressing that duplicate loses nothing, while a drawn
    header is the only copy there is and suppressing it deletes content.
    """
    for node in root.walk():
        if role_of(node.type) == "nav":
            if node.label:
                return node.label, True
            for child in node.walk()[1:]:
                if role_of(child.type) == "text" and child.label:
                    return child.label, True
    return _drawn_header(root), False


def _drawn_header(root: SnapshotNode) -> str | None:
    """The topmost visible line of text in the band a header would occupy."""
    cutoff = root.rect.y + root.rect.height * _HEADER_BAND
    headers = [
        node
        for node in root.walk()
        if role_of(node.type) == "text" and node.label and node.visible and node.rect.y < cutoff
    ]
    if not headers:
        return None
    return min(headers, key=lambda n: (n.rect.y, n.rect.x)).label


def _truncate(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
