"""Driving the phone by hand, with no model in the loop.

Worth having for two reasons that have nothing to do with saving API credits,
though it does that too.

**It is the fastest way to debug perception on an app nobody has pointed this
at before.** When a target will not resolve, the question is what the digest
actually says, and the loop between reading it and trying a different string
should be one keystroke rather than one model turn.

**It makes the tool usable with no provider configured at all.** Everything
below the agent works without a key, and a front end that refuses to start
without one would hide that.

The parser is deliberately thin. These are the same eight verbs the agent has,
spelled the same way, so what you type by hand and what the agent does are the
same operations and produce the same events, the same counters and the same
audit rows.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from ios_agent.backend import Backend


class Unknown(ValueError):
    """The line was not a verb this understands.

    Carries a suggestion when one is close enough to be worth offering. A
    grammar dump for a typo is a wall of text where one line would do, and it
    buries the thing that was actually wrong.
    """

    def __init__(self, typed: str, suggestion: str | None = None) -> None:
        self.typed = typed
        self.suggestion = suggestion
        if suggestion:
            super().__init__(f"{typed!r} is not a verb here. Did you mean {suggestion!r}?")
        else:
            super().__init__(f"{typed!r} is not a verb here. Type `help` to see them.")


class Help(Exception):
    """`help` was asked for. Not an error, and it used to be reported as one.

    It is in `USAGE` as a command, and typing it answered "I do not understand
    'help'", which is the tool disagreeing with its own documentation.
    """


@dataclass(frozen=True, slots=True)
class Command:
    verb: str
    run: Callable[[Backend], Awaitable[str]]


#: What `help` prints, and the only place the grammar is written down.
USAGE = """\
observe                     read the screen
tap <target>                tap something by its label, value or id
type <text>                 type into the focused field
type <text> > <target>      type into a named field
set <value> > <target>      set a switch or slider
scroll <up|down|left|right> [> <until>]
press <home|back|enter|volumeup|volumedown>
open <url>                  open a deep link
help                        this
"""


def parse(line: str) -> Command:
    """Turn one typed line into one call on the backend.

    Every action gets a fresh idempotency key. By hand there is no node to
    replay, so two identical commands are two deliberate requests, and keying
    them the same would silently drop the second."""
    text = line.strip()
    if not text:
        raise Unknown("")
    verb, _, rest = text.partition(" ")
    verb, rest = verb.lower(), rest.strip()

    def key() -> str:
        return uuid4().hex

    match verb:
        case "observe" | "o":
            return Command("observe", lambda b: b.observe())
        case "tap" | "t" if rest:
            return Command("tap", lambda b: b.tap(rest, idem_key=key()))
        case "type" if rest:
            text_part, target = _split(rest)
            return Command("type_text", lambda b: b.type_text(text_part, target, idem_key=key()))
        case "set" if ">" in rest:
            value, target = _split(rest)
            if target is None:
                raise Unknown(USAGE)
            return Command("set_value", lambda b: b.set_value(value, target, idem_key=key()))
        case "scroll" | "s" if rest:
            direction, until = _split(rest)
            return Command("scroll", lambda b: b.scroll(direction, until, idem_key=key()))
        case "press" | "p" if rest:
            return Command("press_button", lambda b: b.press_button(rest, idem_key=key()))
        case "open" if rest:
            return Command("open_url", lambda b: b.open_url(rest))
        case "help" | "?":
            raise Help
        case _:
            raise Unknown(verb, _nearest(verb))


#: What a bare verb can be, for suggesting a near miss.
_VERBS = ("observe", "tap", "type", "set", "scroll", "press", "open", "help")


def _nearest(verb: str) -> str | None:
    """The closest verb, when one is close enough to be worth offering.

    A cutoff rather than a best-effort match: suggesting `press` for `hello`
    is worse than suggesting nothing, because it reads as though the tool
    thinks it understood.
    """
    from difflib import get_close_matches

    matches = get_close_matches(verb, _VERBS, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _split(rest: str) -> tuple[str, str | None]:
    """`<argument> > <target>`, where the target is optional.

    `>` rather than a quoted grammar because targets are arbitrary UI strings
    full of spaces, commas and quotes, and anything cleverer would mean
    escaping the labels this exists to let you type quickly.
    """
    argument, sep, target = rest.partition(">")
    return argument.strip(), target.strip() if sep and target.strip() else None
