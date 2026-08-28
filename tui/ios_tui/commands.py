"""What the front end can be told to do, in one list.

One registry serves two surfaces: the `/` menu above the input, and Textual's
command palette on ctrl+p. Two lists would drift, and a command that exists in
one place and not the other is worse than a command that exists in neither,
because a person who found it once will look for it again where they found it.

The names are verbs a person would type, not the method names behind them. A
command is named by what it does to the thing you are looking at.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass

from textual.command import DiscoveryHit, Hit, Provider


@dataclass(frozen=True, slots=True)
class Command:
    """One thing the front end can be asked to do."""

    name: str
    help: str
    #: The return is ignored. Some of these are Textual workers, which hand
    #: back a handle nothing here has any use for.
    run: Callable[[], object]
    #: Shown beside the name, when there is a key that does the same thing.
    key: str = ""


def matching(commands: Iterable[Command], query: str) -> list[Command]:
    """The commands worth offering for what has been typed so far.

    Prefix matches come first and in the registry's own order. Someone typing
    `/d` is reaching for `device`, and putting a command that merely contains a
    `d` above it would make the first keystroke unpredictable.

    A query that matches nothing returns nothing rather than everything: an
    empty menu says the name is wrong, and a full one says it is being ignored.
    """
    wanted = query.strip().lower()
    if not wanted:
        return list(commands)
    prefix = [c for c in commands if c.name.startswith(wanted)]
    contains = [c for c in commands if wanted in c.name and c not in prefix]
    return prefix + contains


class AppCommands(Provider):
    """The same registry, offered to Textual's command palette.

    Registered so `ctrl+p` and `/` cannot disagree. A command that exists in
    one and not the other is worse than one that exists in neither: someone who
    found it once will look for it again where they found it.
    """

    @property
    def _commands(self) -> list[Command]:
        source = getattr(self.app, "commands", None)
        return list(source()) if callable(source) else []

    async def discover(self) -> AsyncIterator[DiscoveryHit]:
        """What the palette shows before anything has been typed."""
        for command in self._commands:
            yield DiscoveryHit(f"/{command.name}", command.run, help=command.help)

    async def search(self, query: str) -> AsyncIterator[Hit]:
        """Textual's own fuzzy matcher, so the palette ranks the way it does
        everywhere else. The `/` menu uses prefix matching instead, because
        there the query is being typed into a command line rather than a
        search box."""
        matcher = self.matcher(query)
        for command in self._commands:
            name = f"/{command.name}"
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), command.run, help=command.help)
