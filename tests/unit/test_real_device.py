"""How the real-device adapter picks a route to WebDriverAgent.

Three routes exist and choosing wrong is not a graceful failure: the USB path
hangs for the full startup timeout when there is no cable, and the network path
needs Xcode that a go-ios-only host may not have.
"""

from __future__ import annotations

import pytest

from ios_mcp.config import Settings
from ios_mcp.devices.base import DeviceInfo
from ios_mcp.devices.real_device import RealDeviceAdapter
from ios_mcp.errors import DeviceNotReady, ToolchainMissing


def phone() -> DeviceInfo:
    return DeviceInfo(
        udid="00008150-TEST",
        name="Test iPhone",
        os_version="26.6",
        kind="device",
        state="connected",
        ready=True,
    )


def adapter(settings: Settings) -> RealDeviceAdapter:
    return RealDeviceAdapter(phone(), settings)


async def test_a_configured_runner_is_used_as_is(settings: Settings, monkeypatch) -> None:
    """wda.base_url points at something the caller manages."""
    settings.wda.base_url = "http://10.0.0.5:8100"
    device = adapter(settings)

    async def alive(_endpoint) -> bool:
        return True

    monkeypatch.setattr(device, "_runner_alive", alive)

    endpoint = await device.ensure_runner()

    assert endpoint.base_url == "http://10.0.0.5:8100"
    assert endpoint.started_by_us is False


async def test_a_configured_runner_that_is_not_answering_says_so(
    settings: Settings, monkeypatch
) -> None:
    settings.wda.base_url = "http://10.0.0.5:8100"
    device = adapter(settings)

    async def dead(_endpoint) -> bool:
        return False

    monkeypatch.setattr(device, "_runner_alive", dead)

    with pytest.raises(DeviceNotReady) as exc_info:
        await device.ensure_runner()
    assert "does not manage" in (exc_info.value.hint or "")


async def test_a_runner_we_did_not_start_is_not_torn_down(settings: Settings, monkeypatch) -> None:
    """Killing someone else's WebDriverAgent would be a nasty surprise."""
    settings.wda.base_url = "http://10.0.0.5:8100"
    device = adapter(settings)

    async def alive(_endpoint) -> bool:
        return True

    monkeypatch.setattr(device, "_runner_alive", alive)
    await device.ensure_runner()

    killed: list[str] = []
    monkeypatch.setattr(device, "_start_runner", lambda: killed.append("no"))

    await device.teardown()

    assert killed == []
    assert device._endpoint is None


async def test_usb_is_preferred_when_the_cable_is_attached(settings: Settings, monkeypatch) -> None:
    device = adapter(settings)
    chosen: list[str] = []

    async def wired() -> bool:
        return True

    async def over_usb():
        chosen.append("usb")
        from ios_mcp.devices.base import WdaEndpoint

        return WdaEndpoint(base_url="http://127.0.0.1:8100", port=8100, started_by_us=True)

    async def ready(_endpoint) -> None:
        return None

    monkeypatch.setattr(device, "_usb_attached", wired)
    monkeypatch.setattr(device, "_ensure_runner_over_usb", over_usb)
    monkeypatch.setattr(device, "_wait_for_runner", ready)

    await device.ensure_runner()

    assert chosen == ["usb"]


async def test_the_network_route_is_used_when_there_is_no_cable(
    settings: Settings, monkeypatch
) -> None:
    device = adapter(settings)
    chosen: list[str] = []

    async def unplugged() -> bool:
        return False

    async def over_network():
        chosen.append("network")
        from ios_mcp.devices.base import WdaEndpoint

        return WdaEndpoint(base_url="http://10.0.0.5:8100", port=0, started_by_us=True)

    async def ready(_endpoint) -> None:
        return None

    monkeypatch.setattr(device, "_usb_attached", unplugged)
    monkeypatch.setattr(device, "_ensure_runner_over_network", over_network)
    monkeypatch.setattr(device, "_wait_for_runner", ready)

    await device.ensure_runner()

    assert chosen == ["network"]


async def test_the_network_route_explains_that_it_needs_xcode(
    settings: Settings, monkeypatch
) -> None:
    """go-ios alone cannot reach a device that is not cabled."""
    import ios_mcp.devices.real_device as module

    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    device = adapter(settings)

    with pytest.raises(ToolchainMissing) as exc_info:
        await device._ensure_runner_over_network()
    assert "usbmuxd" in (exc_info.value.hint or "")


def test_the_announced_address_is_read_from_the_runner_log() -> None:
    """WDA prints the address it bound, which beats guessing the device's IP."""
    from ios_mcp.devices.real_device import _SERVER_URL

    line = "ServerURLHere->http://10.0.0.195:8100<-ServerURLHere"
    match = _SERVER_URL.search(line)
    assert match is not None
    assert match.group(1) == "http://10.0.0.195:8100"


def test_an_xcodebuild_failure_is_surfaced_not_swallowed(tmp_path) -> None:
    """A generic timeout hides the real reason the runner never came up."""
    from ios_mcp.devices.real_device import _xcodebuild_hint

    log = tmp_path / "wda.log"
    log.write_text(
        "Build settings from command line:\n"
        "/path/WebDriverAgent.xcodeproj: error: No profiles for 'com.x' were found\n"
    )
    assert "No profiles" in _xcodebuild_hint(log)


def test_a_missing_log_still_gives_advice(tmp_path) -> None:
    from ios_mcp.devices.real_device import _xcodebuild_hint

    assert "doctor" in _xcodebuild_hint(tmp_path / "absent.log")
