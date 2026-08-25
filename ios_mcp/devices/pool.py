"""Device pool: allocation, one live session per device, idle reclamation.

This is the seam where a remote/farm adapter or a multi-host registry would
drop in later without touching any layer above it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ios_mcp.config import Settings
from ios_mcp.devices.base import DeviceAdapter, DeviceInfo
from ios_mcp.devices.discovery import list_devices
from ios_mcp.devices.real_device import RealDeviceAdapter
from ios_mcp.devices.simulator import SimulatorAdapter
from ios_mcp.errors import DeviceUnavailable
from ios_mcp.wda.client import WdaClient
from ios_mcp.wda.session import WdaSession

logger = logging.getLogger(__name__)


@dataclass
class Lease:
    """One device, its adapter, and its live WDA session."""

    device: DeviceInfo
    adapter: DeviceAdapter
    session: WdaSession
    opened_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used_at = time.monotonic()

    @property
    def idle_for(self) -> float:
        return time.monotonic() - self.last_used_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device.to_dict(),
            "wda_url": self.session.client.base_url,
            "session_id": self.session.session_id,
            "foreground_app": self.session.bundle_id,
            "age_s": round(time.monotonic() - self.opened_at, 1),
            "idle_s": round(self.idle_for, 1),
            "recovered_count": self.session.recovered_count,
        }


class DevicePool:
    """Allocates devices and keeps at most one WDA session per device."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._leases: dict[str, Lease] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, udid: str) -> asyncio.Lock:
        return self._locks.setdefault(udid, asyncio.Lock())

    # -- allocation --------------------------------------------------------

    async def acquire(
        self,
        device: str | None = None,
        *,
        bundle_id: str | None = None,
        fresh: bool = False,
    ) -> Lease:
        """Get a ready session for a device, reusing a live one when possible.

        ``device`` accepts a UDID, an exact name, or a case-insensitive
        substring of a name, because an agent is far more likely to say
        "iPhone 16" than to know a UDID.
        """
        info = await self.resolve(device)
        async with self._lock_for(info.udid):
            existing = self._leases.get(info.udid)
            if existing is not None and await existing.session.client.is_alive():
                existing.touch()
                if bundle_id:
                    await existing.session.launch_app(bundle_id, fresh=fresh)
                return existing
            if existing is not None:
                await self._discard(existing)

            adapter = self._adapter_for(info)
            endpoint = await adapter.ensure_runner()

            async def relaunch() -> str:
                restarted = await adapter.ensure_runner()
                return restarted.base_url

            client = WdaClient(endpoint.base_url, self.settings.wda)
            session = WdaSession(client, self.settings, relaunch=relaunch)
            await session.open(bundle_id, fresh=fresh)

            lease = Lease(device=info, adapter=adapter, session=session)
            self._leases[info.udid] = lease
            logger.info("Leased %s (%s)", info.name, info.udid)
            return lease

    async def resolve(self, device: str | None) -> DeviceInfo:
        """Pick a device by UDID, name, or substring, else the best default."""
        devices = await list_devices(self.settings)
        if not devices:
            raise DeviceUnavailable(
                "No iOS devices or simulators are available",
                hint="Run ios_doctor to find out what is missing.",
            )

        wanted = device or self.settings.default_device
        if wanted:
            for d in devices:
                if d.udid == wanted or d.name == wanted:
                    return d
            matches = [d for d in devices if wanted.lower() in d.name.lower()]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                booted = [d for d in matches if d.state == "Booted"]
                if len(booted) == 1:
                    return booted[0]
                raise DeviceUnavailable(
                    f"{len(matches)} devices match {wanted!r}",
                    hint="Pass an exact name or UDID: "
                    + ", ".join(f"{d.name} ({d.udid[:8]})" for d in matches[:5]),
                )
            raise DeviceUnavailable(
                f"No device matches {wanted!r}",
                hint="Call ios_list_devices to see what is available.",
            )

        return _best_default(devices)

    def _adapter_for(self, info: DeviceInfo) -> DeviceAdapter:
        if info.kind == "simulator":
            return SimulatorAdapter(info, self.settings)
        return RealDeviceAdapter(info, self.settings)

    # -- release -----------------------------------------------------------

    async def release(self, udid: str) -> None:
        async with self._lock_for(udid):
            lease = self._leases.pop(udid, None)
            if lease is not None:
                await self._discard(lease)

    async def release_all(self) -> None:
        for udid in list(self._leases):
            await self.release(udid)

    async def reap_idle(self, max_idle_s: float) -> int:
        """Drop leases that nothing has touched recently."""
        stale = [udid for udid, lease in self._leases.items() if lease.idle_for > max_idle_s]
        for udid in stale:
            logger.info("Reaping idle lease for %s", udid)
            await self.release(udid)
        return len(stale)

    async def _discard(self, lease: Lease) -> None:
        try:
            await lease.session.close()
        finally:
            await lease.adapter.teardown()

    # -- introspection -----------------------------------------------------

    @property
    def leases(self) -> list[Lease]:
        return list(self._leases.values())

    def get(self, udid: str) -> Lease | None:
        return self._leases.get(udid)


def _is_phone(device: DeviceInfo) -> bool:
    name = device.name.lower()
    model = (device.model or "").lower()
    return "iphone" in name or "iphone" in model


def _best_default(devices: list[DeviceInfo]) -> DeviceInfo:
    """Pick the safest useful device when the caller did not name one.

    Order matters. A simulator outranks a physical phone even when the phone is
    connected and the simulator is cold: acting on someone's real device should
    be a deliberate choice, never a default. An iPhone outranks an iPad, because
    this is a phone automation tool and an iPad's split-view layout behaves
    differently enough to surprise a caller who did not ask for one. Within a
    kind, an already-booted device wins, since booting a cold simulator costs
    tens of seconds.
    """
    ranked = sorted(
        devices,
        key=lambda d: (
            not d.ready,
            d.kind != "simulator",
            not _is_phone(d),
            d.state not in ("Booted", "connected"),
        ),
    )
    chosen = ranked[0]
    if not chosen.ready:
        raise DeviceUnavailable(
            f"The only available device ({chosen.name}) is not ready",
            hint="; ".join(chosen.blockers) or "Run ios_doctor for details.",
        )
    return chosen
