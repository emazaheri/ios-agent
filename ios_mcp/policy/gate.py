"""Safety gate for actions on a real person's device.

Automating someone's phone with their real accounts is materially riskier than
test automation: the same tap that dismisses a dialog in CI can send a message,
make a payment, or delete a photo library here. This gate is on by default and
sits in front of every action.

It classifies a resolved target *before* acting, so approval is requested while
the operation is still preventable rather than reported afterwards.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ios_mcp.actions.catalog import READ_ONLY_ACTIONS
from ios_mcp.config import PolicySettings
from ios_mcp.errors import AppNotAllowed, SessionHalted
from ios_mcp.perception.refs import Target

logger = logging.getLogger(__name__)


class Risk(StrEnum):
    SAFE = "safe"
    #: Destroys data or costs money.
    DESTRUCTIVE = "destructive"
    #: Reaches another person: a like, a follow, a reply. Nothing is lost on
    #: the device, and a stranger is notified in the owner's name, which is not
    #: undone by undoing the tap.
    REACHES_A_PERSON = "reaches_a_person"


@dataclass(slots=True, frozen=True)
class Verdict:
    risk: Risk
    reason: str | None = None
    matched: str | None = None

    @property
    def needs_approval(self) -> bool:
        return self.risk is not Risk.SAFE

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"risk": self.risk.value}
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class PolicyGate:
    """Per-session policy state and decisions."""

    settings: PolicySettings
    #: Approvals already granted, keyed by the action signature they covered.
    approved: set[str] = field(default_factory=set)
    halted_reason: str | None = None
    consecutive_failures: int = 0

    # -- app scope ---------------------------------------------------------

    def check_app(self, bundle_id: str | None) -> None:
        """Refuse to drive an app outside the session's scope."""
        if not self.settings.enabled or not bundle_id:
            return
        if self.settings.app_allowlist and bundle_id not in self.settings.app_allowlist:
            raise AppNotAllowed(
                f"{bundle_id} is not in this session's allowlist",
                hint=(
                    "Allowed: "
                    + ", ".join(self.settings.app_allowlist)
                    + ". Open a new session with a wider scope if this is intended."
                ),
            )
        if bundle_id in self.settings.app_blocklist:
            raise AppNotAllowed(
                f"{bundle_id} is blocked for automation",
                hint=(
                    "Apps holding payment or credential data are blocked by default. "
                    "Change policy.app_blocklist to override."
                ),
            )

    # -- action risk -------------------------------------------------------

    def classify(self, action: str, target: Target | None, *, text: str | None = None) -> Verdict:
        """Judge an action about to be performed.

        Matching is on whole words so that "Sender" and "Undelete" do not trip
        the "send" and "delete" rules, which would train an operator to approve
        everything reflexively.
        """
        if not self.settings.enabled:
            return Verdict(Risk.SAFE)
        # Read verbs are safe by definition, and which verbs those are is a
        # fact about the action rather than about this file: it comes from the
        # catalog, so a new read verb cannot be safe here and gated there.
        # `list` stays a prefix because the listing tools live in the server
        # layer, which this one must not import.
        if action in READ_ONLY_ACTIONS or action.startswith("list"):
            return Verdict(Risk.SAFE)

        haystacks = [
            target.label if target else None,
            target.identifier if target else None,
            text,
        ]
        # Destructive first: an action that both costs money and messages
        # someone should be asked about as the costlier of the two.
        #
        # The person rule reads less than the destructive one. It judges the
        # control being pressed, not prose and not typing: typing into a field
        # labelled "Comment" reaches nobody until it is sent, and "send" is
        # already destructive, while a paragraph of static text is nobody's
        # button. Both are where "like" turns up as a
        # preposition. Measured on a real Settings app, ten panes and 178
        # labels, the only hit was a StandBy description reading "information
        # like widgets".
        judged = target is not None and target.role != _PROSE and not action.startswith("type")
        person_haystacks = [target.label, target.identifier] if judged and target else []
        rules: list[tuple[Risk, tuple[str, ...], list[str | None]]] = []
        if self.settings.confirm_destructive:
            rules.append((Risk.DESTRUCTIVE, self.settings.destructive_labels, haystacks))
        if self.settings.confirm_reaching_a_person:
            rules.append((Risk.REACHES_A_PERSON, self.settings.person_labels, person_haystacks))
        for risk, words, where in rules:
            for haystack in where:
                match = _whole_word(haystack, words)
                if haystack and match:
                    reason = _reason(risk, action, haystack, match)
                    return Verdict(risk, reason=reason, matched=match)
        return Verdict(Risk.SAFE)

    # -- approval ----------------------------------------------------------

    def signature(self, action: str, target: Target | None) -> str:
        name = (target.identifier or target.label or target.ref) if target else "-"
        return f"{action}:{name}"

    def is_approved(self, signature: str) -> bool:
        return signature in self.approved

    def approve(self, signature: str) -> None:
        """Record consent for one specific action, not for a class of them."""
        self.approved.add(signature)

    def revoke_all(self) -> None:
        self.approved.clear()

    # -- kill switch -------------------------------------------------------

    def check_running(self) -> None:
        if self.halted_reason is not None:
            raise SessionHalted(
                f"This session is halted: {self.halted_reason}",
                hint="Open a new session, or call ios_resume to continue deliberately.",
            )

    def halt(self, reason: str) -> None:
        logger.warning("Halting session: %s", reason)
        self.halted_reason = reason

    def resume(self) -> None:
        self.halted_reason = None
        self.consecutive_failures = 0

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        """Stop after repeated failures rather than flailing at the screen."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.settings.max_consecutive_failures:
            self.halt(
                f"{self.consecutive_failures} actions failed in a row; the agent is probably stuck"
            )

    def record_loop(self) -> None:
        self.halt(
            "the screen has cycled between the same few states; the agent appears to be looping"
        )


#: The role a digest gives static text. Prose, not a control.
_PROSE = "text"


def _whole_word(text: str | None, words: tuple[str, ...]) -> str | None:
    """The first of `words` that appears in `text` as a whole word.

    Whole words so that "Sender" does not trip "send" and "Likes", the name of
    a tab, does not trip "like". A rule that fires on those trains the person
    approving to stop reading, which leaves them worse off than no rule.
    """
    if not text:
        return None
    lowered = text.lower()
    for word in words:
        if re.search(rf"(?<![a-z]){re.escape(word.lower())}(?![a-z])", lowered):
            return word
    return None


def _reason(risk: Risk, action: str, haystack: str, match: str) -> str:
    """What the person being asked needs to know, in terms of consequence."""
    where = f'"{haystack}"'
    if risk is Risk.REACHES_A_PERSON:
        return f"{action} on {where} would reach another person (matched {match!r})"
    return f"{action} on {where} matches the destructive rule {match!r}"
