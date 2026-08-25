"""Device selection and lease management."""

from __future__ import annotations

import pytest

from ios_mcp.config import Settings
from ios_mcp.devices.base import DeviceInfo
from ios_mcp.devices.pool import DevicePool, _best_default
from ios_mcp.devices.ports import free_port, release_port
from ios_mcp.errors import DeviceUnavailable


def sim(name: str, state: str = "Shutdown", udid: str | None = None) -> DeviceInfo:
    return DeviceInfo(
        udid=udid or f"UDID-{name.replace(' ', '-')}",
        name=name,
        os_version="18.2",
        kind="simulator",
        state=state,
        ready=True,
    )


def phone(name: str = "Ehsan's iPhone", ready: bool = True) -> DeviceInfo:
    return DeviceInfo(
        udid="00008120-REAL",
        name=name,
        os_version="18.5",
        kind="device",
        state="connected",
        ready=ready,
        blockers=() if ready else ("iOS 17+ requires a running tunnel",),
    )


def test_default_prefers_an_already_booted_simulator() -> None:
    """Booting a cold simulator costs tens of seconds."""
    devices = [sim("iPhone 15"), sim("iPhone 16", state="Booted"), sim("iPad Pro")]
    assert _best_default(devices).name == "iPhone 16"


def test_default_prefers_a_simulator_over_a_real_phone() -> None:
    """Acting on someone's real phone should be a deliberate choice."""
    assert _best_default([phone(), sim("iPhone 16")]).kind == "simulator"


def test_default_refuses_a_device_that_is_not_ready() -> None:
    with pytest.raises(DeviceUnavailable) as exc_info:
        _best_default([phone(ready=False)])
    assert "tunnel" in (exc_info.value.hint or "")


async def test_resolve_matches_udid_exact_name_and_substring(
    settings: Settings, monkeypatch
) -> None:
    devices = [sim("iPhone 16 Pro"), sim("iPad Pro 11-inch")]

    async def fake_list(_: Settings) -> list[DeviceInfo]:
        return devices

    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    pool = DevicePool(settings)

    assert (await pool.resolve("UDID-iPhone-16-Pro")).name == "iPhone 16 Pro"
    assert (await pool.resolve("iPhone 16 Pro")).name == "iPhone 16 Pro"
    assert (await pool.resolve("ipad")).name == "iPad Pro 11-inch"


async def test_resolve_disambiguates_by_booted_state(settings: Settings, monkeypatch) -> None:
    devices = [sim("iPhone 16"), sim("iPhone 16 Pro", state="Booted")]

    async def fake_list(_: Settings) -> list[DeviceInfo]:
        return devices

    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    assert (await DevicePool(settings).resolve("iphone 16")).name == "iPhone 16 Pro"


async def test_ambiguous_match_lists_the_candidates(settings: Settings, monkeypatch) -> None:
    devices = [sim("iPhone 16"), sim("iPhone 16 Plus"), sim("iPhone 16 Pro")]

    async def fake_list(_: Settings) -> list[DeviceInfo]:
        return devices

    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    with pytest.raises(DeviceUnavailable) as exc_info:
        await DevicePool(settings).resolve("iphone 16")
    assert "3 devices match" in exc_info.value.message
    assert "iPhone 16 Plus" in (exc_info.value.hint or "")


async def test_unknown_device_points_at_the_listing_tool(settings: Settings, monkeypatch) -> None:
    async def fake_list(_: Settings) -> list[DeviceInfo]:
        return [sim("iPhone 16")]

    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    with pytest.raises(DeviceUnavailable) as exc_info:
        await DevicePool(settings).resolve("Pixel 9")
    assert "ios_list_devices" in (exc_info.value.hint or "")


async def test_empty_estate_points_at_the_doctor(settings: Settings, monkeypatch) -> None:
    async def fake_list(_: Settings) -> list[DeviceInfo]:
        return []

    monkeypatch.setattr("ios_mcp.devices.pool.list_devices", fake_list)
    with pytest.raises(DeviceUnavailable) as exc_info:
        await DevicePool(settings).resolve(None)
    assert "ios_doctor" in (exc_info.value.hint or "")


