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

from ios_mcp.config import PolicySettings
from ios_mcp.errors import AppNotAllowed, SessionHalted
from ios_mcp.perception.refs import Target

logger = logging.getLogger(__name__)


class Risk(StrEnum):
    SAFE = "safe"
    DESTRUCTIVE = "destructive"


@dataclass(slots=True, frozen=True)
class Verdict:
    risk: Risk
    reason: str | None = None
    matched: str | None = None

    @property
    def needs_approval(self) -> bool:
        return self.risk is Risk.DESTRUCTIVE

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
        if not self.settings.enabled or not self.settings.confirm_destructive:
            return Verdict(Risk.SAFE)
        if action.startswith(("observe", "screenshot", "read_text", "wait_for", "list")):
            return Verdict(Risk.SAFE)

        haystacks = [
            target.label if target else None,
            target.identifier if target else None,
            text,
        ]
        for haystack in haystacks:
            match = self._match_destructive(haystack)
            if match:
                where = f'"{haystack}"'
                return Verdict(
                    Risk.DESTRUCTIVE,
                    reason=f"{action} on {where} matches the destructive rule {match!r}",
                    matched=match,
                )
        return Verdict(Risk.SAFE)

    def _match_destructive(self, text: str | None) -> str | None:
        if not text:
            return None
        lowered = text.lower()
        for word in self.settings.destructive_labels:
            if re.search(rf"(?<![a-z]){re.escape(word.lower())}(?![a-z])", lowered):
                return word
        return None

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
