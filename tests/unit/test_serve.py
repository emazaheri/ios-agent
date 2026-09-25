"""The HTTP transport, which has no authentication and must not need any.

It is meant for a client on the same machine. Loopback was supposed to make
that safe, and did not: a web page can resolve its own hostname to 127.0.0.1
and reach a local server from inside the browser, which is DNS rebinding. A
request claiming to come from `attacker.example` was answered with a live
session. `fastmcp` checks `Host` and `Origin` when asked and its default is
off, so this file holds the asking, and the refusal to bind anywhere else.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

import ios_mcp.server.app as server_app
from ios_mcp.__main__ import _cmd_serve, http_run_options, is_loopback
from ios_mcp.config import Settings


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.5", "localhost", "LOCALHOST", "::1", "[::1]"]
)
def test_loopback_addresses_are_recognised(host: str) -> None:
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.2", "example.com", ""])
def test_everything_else_is_not(host: str) -> None:
    assert not is_loopback(host)


class _Server:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> None:
        self.runs.append(kwargs)


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: str = "http",
    host: str | None = None,
    allow_remote: bool = False,
) -> tuple[int, _Server]:
    server = _Server()
    monkeypatch.setattr(server_app, "build_server", lambda _settings: server)
    args = argparse.Namespace(transport=transport, host=host, port=None, allow_remote=allow_remote)
    return _cmd_serve(Settings(_env_file=None), args), server


def test_http_on_loopback_checks_where_requests_come_from(monkeypatch) -> None:
    code, server = _serve(monkeypatch)

    assert code == 0
    assert server.runs == [http_run_options("127.0.0.1", 8765)]
    assert server.runs[0]["host_origin_protection"] == "auto"


def test_another_address_is_refused_without_asking_for_it(monkeypatch, capsys) -> None:
    """Refused, not warned: a warning scrolls past while the phone stays reachable."""
    code, server = _serve(monkeypatch, host="0.0.0.0")

    assert code == 2
    assert server.runs == [], "the server started anyway"
    err = capsys.readouterr().err
    assert "no authentication" in err
    assert "--allow-remote" in err


def test_the_refusal_covers_a_host_set_in_configuration_too(monkeypatch) -> None:
    """Not just the flag: `IOS_MCP_SERVER__HOST` reaches the same bind."""
    server = _Server()
    monkeypatch.setattr(server_app, "build_server", lambda _settings: server)
    settings = Settings(_env_file=None)
    settings.server.host = "192.168.1.20"
    args = argparse.Namespace(transport="http", host=None, port=None, allow_remote=False)

    assert _cmd_serve(settings, args) == 2
    assert server.runs == []


def test_allow_remote_binds_and_says_so(monkeypatch, caplog) -> None:
    code, server = _serve(monkeypatch, host="0.0.0.0", allow_remote=True)

    assert code == 0
    assert server.runs[0]["host"] == "0.0.0.0"
    assert "no authentication" in caplog.text


def test_stdio_is_untouched(monkeypatch) -> None:
    code, server = _serve(monkeypatch, transport="stdio", host="0.0.0.0")

    assert code == 0
    assert server.runs == [{}], "stdio has no address and nothing to refuse"


# -- the guard itself, on the scope a loopback-bound server receives ----------


async def _status(host: str, origin: str | None = None) -> int:
    """Send one request through the real app, as bound to 127.0.0.1.

    The guard decides "loopback" from the socket the server is bound to, which
    an ordinary test client does not have, so the scope carries it explicitly.
    Only rejections are asserted here: an accepted request goes on to an MCP
    session manager that only a running server has started. The accepted case
    was checked against a running server.
    """
    options = http_run_options("127.0.0.1", 8765)
    app = server_app.build_server(Settings(_env_file=None)).http_app(
        host_origin_protection=options["host_origin_protection"]
    )
    headers = [(b"host", host.encode()), (b"content-type", b"application/json")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": ("127.0.0.1", 8765),
        "client": ("127.0.0.1", 50000),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def test_a_rebound_host_is_turned_away() -> None:
    """The request that used to get a live session."""
    assert await _status("attacker.example:8765", origin="http://attacker.example:8765") == 421


async def test_a_foreign_origin_on_a_local_host_is_turned_away() -> None:
    assert await _status("127.0.0.1:8765", origin="http://attacker.example") == 403
