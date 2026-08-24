"""Host port allocation for WebDriverAgent forwards."""

from __future__ import annotations

import socket
import threading

from ios_mcp.errors import DeviceNotReady

_lock = threading.Lock()
_reserved: set[int] = set()


def free_port(start: int, end: int) -> int:
    """Reserve the first port in ``[start, end]`` that nothing is listening on.

    Reservations are tracked in-process because a port can be free at bind-check
    time and taken moments later by a concurrent session on the same host.
    """
    with _lock:
        for port in range(start, end + 1):
            if port in _reserved:
                continue
            if _is_available(port):
                _reserved.add(port)
                return port
    raise DeviceNotReady(
        f"No free port in range {start}-{end}",
        hint="Widen wda.port_range, or close sessions you are no longer using.",
    )


def release_port(port: int) -> None:
    with _lock:
        _reserved.discard(port)


def _is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True
