"""What a command line offers, read from its parser, for holding docs/cli.md to it.

A reference page for flags is the one kind of documentation whose every line
can be checked, so it is: a flag added to a parser and not to the page fails a
test naming it, rather than surviving until someone types it and finds it
undocumented.
"""

from __future__ import annotations

import argparse
from pathlib import Path

CLI_DOC = Path(__file__).resolve().parents[1] / "docs" / "cli.md"


def surface(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    """Each subcommand, `""` for the top level, mapped to its long options."""

    def options(p: argparse.ArgumentParser) -> set[str]:
        return {
            flag
            for action in p._actions
            for flag in action.option_strings
            if flag.startswith("--") and flag != "--help"
        }

    found = {"": options(parser)}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                found[name] = options(sub)
    return found


def missing_from_doc(parser: argparse.ArgumentParser) -> list[str]:
    """Every subcommand and long option the page does not name in backticks."""
    text = CLI_DOC.read_text()
    missing = []
    for command, flags in surface(parser).items():
        if command and f"`{command}`" not in text:
            missing.append(command)
        missing.extend(
            f"{command or '(top level)'} {flag}" for flag in sorted(flags) if f"`{flag}" not in text
        )
    return missing
