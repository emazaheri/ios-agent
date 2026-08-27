"""The logging bridge, including the part that stops it wrecking the display.

`DevicePool.acquire` is the longest silence in the tool and its only signal is
INFO logging, so this bridge is the whole of what a person sees while a device
comes up. It has to catch every device module, it has to stop those records
reaching stderr, and it has to put the logger back exactly as it found it.
"""

from __future__ import annotations

import logging

import pytest
from ios_tui.bus import ListSink
from ios_tui.events import Progress
from ios_tui.progress import LOGGER, device_progress


def test_it_catches_every_device_module() -> None:
    """One handler on the parent, not one per module.

    Each device module logs to `getLogger(__name__)`, so attaching to the
    package root is what makes a new adapter visible without an edit here.
    """
    sink = ListSink()
    with device_progress(sink):
        logging.getLogger("ios_mcp.devices.pool").info("Leased %s (%s)", "iPhone", "simulator")
        logging.getLogger("ios_mcp.devices.simulator").info("Booting simulator")
        logging.getLogger("ios_mcp.devices.real_device").info("Starting WebDriverAgent")

    assert [e.text for e in sink.of_type(Progress)] == [
        "Leased iPhone (simulator)",
        "Booting simulator",
        "Starting WebDriverAgent",
    ]
    assert sink.of_type(Progress)[1].source == "ios_mcp.devices.simulator"


def test_nothing_escapes_to_a_handler_someone_else_installed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line that keeps a full-screen app from looking corrupted.

    Without `propagate = False`, a root `StreamHandler` (which `basicConfig`
    installs, and which plenty of libraries install for you) keeps writing to
    stderr straight through the canvas.
    """
    sink = ListSink()
    with caplog.at_level(logging.INFO), device_progress(sink):
        logging.getLogger("ios_mcp.devices.simulator").info("Booting simulator")

    assert sink.of_type(Progress), "the bridge did not receive the record"
    assert not [r for r in caplog.records if r.name.startswith("ios_mcp")], (
        "device logging reached a handler above the bridge"
    )


def test_the_logger_is_left_exactly_as_it_was() -> None:
    log = logging.getLogger(LOGGER)
    before = (log.level, log.propagate, list(log.handlers))

    with device_progress(ListSink()):
        assert log.propagate is False
        assert log.level == logging.INFO

    assert (log.level, log.propagate, list(log.handlers)) == before


def test_it_restores_on_the_way_out_of_an_exception() -> None:
    """A front end that dies must not leave the library's logging rewired."""
    log = logging.getLogger(LOGGER)
    before = (log.level, log.propagate, list(log.handlers))

    with pytest.raises(RuntimeError), device_progress(ListSink()):
        raise RuntimeError("the device never arrived")

    assert (log.level, log.propagate, list(log.handlers)) == before
