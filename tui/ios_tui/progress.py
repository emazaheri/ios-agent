"""Device startup, bridged out of `logging`.

`DevicePool.acquire` has no progress callbacks, and it is the longest silence
in the whole tool: a simulator boot can take 180 seconds, a WebDriverAgent
build a few minutes, and `wda.startup_timeout_s` is raised to 300 for a phone.
What it does have is INFO logging, on `ios_mcp.devices.*`, saying exactly the
things a person waiting wants to read ("Booting simulator", "Starting
WebDriverAgent from", "Building and launching WDA from source; this takes a few
minutes").

So the front end borrows the logger rather than asking the library to grow a
callback for one consumer's benefit.

`propagate = False` is the load-bearing line. Without it, a root handler that
someone else installed keeps writing to stderr, straight through a Textual
canvas, and the app looks corrupted rather than busy. For the same reason
nothing in this package ever calls `basicConfig`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from ios_tui.bus import EventSink
from ios_tui.events import Progress

#: Every device module logs to `getLogger(__name__)` under this root, so one
#: handler here catches the pool, both adapters, the tunnel and devicectl.
LOGGER = "ios_mcp"


class LogBridge(logging.Handler):
    """Turns log records into events. Never raises; a broken display must not
    break a run that is working."""

    def __init__(self, sink: EventSink) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink.emit(Progress(text=record.getMessage(), source=record.name))
        except Exception:
            self.handleError(record)


@contextmanager
def device_progress(sink: EventSink, *, level: int = logging.INFO) -> Iterator[None]:
    """Route `ios_mcp` logging into `sink` for the duration.

    Everything it changes is restored, including on the way out of an
    exception, because a front end that leaves a library's logging rewired
    after it exits has broken every other consumer in the process.
    """
    log = logging.getLogger(LOGGER)
    previous_level = log.level
    previous_propagate = log.propagate
    previous_handlers = list(log.handlers)

    log.handlers = [LogBridge(sink)]
    log.propagate = False
    log.setLevel(level)
    # Warnings would otherwise reach stderr by their own route.
    logging.captureWarnings(True)
    try:
        yield
    finally:
        logging.captureWarnings(False)
        log.handlers = previous_handlers
        log.propagate = previous_propagate
        log.setLevel(previous_level)
