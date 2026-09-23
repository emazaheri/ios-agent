"""What a per-app skill file is allowed to say, checked against the files.

The rule these enforce is not a style preference. ADR 0003 measured what
happens when the agent is told assertively that something on the device will
not work: it stops checking, and reports a failure it never observed. A skill
file is static and reviewed where memory was written by the agent, but the
sentence reaching the model is the same sentence, so the constraint has to be
the same one.

`test_no_file_asserts_an_outcome` is therefore the load-bearing test in this
file. The others keep the briefing small, legible and legibly stale.
"""

from __future__ import annotations

import pytest
from ios_agent.skills import FORBIDDEN, MAX_CHARS, app_skill, shipped

_FILES = sorted(shipped().items())
_IDS = [name for name, _ in _FILES]


@pytest.mark.parametrize("text", [t for _, t in _FILES], ids=_IDS)
def test_no_file_asserts_an_outcome(text: str) -> None:
    lowered = text.lower()
    hits = [phrase for phrase in FORBIDDEN if phrase in lowered]
    assert not hits, (
        f"says {hits}, which is the framing ADR 0003 rejected. Describe the app, "
        "not what an attempt on it will do."
    )


@pytest.mark.parametrize("text", [t for _, t in _FILES], ids=_IDS)
def test_every_file_is_under_the_cap(text: str) -> None:
    assert len(text) <= MAX_CHARS, (
        f"{len(text)} characters against a cap of {MAX_CHARS}. The run pays for every one of them."
    )


@pytest.mark.parametrize("text", [t for _, t in _FILES], ids=_IDS)
def test_every_file_says_what_it_was_written_against(text: str) -> None:
    """A briefing with no date on it cannot be recognised as out of date."""
    assert text.startswith("# "), "the first line names the app"
    assert any(line.startswith("Written against ") for line in text.split("\n")), (
        "no line starting `Written against `, so nothing says which build this describes"
    )


@pytest.mark.parametrize("name", _IDS)
def test_every_file_is_named_for_a_bundle_id(name: str) -> None:
    assert name.count(".") >= 2 and " " not in name, (
        f"{name}.md is not a bundle id, so no lookup will ever find it"
    )


def test_an_app_nobody_wrote_about_is_not_an_error() -> None:
    assert app_skill("com.example.nothing-here") is None
    assert app_skill("") is None


def test_a_shipped_file_loads_by_its_bundle_id() -> None:
    for name, text in _FILES:
        assert app_skill(name) == text


# -- what reaches the transcript --------------------------------------------


class _Opens:
    """The two lines of `Backend` this path touches, and a switch to fail."""

    def __init__(self, *, works: bool = True) -> None:
        self.works = works
        self.opened: list[str] = []

    async def open_app(self, name: str) -> str:
        from ios_mcp.errors import IosAutomationError

        self.opened.append(name)
        if not self.works:
            raise IosAutomationError("no such app")
        return "Cards\n- e1 button Discover"


def _run(**kwargs: object):
    from ios_agent.tools import Run

    from ios_mcp.devices.base import AppInfo

    apps = [
        AppInfo(bundle_id="com.example.cards", name="Cards", kind="user"),
        AppInfo(bundle_id="com.apple.Maps", name="Maps", kind="system"),
    ]
    return Run(backend=_Opens(), goal="anything", apps=apps, **kwargs)  # type: ignore[arg-type]


def _open_app(run: object, skills: object):
    from ios_agent.tools import build_tools

    return next(t for t in build_tools(run, skills=skills) if t.name == "open_app")  # type: ignore[arg-type]


async def test_opening_an_app_appends_what_was_written_about_it() -> None:
    run = _run()
    reply = await _open_app(run, lambda _b: "It calls its settings Me.").ainvoke({"name": "Cards"})

    assert "Cards" in reply, "the screen itself must still be there"
    assert "It calls its settings Me." in reply
    assert run.skill_tokens > 0, "a briefing the report does not charge for is a hidden cost"


async def test_the_same_app_is_briefed_once_per_run() -> None:
    """The second open is the cheap one, or the notes are a per-call tax."""
    run = _run()
    tool = _open_app(run, lambda _b: "It calls its settings Me.")

    first = await tool.ainvoke({"name": "Cards"})
    charged = run.skill_tokens
    second = await tool.ainvoke({"name": "Cards"})

    assert "It calls its settings Me." in first
    assert "It calls its settings Me." not in second
    assert run.skill_tokens == charged


async def test_an_app_that_did_not_open_is_not_briefed() -> None:
    """Notes about a screen the agent is not on are the worst version of this."""
    run = _run()
    run.backend.works = False  # type: ignore[attr-defined]
    reply = await _open_app(run, lambda _b: "It calls its settings Me.").ainvoke({"name": "Cards"})

    assert "It calls its settings Me." not in reply
    assert run.skill_tokens == 0


async def test_the_briefing_can_be_turned_off_entirely() -> None:
    """The control arm of the measurement, and the reason `skills` is a
    parameter rather than an import."""
    run = _run()
    reply = await _open_app(run, None).ainvoke({"name": "Cards"})

    assert "Notes on this app" not in reply
    assert run.skill_tokens == 0


async def test_an_app_the_device_does_not_have_is_not_an_error() -> None:
    run = _run()
    reply = await _open_app(run, lambda _b: "notes").ainvoke({"name": "Nowhere"})

    assert "notes" not in reply
    assert run.skill_tokens == 0


async def test_the_name_is_matched_the_way_the_session_matches_it() -> None:
    """The briefing has to follow the app that actually opened.

    `IosSession.open_app` resolves a name fuzzily, on purpose: handing an
    agent a list of thirty apps to disambiguate costs a whole turn. A lookup
    here that demanded the exact name would open Cards on "the cards app" and
    then brief nobody, and nothing in the run would say so.
    """
    run = _run()
    reply = await _open_app(run, lambda b: f"notes for {b}").ainvoke({"name": "cards app"})

    assert "notes for com.example.cards" in reply
