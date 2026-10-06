"""Scoring an exploratory run by what the agent reported.

Every other task is judged on the device, because what an agent claims and
what the phone did are different questions. An exploratory goal ends in a
report, and the report is the product: "tell me what is broken" is answered
by the words, not by any switch. So this one task class is scored on the
agent's own summary, and the rules for doing that are fixed here, before any
model has been run against them (ADR 0021).

A planted bug counts as reported when **one line** of the report matches every
pattern the bug carries: what was involved and what was wrong with it, so a
report that mentions the Done button in passing and calls something else
broken does not score the Done bug. Lines are newline-separated items, and
sentences within one, because a model writes either.

A false report is a line that says something is wrong and matches no planted
bug. Those are only candidates: the scorer cannot tell a working thing called
broken from a fair remark it has no pattern for, so each one is read by hand
and the reading recorded. Style suggestions are not failures and are not
counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

BugClass = Literal["self_evident", "needs_spec", "visual"]

#: Words that make a line a claim that something is wrong.
_COMPLAINT = re.compile(
    r"\b(broken|bug|fail|fails|failed|error|wrong|stuck|never|incorrect|"
    r"does ?n[o']?t|did ?n[o']?t|is ?n[o']?t|not working|unresponsive|missing|"
    r"inconsistent|mismatch|contradict)",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"\n+|(?<=[.;])\s+(?=\S)")


@dataclass(frozen=True, slots=True)
class PlantedBug:
    name: str
    bug_class: BugClass
    #: Every pattern must match the same line, case-insensitively.
    patterns: tuple[str, ...]
    why: str = ""

    def found_in(self, line: str) -> bool:
        return all(re.search(p, line, re.IGNORECASE) for p in self.patterns)


PLANTED: tuple[PlantedBug, ...] = (
    PlantedBug(
        "dead_done",
        "self_evident",
        (
            r"\bdone\b",
            r"nothing|no effect|does ?n[o']?t|did ?n[o']?t|unresponsive|respond|dead|"
            r"inactive|broken|not work|no change|stays",
        ),
        "Review's Done takes the tap and changes nothing.",
    ),
    PlantedBug(
        "save_claimed",
        "self_evident",
        (
            r"\bsav",
            r"name|profile",
            r"not set|not saved|was ?n[o']?t saved|did ?n[o']?t (save|persist|stick|keep)|"
            r"not (kept|persisted|stored)|lost|missing|empty|fail|but",
        ),
        "Profile says 'Profile saved'; Review says 'Name: Not set'.",
    ),
    PlantedBug(
        "upload_stuck",
        "self_evident",
        (
            r"upload|photo|spinner|progress",
            r"never|stuck|hang|forever|indefinite|endless|does ?n[o']?t (finish|complete|end)|"
            r"did ?n[o']?t (finish|complete|end)|still|keeps|not (finish|complete)",
        ),
        "Choosing a photo shows 'Uploading photo...' on every read, forever.",
    ),
    PlantedBug(
        "count_wrong",
        "self_evident",
        (
            r"selected|count|interest",
            r"wrong|incorrect|mismatch|off[- ]by|contradict|does ?n[o']?t match|does not match|"
            r"inaccurate|but only|instead of|one (more|too many)|"
            r"\b3\b[^.\n]*\b2\b|\b2\b[^.\n]*\b3\b",
        ),
        "'N selected' reads one more than the chips switched on.",
    ),
    PlantedBug(
        "email_first_char",
        "self_evident",
        (
            r"e-?mail",
            r"first (char|letter)|drop|lost|missing|truncat|cut off|mismatch|"
            r"did ?n[o']?t (land|take|accept|register)|not (land|accept|register|entered)|"
            r"wrong|incorrect|redacted|garbled|altered",
        ),
        "The email field loses the first character of each burst of keys (mobile-mcp #450).",
    ),
    PlantedBug(
        "name_over_cap",
        "needs_spec",
        (r"\bname", r"\b30\b|limit|cap\b|capped|max|exceed|\b38\b|\b40\b|too long|longer than"),
        "The name field takes 38 characters; the spec, when given, caps names at 30.",
    ),
    PlantedBug(
        "caption_unreadable",
        "visual",
        (r"white|contrast|unreadable|invisible|legib|can ?n[o']?t (read|see)",),
        "The Welcome caption is white on white. Expected to be missed by a tree reader.",
    ),
)


@dataclass(frozen=True, slots=True)
class ReportScore:
    caught: dict[str, bool]
    #: Bugs this run could be expected to catch: self-evident ones always,
    #: the spec one only when the goal stated the spec. Never the visual one.
    expected: tuple[str, ...]
    false_report_candidates: tuple[str, ...]

    @property
    def caught_expected(self) -> int:
        return sum(self.caught[name] for name in self.expected)

    @property
    def all_expected(self) -> bool:
        return self.caught_expected == len(self.expected)

    def by_class(self) -> dict[str, tuple[int, int]]:
        """Caught over planted, per class."""
        out: dict[str, tuple[int, int]] = {}
        for bug in PLANTED:
            got, total = out.get(bug.bug_class, (0, 0))
            out[bug.bug_class] = (got + self.caught[bug.name], total + 1)
        return out


def lines(report: str) -> list[str]:
    return [part.strip(" -*•\t") for part in _SPLIT.split(report) if part.strip()]


def score(report: str, *, spec_given: bool) -> ReportScore:
    units = lines(report)
    caught = {bug.name: any(bug.found_in(unit) for unit in units) for bug in PLANTED}
    expected = tuple(
        bug.name
        for bug in PLANTED
        if bug.bug_class == "self_evident" or (bug.bug_class == "needs_spec" and spec_given)
    )
    candidates = tuple(
        unit
        for unit in units
        if _COMPLAINT.search(unit) and not any(bug.found_in(unit) for bug in PLANTED)
    )
    return ReportScore(caught, expected, candidates)
