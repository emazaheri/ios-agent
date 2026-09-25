"""Argument handling, which is the part a person meets first.

The bare-goal form is the one worth testing. `ios-agent "turn on bold text"`
has to keep working, and so does the same thing with flags in front of it,
without either of them swallowing a real subcommand.
"""

from __future__ import annotations

import argparse

import pytest
from ios_agent.state import Outcome
from ios_tui.cli import _normalise, build_parser, trail_row
from screens import DeviceModel, build_session
from tui_harness import settings

from ios_mcp.config import Settings
from ios_mcp.policy.audit import AuditEntry


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], []),
        (["--help"], ["--help"]),
        (["devices"], ["devices"]),
        (["doctor", "--json"], ["doctor", "--json"]),
        (["run", "--approve", "erase it"], ["run", "--approve", "erase it"]),
        # The forms that have to keep working without the verb.
        (["turn on bold text"], ["run", "turn on bold text"]),
        (
            ["--device", "iPhone", "turn wi-fi off"],
            ["run", "--device", "iPhone", "turn wi-fi off"],
        ),
        # A global flag before a real subcommand must not be turned into a goal.
        (["--config", "x.toml", "devices"], ["--config", "x.toml", "devices"]),
        # `reset` is a verb and also an ordinary English word. The test is on
        # the whole token, so the subcommand survives and the goal stays a goal.
        (["reset"], ["reset"]),
        (["reset", "-y"], ["reset", "-y"]),
        (["reset the alarm"], ["run", "reset the alarm"]),
    ],
)
def test_a_bare_goal_means_run(argv: list[str], expected: list[str]) -> None:
    assert _normalise(argv) == expected


def test_the_bare_goal_form_parses_into_a_runnable_command() -> None:
    """Normalising is only half of it; the result has to parse."""
    args = build_parser().parse_args(_normalise(["--device", "iPhone", "turn wi-fi off"]))
    assert args.command == "run"
    assert args.goal == "turn wi-fi off"
    assert args.device == "iPhone"


def test_run_defaults_to_the_full_screen_front_end() -> None:
    args = build_parser().parse_args(_normalise(["turn on bold text"]))
    assert args.no_tui is False
    assert args.approve is False, "approval is opt-in; unattended runs refuse"


def test_a_bare_invocation_means_run_with_nothing_to_do_yet() -> None:
    """Opening the front end should not require knowing what you want from it.

    `ios-agent` alone printed help and exited, so the only way into the TUI in
    agent mode was to have already decided on a goal and typed it as an
    argument. The app has an input box; it can be asked later.
    """
    args = build_parser().parse_args(_normalise([]) or ["run"])
    assert args.command == "run"
    assert args.goal is None


def test_run_without_a_goal_is_allowed() -> None:
    args = build_parser().parse_args(["run"])
    assert args.command == "run"
    assert args.goal is None
    assert args.no_tui is False


def test_the_plain_front_end_still_needs_a_goal() -> None:
    """`--no-tui` has nothing to type into, so it is the one shape that
    genuinely cannot be asked later."""
    from ios_tui.cli import main

    assert main(["--no-tui"]) == 2


# -- the trail table --------------------------------------------------------


def _entry(**kwargs) -> AuditEntry:
    fields = {"seq": 1, "at": 0.0, "action": "tap", "args": {}, "ok": True}
    return AuditEntry(**{**fields, **kwargs})


def test_a_successful_row_reports_whether_the_screen_moved() -> None:
    row = trail_row(_entry(target="Wi-Fi", screen_changed=True))
    assert "tap" in row
    assert "Wi-Fi" in row
    assert "changed=True" in row


def test_a_failed_row_says_so_rather_than_looking_idle() -> None:
    """Without this a refused launch reads as a launch that changed nothing,
    which is the one thing the table exists to tell apart."""
    row = trail_row(_entry(action="launch_app:x", ok=False, code="app_not_allowed"))
    assert "FAILED" in row
    assert "app_not_allowed" in row
    assert "changed=" not in row


def test_a_failure_from_before_codes_were_recorded_still_renders() -> None:
    row = trail_row(_entry(ok=False, code=None))
    assert "FAILED unknown" in row


def test_a_row_falls_back_to_the_argument_when_nothing_was_resolved() -> None:
    row = trail_row(_entry(action="open_url", args={"url": "App-prefs:root"}))
    assert "App-prefs:root" in row


# -- the exit code, which is the whole of what a scripted caller sees --------


class _StubRunner:
    """A runner that reports a given outcome without a device or a model.

    `_run_plain` builds its own `GoalRunner` and `AgentSettings`, so the only
    way to reach the exit code deterministically is to stand in for the runner.
    The contradicted branch cannot be produced on demand by a real model
    anyway: it needs a device that accepts an action and does not move, which
    is an injection the scripted device has and hardware does not.
    """

    def __init__(self, outcome: Outcome) -> None:
        self._outcome = outcome
        self.last_screen = "screen: com.apple.Preferences"
        self.closed = False

    def __call__(self, *_args: object, **_kwargs: object) -> _StubRunner:
        return self

    async def start(self) -> object:
        model = DeviceModel()
        session, _, _ = build_session(model, settings())
        return session

    async def run(self, *_args: object, **_kwargs: object) -> Outcome:
        return self._outcome

    async def close(self) -> None:
        self.closed = True


async def _exit_code(monkeypatch, outcome: Outcome) -> tuple[int, _StubRunner]:
    import ios_tui.runner as runner_module
    from ios_tui.cli import _run_plain

    stub = _StubRunner(outcome)
    monkeypatch.setattr(runner_module, "GoalRunner", stub)
    args = argparse.Namespace(
        goal="turn on airplane mode",
        approve=False,
        max_steps=None,
        verbose=False,
        device=None,
        app=None,
    )
    return await _run_plain(Settings(), args), stub


async def test_a_verified_run_exits_zero(monkeypatch) -> None:
    outcome = Outcome(goal="g", succeeded=True, summary="done")
    code, stub = await _exit_code(monkeypatch, outcome)

    assert outcome.verified is True
    assert code == 0
    assert stub.closed, "the session was left open"


async def test_a_claim_the_device_denies_exits_non_zero(monkeypatch) -> None:
    """The bug this closed: a scripted caller could not tell a lie from a pass.

    `succeeded` stays true, because it is what the agent said. The exit code
    follows the verdict instead.
    """
    outcome = Outcome(goal="g", succeeded=True, summary="done", contradicted=True)
    code, _ = await _exit_code(monkeypatch, outcome)

    assert outcome.succeeded is True
    assert outcome.verified is False
    assert code == 1


async def test_an_honest_failure_still_exits_non_zero(monkeypatch) -> None:
    code, _ = await _exit_code(monkeypatch, Outcome(goal="g", succeeded=False, summary="no"))

    assert code == 1


def test_no_tui_without_a_goal_is_a_usage_error(capsys) -> None:
    """The one shape that genuinely needs a goal up front."""
    from ios_tui.cli import _cmd_run

    args = argparse.Namespace(no_tui=True, goal=None)

    assert _cmd_run(Settings(), args) == 2
    assert "needs a goal" in capsys.readouterr().err
