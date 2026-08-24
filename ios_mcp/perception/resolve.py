"""Element resolution with a deterministic fallback chain.

The agent supplies either a ref from the last digest or a plain-language
description. Everything else happens server-side, which matters because a
failed resolution retried here costs zero model tokens, while a failed
resolution bounced back to the model costs a whole turn.

Tiers, in order:

1. ``ref`` in the current digest                (exact)
2. stale ``ref``, re-found by accessibility id  (id)
3. stale ``ref``, re-found by label and role    (label+role)
4. stale ``ref``, nearest match by position     (proximity)
5. ``target`` text, exact label match           (text-exact)
6. ``target`` text, substring then fuzzy match  (text-partial / text-fuzzy)

Tier 6 failing raises with the closest candidates listed, which is the agent's
cue to fall back to an annotated screenshot.
"""

from __future__ import annotations

import difflib

from ios_mcp.errors import ElementAmbiguous, ElementNotFound, InvalidArgument, NoSnapshot
from ios_mcp.perception.digest import Digest, DigestNode
from ios_mcp.perception.refs import RefTable, Target
from ios_mcp.perception.roles import INTERACTIVE_ROLES

#: Below this ratio a fuzzy match is more likely wrong than right.
_FUZZY_THRESHOLD = 0.72


def resolve(
    digest: Digest,
    refs: RefTable,
    *,
    ref: str | None = None,
    target: str | None = None,
    role: str | None = None,
    prefer_roles: frozenset[str] | None = None,
    actionable_only: bool = True,
) -> Target:
    """Find one element. Raises ``ElementNotFound`` with candidates when it cannot.

    ``prefer_roles`` breaks ties toward the kind of element the caller can
    actually use. A settings row and its switch share a label, so "Airplane
    Mode" is ambiguous in general but unambiguous for a caller that can only
    act on a switch.
    """
    if not ref and not target:
        raise InvalidArgument(
            "Provide either a ref from the last observation or a target description",
            hint="Call ios_observe first, then pass one of its refs such as 'e7'.",
        )
    if not digest.nodes:
        raise NoSnapshot(
            "The screen has no readable elements",
            hint="Call ios_screenshot to see what is actually displayed.",
        )

    if ref:
        return _resolve_ref(digest, refs, ref, role=role)
    assert target is not None
    return _resolve_text(
        digest, target, role=role, prefer_roles=prefer_roles, actionable_only=actionable_only
    )


# --- ref path --------------------------------------------------------------


def _resolve_ref(digest: Digest, refs: RefTable, ref: str, *, role: str | None) -> Target:
    """Resolve a ref, verifying it still denotes the element the agent was shown.

    Refs are positional, so inserting a row shifts every ref below it. Trusting
    position alone would silently act on the wrong control, which is the worst
    failure this system can have. ``RefTable.current`` records what the agent
    was last shown, so a fresh digest is checked against that memory before the
    fast path is taken.
    """
    remembered = refs.get(ref)
    node = digest.by_ref(ref)

    if node is not None and (remembered is None or _same_element(node, remembered)):
        return _target(node, "exact")

    stale = remembered or refs.get_stale(ref)
    if stale is None:
        raise ElementNotFound(
            f"Unknown ref {ref!r}",
            hint=(
                "Refs come from the most recent ios_observe and are not stable across "
                "observations. Observe again and use a fresh ref."
            ),
            details={"known_refs": [n.ref for n in digest.nodes][:20]},
        )

    # The screen moved. Re-find the same element by its own attributes.
    if stale.identifier:
        for node in digest.nodes:
            if node.identifier == stale.identifier:
                return _target(node, "id")

    if stale.label:
        matches = [n for n in digest.nodes if n.label == stale.label and n.role == stale.role]
        if len(matches) == 1:
            return _target(matches[0], "label+role")
        if matches:
            nearest = min(matches, key=lambda n: n.rect.distance_to(stale.rect))
            return _target(nearest, "proximity")

    same_role = [n for n in digest.nodes if n.role == stale.role]
    if same_role:
        nearest = min(same_role, key=lambda n: n.rect.distance_to(stale.rect))
        if nearest.rect.distance_to(stale.rect) < 60:
            return _target(nearest, "proximity")

    raise ElementNotFound(
        f"Ref {ref!r} pointed at {stale.role} "
        f'"{stale.label or stale.identifier or "unlabelled"}", which is no longer on screen',
        hint="The screen changed. Observe again and use a fresh ref.",
    )


