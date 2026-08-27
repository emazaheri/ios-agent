"""The plain consumer: events to stdout, one line at a time.

This is not a fallback that exists to be worse than the real thing. It is what
you want in a pipe, in CI, in a scrollback you intend to paste somewhere, and
on any terminal where a full-screen app is the wrong shape. It also proves the
seam is a seam: two front ends over one set of events, differing only in how
they draw.

The four closing blocks are the ones `scripts/ask.py` printed, kept because
they were the right four: what it did, what it cost, what it says, and the
screen it ended on. What is new is that the first three arrive while the run is
happening rather than after it.
"""

from __future__ import annotations

import sys
from typing import TextIO

from ios_tui.events import (
    ActionFinished,
    ApprovalAnswered,
    ApprovalAsked,
    DeviceReady,
    Event,
    Failed,
    GoalFinished,
    GoalStarted,
    ModelTurn,
    Observed,
    Progress,
    Stopping,
)


class Printer:
    """Renders events as they arrive."""

    def __init__(self, stream: TextIO | None = None, *, verbose: bool = False) -> None:
        self.stream = stream or sys.stdout
        #: Progress lines are the device coming up. Interesting while you wait,
        #: noise in a transcript, so they are opt-in.
        self.verbose = verbose

    def emit(self, event: Event) -> None:
        match event:
            case Progress(text=text) if self.verbose:
                self._line(f"  ... {text}")
            case DeviceReady(lease=lease):
                device = dict(lease).get("device", {})
                name = device.get("name", "?") if isinstance(device, dict) else "?"
                kind = device.get("kind", "?") if isinstance(device, dict) else "?"
                version = device.get("os_version", "?") if isinstance(device, dict) else "?"
                self._line(f"  device : {name} ({kind}, iOS {version})")
            case GoalStarted(goal=goal, model=model):
                self._line(f"  model  : {model}")
                self._line(f"  goal   : {goal}\n")
            case ModelTurn(text=text) if text.strip():
                self._line(f"  {text.strip()}")
            case Observed(stats=stats):
                self._line(f"    observe      -> {stats.device_tokens} device tokens so far")
            case ActionFinished(verb=verb, args=args, elapsed_ms=ms, refused=refused):
                target = _target_of(args)
                note = "  refused (never reached the device)" if refused else ""
                self._line(f"    {verb:<12} {target[:44]:<44} {ms:>5}ms{note}")
            case ApprovalAsked(request=request):
                self._line(f"\n  ? {request.get('action')} on {request.get('signature')}")
                self._line(f"    {request.get('reason')}")
            case ApprovalAnswered(allowed=allowed):
                self._line(f"    -> {'allowed' if allowed else 'refused'}\n")
            case Stopping(reason=reason):
                self._line(f"\n  stopping: {reason}")
            case Failed(where=where, message=message):
                self._line(f"\n  failed during {where}: {message}")
            case GoalFinished():
                self._finished(event)
            case _:
                return

    def _finished(self, outcome: GoalFinished) -> None:
        stats = outcome.stats
        self._line("\n  what it cost")
        self._line(
            f"    {stats.actions} actions, {stats.observations} observation(s), "
            f"{stats.device_tokens} device tokens, {outcome.elapsed_s:.1f}s"
        )
        self._line(f"    model: {outcome.prompt_tokens} in / {outcome.completion_tokens} out")
        if stats.refusals:
            self._line(f"    {stats.refusals} call(s) refused before reaching the device")
        if outcome.approvals_asked:
            self._line(f"    stopped to ask you {outcome.approvals_asked} time(s)")

        self._line("\n  what it says")
        self._line(f"    succeeded: {outcome.succeeded}")
        self._line(f"    {outcome.summary or '(no summary)'}")
        if outcome.stopped_because:
            self._line(f"    stopped because: {outcome.stopped_because}")

    def _line(self, text: str) -> None:
        print(text, file=self.stream, flush=True)


def _target_of(args: object) -> str:
    """The one argument worth putting in a column."""
    if not isinstance(args, dict):
        return ""
    for key in ("target", "url", "name", "direction"):
        value = args.get(key)
        if value:
            return str(value)
    return ""
