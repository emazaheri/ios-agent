"""What the agent carries from one task to the next.

The measured situation this was designed against, rather than the one the plan
assumed. After S2 the agent sits at the oracle's action floor on eight of ten
tasks, so "remember the route" has nothing to recover: a route it already walks
optimally cannot be walked more optimally. The entire remaining gap across the
whole set is **five actions, and all five are one task** — the dead-switch
injection, where the device accepts a tap, reports success, never moves, and
the agent spends about six actions discovering that.

That discovery is the thing worth keeping. Learning a layout saves nothing here;
learning that *a control is a lie* saves the whole investigation, every time
that control is met again.

So this remembers failures, not routes. It is the narrower claim, and it is the
one the numbers support.

## Two rules that keep a hint from becoming a liability

**A note is a hint, never a substitute for the screen.** It is surfaced as
something previously observed, and the agent is told to trust the screen over
it. Remembered state that outranks live perception is worse than no memory,
because it is confidently wrong.

**Anything contradicted is forgotten immediately.** If a control noted as
unresponsive does move, the note is deleted on the spot rather than aged out.
Apps get updated and devices get fixed, and a memory that argues with the
device it is looking at has stopped being evidence.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Where notes live when no path is given. `Settings.artifacts_dir` has been
#: declared since the first commit with nothing ever writing to it; this is its
#: first real use.
DEFAULT_FILENAME = "agent-memory.json"


@dataclass(frozen=True, slots=True)
class Note:
    """One thing learned the hard way."""

    app: str
    #: The control's visible label, lowercased. Keyed on this rather than on a
    #: screen fingerprint because a fingerprint hashes switch values, so
    #: toggling anything on the screen would make the note unrecallable, which
    #: is exactly backwards for a note about a switch.
    target: str
    detail: str
    #: The fingerprint of the screen it was learned on. Provenance, not a key:
    #: useful when explaining where a note came from, useless for finding it.
    fingerprint: str | None = None
    attempts: int = 1
    at: float = field(default_factory=time.time)

    @property
    def key(self) -> tuple[str, str]:
        return (self.app, self.target)

    def render(self) -> str:
        return f"- {self.detail}"


@dataclass
class Memory:
    """Notes about controls that did not do what they claimed.

    Deliberately not a general key-value store for the model to write into.
    Everything here is derived from something the device did, so a note can be
    contradicted by the device and removed. A free-form scratchpad cannot be
    checked against anything, and an unfalsifiable memory is where confident
    wrongness comes from.
    """

    path: Path | None = None
    notes: dict[tuple[str, str], Note] = field(default_factory=dict)
    #: How firmly a note is put to the agent. Present because the hedged
    #: framing measured *worse* than no memory at all, and the obvious
    #: explanation is that hedging invites re-investigation. Kept as a flag so
    #: the two can be compared rather than argued about.
    assertive: bool = False

    # -- learning ----------------------------------------------------------

    def note_unresponsive(
        self, app: str, target: str, *, attempts: int, fingerprint: str | None = None
    ) -> Note:
        """Record that a control accepted input and did nothing."""
        note = Note(
            app=app,
            target=target.strip().lower(),
            detail=(
                f'"{target}" accepted {attempts} attempt(s) and never changed. '
                f"It may not work on this device."
            ),
            fingerprint=fingerprint,
            attempts=attempts,
        )
        self.notes[note.key] = note
        return note

    def forget(self, app: str, target: str) -> bool:
        """Drop a note the device has just contradicted.

        Called when a control that was noted as unresponsive does move. Removing
        it immediately rather than ageing it out is the point: a note that
        argues with the screen in front of it is not evidence any more.
        """
        return self.notes.pop((app, target.strip().lower()), None) is not None

    # -- recall ------------------------------------------------------------

    def about(self, app: str | None) -> list[Note]:
        """Notes for one app, or every note when the app is not known yet.

        Not filtering is the honest behaviour for `None` rather than a
        convenience. Notes are written under the app the *digest* reported,
        which is only known once something has been observed, while a briefing
        has to be assembled before the loop starts. Returning nothing in that
        window would silently disable memory exactly when the session has not
        opened an app yet, which is a failure that looks like an empty memory.
        Every note names its control, and the agent is told the screen outranks
        all of them, so an irrelevant note costs a few tokens rather than a
        wrong action.
        """
        notes = (
            self.notes.values() if app is None else (n for n in self.notes.values() if n.app == app)
        )
        return sorted(notes, key=lambda n: (n.app, n.target))

    def briefing(self, app: str | None) -> str | None:
        """What to tell the agent before it starts, or None if nothing.

        The framing is load-bearing. These are reported as things seen before
        and explicitly outranked by the current screen, because the failure mode
        of memory is an agent that believes a stale note over what it can see.
        """
        notes = self.about(app)
        if not notes:
            return None
        lines = "\n".join(n.render() for n in notes)
        if self.assertive:
            return (
                "Known about this app from earlier sessions:\n"
                f"{lines}\n"
                "Do not spend actions re-testing these. Treat them as settled and "
                "work around them."
            )
        return (
            "From earlier sessions on this app:\n"
            f"{lines}\n"
            "This is what happened before, not what is true now. Trust the screen "
            "over these notes, and if one turns out to be wrong, ignore it."
        )

    # -- persistence -------------------------------------------------------

    def load(self) -> Memory:
        if self.path is None or not self.path.is_file():
            return self
        raw = json.loads(self.path.read_text())
        for item in raw.get("notes", []):
            note = Note(**item)
            self.notes[note.key] = note
        return self

    def save(self) -> Path | None:
        if self.path is None:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written_at": time.time(),
            "notes": [asdict(n) for n in self.notes.values()],
        }
        self.path.write_text(json.dumps(payload, indent=2))
        return self.path


def default_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / DEFAULT_FILENAME
