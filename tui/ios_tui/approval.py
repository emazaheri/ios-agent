"""Asking a human, properly.

The old script asked with `input()` on stdin, inside the event loop, while a
WebDriverAgent session sat open waiting. That is the worst possible surface for
the one thing SAFETY.md is built around.

The gate classifies *before* acting, so when this screen appears nothing has
reached the device yet. That is what makes the pause worth anything, and it is
why the modal can take as long as it likes.

Default no, and every way out that is not an explicit yes is a no: escape, the
No button, and dismissing the screen. An unanswerable question is not consent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ApprovalModal(ModalScreen[bool]):
    """One question, one action, one answer.

    Scoped to a single action deliberately: approving Send never approves
    Delete. The gate enforces that by signature; this only carries the answer.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "allow", "Allow", show=True),
        Binding("n,escape", "refuse", "Refuse", show=True),
    ]

    def __init__(self, request: Mapping[str, Any]) -> None:
        super().__init__()
        self.request = dict(request)

    def compose(self) -> ComposeResult:
        action = str(self.request.get("action", "an action"))
        reason = str(self.request.get("reason") or "it matched a destructive rule")
        signature = str(self.request.get("signature") or "")
        goal = str(self.request.get("goal") or "")

        with Grid(id="approval"):
            yield Label(f"Allow {action}?", id="approval-title")
            yield Static(reason, id="approval-reason")
            yield Static(f"on: {signature}", id="approval-signature")
            yield Static(f"goal: {goal}", id="approval-goal")
            yield Button("Refuse  (n)", variant="primary", id="refuse")
            yield Button("Allow  (y)", variant="error", id="allow")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "allow")

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_refuse(self) -> None:
        self.dismiss(False)
