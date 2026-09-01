"""One command from a fresh clone to a device that moves.

The interesting cases are the ones where it declines to act: no runtime, no
consent, nobody attached to give consent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from ios_tui.quickstart import Quickstart

from ios_mcp.config import Settings


@dataclass
class FakeCheck:
    name: str
    status: str
    detail: str = ""
    remedy: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeReport:
    checks: list[FakeCheck]
    summary: str = "a summary"


def _ready() -> list[FakeCheck]:
    return [
        FakeCheck("xcode", "ok"),
        FakeCheck("simctl", "ok"),
        FakeCheck("simulators", "ok"),
        FakeCheck("wda-bundle", "ok", data={"xctestrun": "/somewhere.xctestrun"}),
    ]


def _quickstart(**kwargs: Any) -> Quickstart:
    return Quickstart(Settings(), **kwargs)


async def test_a_ready_machine_asks_for_nothing(capsys) -> None:
    q = _quickstart()
    q.ask = lambda question: pytest.fail(f"should not have asked: {question}")  # type: ignore[method-assign]
    assert q._toolchain_is_usable(FakeReport(_ready()))
    assert await q._ensure_a_simulator(FakeReport(_ready()))
    assert q._ensure_webdriveragent(FakeReport(_ready()))


async def test_a_missing_runtime_is_named_and_never_downloaded(capsys) -> None:
    """8 GB belongs to a command someone starts deliberately, where they can
    watch it and stop it."""
    checks = _ready()
    checks[2] = FakeCheck(
        "simulators",
        "fail",
        "no iOS runtime installed",
        remedy="xcodebuild -downloadPlatform iOS",
        data={"can_create": False},
    )
    q = _quickstart()
    q.ask = lambda question: pytest.fail("must not offer to download a runtime")  # type: ignore[method-assign]

    assert not await q._ensure_a_simulator(FakeReport(checks))
    err = capsys.readouterr().err
    assert "downloadPlatform" in err
    assert "8 GB" in err


async def test_creating_a_simulator_needs_a_yes(capsys) -> None:
    """Cheap is not the same as ours to decide."""
    checks = _ready()
    checks[2] = FakeCheck(
        "simulators", "warn", "a runtime but no device", data={"can_create": True, "runtime": "iOS"}
    )
    q = _quickstart()
    q.ask = lambda question: False  # type: ignore[method-assign]

    assert not await q._ensure_a_simulator(FakeReport(checks))
    assert "simctl create" in capsys.readouterr().out


def test_building_webdriveragent_needs_a_yes(capsys) -> None:
    checks = _ready()
    checks[3] = FakeCheck("wda-bundle", "warn", "no build found", data={})
    q = _quickstart()
    q.ask = lambda question: False  # type: ignore[method-assign]

    assert not q._ensure_webdriveragent(FakeReport(checks))
    assert "does it by hand" in capsys.readouterr().out


def test_no_terminal_means_no_consent(monkeypatch, capsys) -> None:
    """An absent person has not agreed to anything. A script that wants this
    can say so with --yes; one that merely has no tty has not."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert _quickstart().ask("Create one?") is False
    assert "--yes to proceed" in capsys.readouterr().out


def test_yes_is_recorded_rather_than_silent(capsys) -> None:
    assert _quickstart(assume_yes=True).ask("Create one?") is True
    assert "(--yes)" in capsys.readouterr().out


def test_xcode_missing_stops_before_anything_else(capsys) -> None:
    """Nothing here can install Xcode, so it says so and stops."""
    checks = _ready()
    checks[0] = FakeCheck("xcode", "fail", "not found", remedy="Install Xcode.")
    q = _quickstart()

    assert not q._toolchain_is_usable(FakeReport(checks))
    assert "Install Xcode." in capsys.readouterr().err