# --- text path -------------------------------------------------------------


def _resolve_text(
    digest: Digest,
    target: str,
    *,
    role: str | None,
    actionable_only: bool,
    prefer_roles: frozenset[str] | None = None,
) -> Target:
    pool = digest.nodes
    if role:
        pool = [n for n in pool if n.role == role]
    if actionable_only:
        actionable = [n for n in pool if n.actionable]
        # Fall back to everything when nothing actionable matches: reading a
        # StaticText is a legitimate target too.
        pool = actionable or pool
    if not pool:
        raise ElementNotFound(
            f"No {role or 'element'} on screen to match {target!r}",
            details={"visible": _summary(digest.nodes)},
        )

    needle = _normalise(target)

    exact = _prefer([n for n in pool if _normalise(n.label) == needle], prefer_roles)
    if len(exact) == 1:
        return _target(exact[0], "text-exact")
    if len(exact) > 1:
        raise ElementAmbiguous(
            f"{len(exact)} elements are labelled {target!r}",
            hint="Pass a ref from ios_observe instead, or narrow with role=.",
            details={"candidates": [n.ref for n in exact]},
        )

    by_id = [n for n in pool if _normalise(n.identifier) == needle]
    if len(by_id) == 1:
        return _target(by_id[0], "id-exact")

    partial = _prefer([n for n in pool if needle and needle in _normalise(n.label)], prefer_roles)
    if len(partial) == 1:
        return _target(partial[0], "text-partial")
    if len(partial) > 1:
        # Prefer the shortest label: "Wi-Fi" over "Wi-Fi Networks Nearby".
        best = min(partial, key=lambda n: len(n.label or ""))
        return _target(
            best,
            "text-partial",
            alternatives=tuple(n.ref for n in partial if n is not best)[:5],
        )

    scored = [
        (difflib.SequenceMatcher(None, needle, _normalise(n.label)).ratio(), n)
        for n in pool
        if n.label
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored and scored[0][0] >= _FUZZY_THRESHOLD:
        return _target(scored[0][1], "text-fuzzy")

    raise ElementNotFound(
        f"Nothing on screen matches {target!r}",
        hint=(
            "Check the closest candidates below. If the element is drawn without "
            "accessibility data, call ios_screenshot with annotate_refs=true."
        ),
        details={
            "closest": [f"{n.ref}: {n.role} {n.label!r}" for _, n in scored[:5]],
            "visible": _summary(digest.nodes),
        },
    )


# --- helpers ---------------------------------------------------------------


def _target(node: DigestNode, via: str, alternatives: tuple[str, ...] = ()) -> Target:
    return Target(
        ref=node.ref,
        role=node.role,
        label=node.label,
        identifier=node.identifier,
        rect=node.rect,
        enabled=node.enabled,
        resolved_via=via,
        alternatives=alternatives,
    )


def _prefer(matches: list[DigestNode], prefer_roles: frozenset[str] | None) -> list[DigestNode]:
    """Break a tie toward the roles the caller can act on.

    Only ever narrows an existing set of matches. Filtering the pool before
    matching would make an element that exists but is unusable look absent,
    which sends the agent hunting for something it can already see.
    """
    if not prefer_roles or len(matches) < 2:
        return matches
    preferred = [n for n in matches if n.role in prefer_roles]
    return preferred or matches


def _same_element(a: DigestNode, b: DigestNode) -> bool:
    """Whether two digest nodes denote the same on-screen element.

    Identity is the accessibility identifier when present, since that is what
    app authors keep stable. Otherwise role plus label, which is weaker but is
    all iOS gives us for most controls.
    """
    if a.identifier and b.identifier:
        return a.identifier == b.identifier
    return a.role == b.role and a.label == b.label


def _normalise(text: str | None) -> str:
    return " ".join((text or "").split()).strip().lower()


def _summary(nodes: list[DigestNode], limit: int = 12) -> list[str]:
    interesting = [n for n in nodes if n.role in INTERACTIVE_ROLES] or nodes
    return [f"{n.ref}: {n.role} {n.label!r}" for n in interesting[:limit]]
