"""Read a text field back after typing, and say whether the text landed.

Typing is where UI automation most often reports success while nothing
happened. Keys go to whatever holds focus, and a field that never took focus,
lost the first characters to a keyboard still animating in, or was quietly
rewritten by the app all look identical to a caller told only "ok". The other
iOS agent tools track this as their second commonest failure: eleven
characters requested and seven landed, the full string reported as typed.

Only the field's value is compared, never the screen. Three outcomes:

- ``verified``: the text is in the field as sent.
- ``reformatted``: it is there once case, typographic quotes, spacing and
  punctuation are set aside. Autocapitalisation, smart punctuation and input
  masks such as a phone number's dashes all land here, and none of them is a
  failure.
- ``mismatch``: it is not there. The action is reported as failed, with what
  the field does show.

A secure field reads back as bullets, so only the count of characters is
compared, and neither the text nor what the field shows is ever reported.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ReadbackStatus = Literal["verified", "reformatted", "mismatch"]

_TYPOGRAPHIC = str.maketrans(
    {
        0x2018: "'",  # left single quote
        0x2019: "'",  # right single quote
        0x201C: '"',  # left double quote
        0x201D: '"',  # right double quote
        0x2013: "-",  # en dash
        0x2014: "-",  # em dash
        0x00A0: " ",  # no-break space
        0x2026: "...",  # ellipsis
    }
)
#: What a secure text field shows for each character.
_BULLETS = frozenset((chr(0x2022), chr(0x25CF)))


@dataclass(slots=True, frozen=True)
class Readback:
    status: ReadbackStatus
    #: Characters requested and characters the field holds.
    expected_chars: int
    shown_chars: int
    #: What the field shows. None for a secret, which is never echoed.
    shown: str | None = None

    @property
    def landed(self) -> bool:
        return self.status != "mismatch"

    @property
    def note(self) -> str | None:
        if self.status == "verified":
            return None
        if self.shown is None:
            if self.status == "mismatch":
                return (
                    f"typed {self.expected_chars} characters but the field holds {self.shown_chars}"
                )
            return None
        if self.status == "reformatted":
            return f"the field reformatted the text: it shows {self.shown!r}"
        return f"the text did not land as typed: the field shows {self.shown!r}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.shown is not None:
            out["shown"] = self.shown
        else:
            out["expected_chars"] = self.expected_chars
            out["shown_chars"] = self.shown_chars
        return out


def field_text(value: str | None, placeholder: str | None) -> str:
    """What a field holds. XCTest reports an empty field's value as its placeholder."""
    if value is None or (placeholder and value == placeholder):
        return ""
    return value


def compare(typed: str, before: str, after: str, *, secret: bool = False) -> Readback:
    """Judge whether ``typed`` landed, given the field's text before and after.

    ``before`` matters because typing appends: a field holding "Wi" that was
    sent "-Fi" should read "Wi-Fi". It is not required, though. A field may
    have cleared itself on focus, as a secure field does on iOS, or the caret
    may have sat mid-text, so the typed text appearing anywhere counts.
    """
    # An unchanged field received nothing, whatever it happens to contain.
    unchanged = bool(typed) and after == before
    if secret or (after and set(after) <= _BULLETS):
        landed = not unchanged and (
            len(after) - len(before) == len(typed) or len(after) == len(typed)
        )
        return Readback(
            "verified" if landed else "mismatch",
            expected_chars=len(typed),
            shown_chars=len(after),
        )

    def readback(status: ReadbackStatus) -> Readback:
        return Readback(status, expected_chars=len(typed), shown_chars=len(after), shown=after)

    if unchanged:
        return readback("mismatch")
    if _arrived(typed, before, after, _exact):
        return readback("verified")
    if _arrived(typed, before, after, _loose) or _arrived(typed, before, after, _skeleton):
        return readback("reformatted")
    return readback("mismatch")


def _arrived(typed: str, before: str, after: str, form: Callable[[str], str]) -> bool:
    """The text appears in the field more often than it did before typing.

    A count rather than a containment test, so that a field which already held
    the text and received nothing is not taken for one that took it.
    """
    needle = form(typed)
    if not needle:
        return False
    return form(after).count(needle) > form(before).count(needle) or form(after) == needle


def _exact(text: str) -> str:
    return text


def _loose(text: str) -> str:
    return " ".join(text.translate(_TYPOGRAPHIC).casefold().split())


def _skeleton(text: str) -> str:
    """Letters and digits only: what survives an input mask."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())
