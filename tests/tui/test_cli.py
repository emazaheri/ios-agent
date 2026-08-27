"""Argument handling, which is the part a person meets first.

The bare-goal form is the one worth testing. `ios-agent "turn on bold text"`
has to keep working, and so does the same thing with flags in front of it,
without either of them swallowing a real subcommand.
"""

from __future__ import annotations

import pytest
from ios_tui.cli import _normalise, build_parser


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
