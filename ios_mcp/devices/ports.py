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


def held_ports(start: int, end: int) -> list[int]:
    """Ports in ``[start, end]`` that another process is already bound to."""
    return [port for port in range(start, end + 1) if not _is_available(port)]


def _is_available(port: int) -> bool:
    """Whether nothing holds ``port`` on the wildcard or on loopback, either family.

    Both kinds of address, because each catches what the other misses. With
    SO_REUSEADDR a specific address may share a port with another socket's
    wildcard, and a wildcard may share one with another socket's specific
    address. Checking ``127.0.0.1`` alone passed a port a Docker container held
    on ``*:8100``; WebDriverAgent, which listens on ``::``, then failed to bind,
    and the readiness probe reached the container until the startup timeout,
    reporting a runner that never started. Checking the wildcard alone would
    pass a go-ios forward on ``127.0.0.1``.
    """
    for family, address in (
        (socket.AF_INET, ""),
        (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET6, "::"),
        (socket.AF_INET6, "::1"),
    ):
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            # No IPv6 on this host, so nothing can hold the port there either.
            continue
        with sock:
            # Still set, so a port left in TIME_WAIT by a runner that just
            # stopped counts as free: a wildcard bind ignores it, a live
            # listener's wildcard does not.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((address, port))
            except OSError:
                return False
    return True
