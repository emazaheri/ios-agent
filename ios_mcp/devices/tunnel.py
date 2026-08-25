"""RemoteXPC tunnel discovery, shared by the doctor and the device adapter.

iOS 17 moved device communication from TCP to QUIC + RemoteXPC, so a tunnel
must exist before anything can reach a physical device. This lived in two
places once and immediately drifted: the doctor learned that go-ios 1.3.x no
longer serves its HTTP control API by default while the adapter went on
probing it and refusing to start on a working setup.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ios_mcp.config import GoIosSettings
from ios_mcp.devices.shell import probe, which

logger = logging.getLogger(__name__)


async def list_tunnels(settings: GoIosSettings) -> list[dict[str, Any]]:
    """Every live tunnel, from whichever source go-ios is exposing.

    Asks the CLI first: the HTTP control API only exists in the opt-in daemon
    mode (ENABLE_GO_IOS_AGENT), so probing it alone reports "no tunnel" on a
    perfectly working `sudo ios tunnel start`.
    """
    if which(settings.binary) is not None:
        result = await probe(settings.binary, "tunnel", "ls", timeout=20.0)
        if result is not None and result.ok:
            tunnels = _parse(result.stdout)
            if tunnels:
                return tunnels

    url = f"http://{settings.tunnel_api_host}:{settings.tunnel_api_port}/tunnel/list"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
        if response.status_code == 200:
            payload = response.json()
            raw = payload if isinstance(payload, list) else payload.get("tunnels", [])
            return [t for t in raw if isinstance(t, dict)]
    except (httpx.HTTPError, ValueError):
        pass
    return []


async def tunnel_for(settings: GoIosSettings, udid: str) -> dict[str, Any] | None:
    """The tunnel serving one device, if any."""
    for tunnel in await list_tunnels(settings):
        if tunnel.get("udid") == udid:
            return tunnel
    return None


def _parse(stdout: str) -> list[dict[str, Any]]:
    """Pull the tunnel list out of go-ios output.

    go-ios interleaves structured log lines with its result on stdout, so the
    payload is the last line that parses as a JSON array.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, list):
            return [t for t in parsed if isinstance(t, dict)]
    return []
