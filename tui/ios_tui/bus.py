"""Where events go.

`emit` is **synchronous**, and that is the whole design decision in this file.

An `async emit` would put an `await` inside every backend verb. That is a
scheduling point the bare `SessionBackend` does not have, in the one path whose
behaviour has to stay identical whether or not a front end is attached. The
rule this package is held to is that a front end may not change what a run
costs; the cheapest way to keep it is to make the instrumentation incapable of
yielding.

Sync also means a `logging.Handler` can use the same sink. Handlers are sync
and may fire from a thread, which is why `QueueSink` carries a loop.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from ios_tui.events import Event


class EventSink(Protocol):
    """Somewhere to put an event.

    Implementations must not raise and must not block. A front end that falls
    over because it could not draw something has broken a run that was working.
    """

    def emit(self, event: Event) -> None: ...


class ListSink:
    """Collects events in order. What the tests assert against."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def of_type[T: Event](self, kind: type[T]) -> list[T]:
        return [e for e in self.events if isinstance(e, kind)]


class NullSink:
    """Discards everything. Lets the seam be used where nobody is watching."""

    def emit(self, event: Event) -> None:
        return None


class QueueSink:
    """Feeds an asyncio queue from anywhere.

    The queue is unbounded on purpose. A bounded queue leaves two options when
    it fills, and both are wrong here: blocking is impossible in a sync `emit`,
    and dropping loses an `ActionFinished`, which corrupts the record of what
    happened to someone's phone. Events are small and a run is bounded by
    `max_steps`, so unbounded is a real ceiling rather than an unbounded one.
    """

    def __init__(self, queue: asyncio.Queue[Event], loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def emit(self, event: Event) -> None:
        try:
            if asyncio.get_running_loop() is self._loop:
                self._queue.put_nowait(event)
                return
        except RuntimeError:
            # No running loop: a logging handler on a thread, most likely.
            pass
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)


async def drain(queue: asyncio.Queue[Event]) -> list[Event]:
    """Wait for one event, then take everything else already waiting.

    Consumers redraw once per batch rather than once per event. Streamed model
    text arrives at token rate, and a widget refresh per token is how a
    terminal app becomes slower than the model it is displaying.
    """
    batch = [await queue.get()]
    while True:
        try:
            batch.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return batch
