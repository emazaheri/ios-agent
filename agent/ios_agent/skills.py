"""Per-app notes, loaded when the agent opens that app.

What this is. Some of what it takes to drive an app is not in the app's
accessibility tree: what it calls a screen, which of its tabs holds settings,
which word it uses where Apple would use another. That knowledge is cheap to
write down once and expensive to rediscover every run, and it is per app, so
it does not belong in the operator prompt that every run pays for.

What it is not, and this is the part that decides the design. ADR 0003
rejected cross-session memory because of what remembering an *outcome* does to
the agent: told assertively that a control was dead, one run in three finished
without touching the device and reported a failure it had not observed. So a
file here may say how an app is shaped and how to reach a screen. It may not
say that something will not work. The screen stays the evidence.

Two more rules, both learned elsewhere in this repository:

- A note that exists because the digest drops something is a bug report, not a
  skill. `docs/realities/third-party-apps.md` is the record of what happens
  when a rule encodes one app's habits; the fix belongs in perception.
- The text is charged to the run that reads it, so it is capped. A long file
  is a sign the app needs a perception fix, not a longer briefing.

`tests/unit/test_skills.py` enforces all of it against the shipped files.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources

#: What `build_tools` takes so the eval can run the same task with the
#: briefings on and off. A bundle id in, the notes or nothing out.
SkillLoader = Callable[[str], str | None]

#: Characters, not tokens, because a character count is the thing that can be
#: checked without a tokeniser. Roughly 375 tokens, against the ~190 per turn
#: ADR 0011 records for a tenth verb -- except this is paid once per app per
#: run rather than on every turn.
MAX_CHARS = 1500

#: Phrases that turn a description into a prediction. The list is short on
#: purpose: it catches the framing ADR 0003 measured, not every way English
#: can express doubt, and a reviewer is still the real guard.
FORBIDDEN = (
    "does not work",
    "doesn't work",
    "never works",
    "is dead",
    "there is no way",
    "always fails",
    "will fail",
    "do not bother",
    "don't bother",
)


def _dir() -> resources.abc.Traversable:
    return resources.files("ios_agent") / "skills" / "apps"


def app_skill(bundle_id: str) -> str | None:
    """The notes for one app, or `None` where nobody has written any.

    Missing is the ordinary case: there are two files here and a phone holds a
    hundred apps. A caller that treats `None` as an error has the polarity
    backwards.
    """
    if not bundle_id:
        return None
    path = _dir() / f"{bundle_id}.md"
    if not path.is_file():
        return None
    text = path.read_text().strip()
    if len(text) > MAX_CHARS:
        # Loud rather than truncated. A briefing cut mid-sentence is worse
        # than one nobody wrote, and the unit test means this can only reach a
        # run through a file added without running the suite.
        raise ValueError(f"{bundle_id}.md is {len(text)} characters, over the {MAX_CHARS} cap")
    return text or None


def shipped() -> dict[str, str]:
    """Every file here, keyed by the bundle id it is named for. For tests."""
    return {
        entry.name.removesuffix(".md"): entry.read_text().strip()
        for entry in _dir().iterdir()
        if entry.name.endswith(".md")
    }
