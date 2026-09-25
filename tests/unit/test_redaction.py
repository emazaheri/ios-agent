"""Redaction reaches every consumer, not only the one that asked for it.

It used to be the MCP server's job, applied at its own boundary to copies of
what `IosSession` returned. The bundled agent and the terminal front end call
`IosSession` directly, so a card number on screen reached the model provider
verbatim from the agent this project ships, the audit trail stored it, and
`ios-agent run` printed it. Found by writing the threat model down and checking
each claim in it against the code, not by any test: every one was passing.

So each exit is asserted here, on the direct path, against a screen carrying a
card number the way cards are actually printed.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fake_device import make_session
from ios_agent.backend import SessionBackend
from trees import node

from ios_mcp.config import Settings
from ios_mcp.policy.redact import Redactor

CARD = "4111 1111 1111 1111"
EMAIL = "someone@example.com"


def _wallet() -> dict[str, Any]:
    return node(
        "Application",
        label="Wallet",
        name="Wallet",
        h=852,
        children=[
            node(
                "Window",
                h=852,
                children=[
                    node("StaticText", label=f"Card {CARD}", x=16, y=120, w=360, h=30),
                    node("StaticText", label=f"Contact {EMAIL}", x=16, y=160, w=360, h=30),
                    node("Button", label=f"Email {EMAIL}", name="email", x=16, y=220, w=300),
                    node("TextField", label="Note", name="note", x=16, y=280, w=300),
                ],
            )
        ],
    )


def _leaks(text: object) -> list[str]:
    rendered = str(text)
    return [s for s in (CARD, EMAIL) if s in rendered]


# -- every exit on the direct path -------------------------------------------


async def test_what_the_agent_observes_is_redacted() -> None:
    session, _, _ = make_session(_wallet())

    assert _leaks(await SessionBackend(session).observe()) == []


async def test_an_action_result_is_redacted_including_its_target() -> None:
    """The target is screen text of its own, so the payload is scrubbed whole."""
    session, _, _ = make_session(_wallet())
    await session.observe()

    result = await session.tap(target="email")

    assert _leaks(result.to_dict()) == []


async def test_a_find_is_redacted() -> None:
    """A find reads the raw tree, where a trimmed value would sit in full."""
    session, _, _ = make_session(_wallet())

    found = await session.find("Card")

    assert found.total >= 1
    assert _leaks(found.render()) == []
    assert _leaks(found.to_dict()) == []


async def test_read_text_is_redacted() -> None:
    session, _, _ = make_session(_wallet())

    assert _leaks(await session.read_text()) == []


async def test_the_audit_trail_stores_nothing_it_should_not() -> None:
    """Its docstring always said so; targets used to be stored raw."""
    session, _, _ = make_session(_wallet())
    await session.observe()

    await session.tap(target="email")
    # A word the policy gate does not know, so this records rather than asking.
    # "pay" asks first, and correctly: the approval shows the human the raw
    # text, which is what they are consenting to.
    await session.type_text(f"ref {CARD}", target="Note")

    assert _leaks(session.audit.to_dict()) == []


# -- what must stay raw -------------------------------------------------------


async def test_the_nodes_themselves_stay_raw() -> None:
    """Refs, fingerprints and resolution run on the nodes, not on what is said.

    Redacting the nodes would make resolution compare a redacted label against
    a raw tree and read every redacted element as a different one, which is the
    wrong-control failure `CLAUDE.md` calls the worst this system has.
    """
    session, _, _ = make_session(_wallet())

    digest = await session.observe()

    assert any(n.label and CARD in n.label for n in digest.nodes)
    assert _leaks(digest.render()) == []


async def test_an_element_whose_label_was_redacted_still_resolves() -> None:
    session, _, _ = make_session(_wallet())
    digest = await session.observe()
    ref = next(n.ref for n in digest.nodes if n.label and EMAIL in n.label)

    result = await session.tap(ref=ref)

    assert result.ok is True


def test_redacting_twice_changes_nothing() -> None:
    """The server still redacts at its boundary, over output already redacted."""
    redactor = Redactor(Settings().policy)
    once = redactor.text(f"Card {CARD} {EMAIL}")

    assert redactor.text(once) == once


# -- the card pattern ---------------------------------------------------------


@pytest.mark.parametrize(
    "shown",
    [
        "4111111111111111",
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "Card 4111 1111 1111 1111",
        "3782 822463 10005",
    ],
)
def test_a_card_is_redacted_however_it_is_grouped(shown: str) -> None:
    """The old pattern matched contiguous digits only, which is not how cards
    are shown. "4111 1111 1111 1111" passed straight through."""
    redacted = Redactor(Settings().policy).text(shown) or ""

    assert "[redacted]" in redacted
    assert not re.search(r"\d", redacted), f"digits survived: {redacted!r}"


@pytest.mark.parametrize(
    "setting",
    [
        "iOS 26.6.1",
        "Version 26.5 (23F79)",
        "+1 (555) 123-4567",
        "2026-09-24",
        "Capacity 511.28 GB",
        "Available 256 GB of 512 GB",
        "IP 192.168.1.20",
        "•••• 1111",
    ],
)
def test_what_a_settings_screen_shows_is_left_alone(setting: str) -> None:
    """Over-redaction is a failure too: it hides what the agent needs to read."""
    assert Redactor(Settings().policy).text(setting) == setting