def test_port_allocation_never_hands_out_the_same_port_twice() -> None:
    a = free_port(18100, 18110)
    b = free_port(18100, 18110)
    try:
        assert a != b
    finally:
        release_port(a)
        release_port(b)


def test_released_ports_are_reusable() -> None:
    a = free_port(18200, 18200)
    release_port(a)
    b = free_port(18200, 18200)
    release_port(b)
    assert a == b


def test_exhausted_port_range_fails_with_a_remedy() -> None:
    taken = free_port(18300, 18300)
    try:
        with pytest.raises(Exception) as exc_info:
            free_port(18300, 18300)
        assert "port_range" in str(exc_info.value)
    finally:
        release_port(taken)


# -- Wi-Fi devices ----------------------------------------------------------


async def test_a_network_device_is_discovered_when_goios_is_blind(
    settings: Settings, monkeypatch
) -> None:
    """go-ios speaks to usbmuxd, so an unplugged phone is invisible to it.

    Without CoreDevice in the mix, a perfectly drivable device on Wi-Fi simply
    does not appear.
    """
    from ios_mcp.devices import discovery
    from ios_mcp.devices.devicectl import DevicectlDevice

    async def no_goios(_: Settings) -> list[DeviceInfo]:
        return []

    async def one_network_device() -> list[DevicectlDevice]:
        return [
            DevicectlDevice(
                udid="UDID-WIFI",
                name="Ehsan's iPhone",
                os_version="26.6",
                model="iPhone18,2",
                transport="network",
                paired=True,
                hostnames=("phone.local",),
            )
        ]

    monkeypatch.setattr(discovery, "_from_goios", no_goios)
    monkeypatch.setattr(discovery.devicectl, "list_devices", one_network_device)

    devices = await discovery.list_real_devices(settings)

    assert len(devices) == 1
    assert devices[0].udid == "UDID-WIFI"
    assert devices[0].ready, "a paired network device is drivable"
    assert any("network" in b for b in devices[0].blockers), "the route should be explained"


async def test_goios_wins_when_both_sources_see_a_device(settings: Settings, monkeypatch) -> None:
    """If go-ios can see it, USB is available, which is the faster route."""
    from ios_mcp.devices import discovery
    from ios_mcp.devices.devicectl import DevicectlDevice

    async def goios(_: Settings) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                udid="SHARED",
                name="Ehsan's iPhone",
                os_version="26.6",
                kind="device",
                state="connected",
                ready=True,
            )
        ]

    async def core() -> list[DevicectlDevice]:
        return [
            DevicectlDevice(
                udid="SHARED",
                name="Ehsan's iPhone",
                os_version="26.6",
                model=None,
                transport="network",
                paired=True,
                hostnames=(),
            )
        ]

    monkeypatch.setattr(discovery, "_from_goios", goios)
    monkeypatch.setattr(discovery.devicectl, "list_devices", core)

    devices = await discovery.list_real_devices(settings)

    assert len(devices) == 1, "the same phone must not appear twice"
    assert not devices[0].blockers, "the USB entry has no network caveat"


async def test_an_unpaired_device_is_not_ready(settings: Settings, monkeypatch) -> None:
    from ios_mcp.devices import discovery
    from ios_mcp.devices.devicectl import DevicectlDevice

    async def no_goios(_: Settings) -> list[DeviceInfo]:
        return []

    async def unpaired() -> list[DevicectlDevice]:
        return [
            DevicectlDevice(
                udid="U",
                name="Someone's iPhone",
                os_version="26.6",
                model=None,
                transport="network",
                paired=False,
                hostnames=(),
            )
        ]

    monkeypatch.setattr(discovery, "_from_goios", no_goios)
    monkeypatch.setattr(discovery.devicectl, "list_devices", unpaired)

    devices = await discovery.list_real_devices(settings)
    assert not devices[0].ready
    assert any("Trust" in b for b in devices[0].blockers)
