"""How the real-device adapter picks a route to WebDriverAgent.

Three routes exist and choosing wrong is not a graceful failure: the USB path
hangs for the full startup timeout when there is no cable, and the network path
needs Xcode that a go-ios-only host may not have.
"""

from __future__ import annotations

import asyncio

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


# -- a tunnel without sudo, and a launch without a fixed wait ---------------


class _FakeProc:
    #: Above macOS's highest pid, so a group signal the reaper sends to it can
    #: only fail, never reach a real process.
    pid = 999_999

    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _with_goios(settings: Settings, **goios) -> Settings:
    return settings.model_copy(update={"goios": settings.goios.model_copy(update=goios)})


async def _tunnel_argv(settings: Settings, monkeypatch, *, comes_up: bool) -> list[str]:
    import ios_mcp.devices.real_device as module

    launched: list[str] = []
    calls = {"n": 0}

    async def tunnel_for(_cfg, _udid):
        calls["n"] += 1
        return {"udid": _udid} if comes_up and launched else None

    async def spawn(*argv, **_kw):
        launched.extend(argv)
        return _FakeProc(None if comes_up else 1)

    monkeypatch.setattr(module, "tunnel_for", tunnel_for)
    monkeypatch.setattr(module, "spawn_guarded", spawn)
    monkeypatch.setattr(module, "which", lambda _name: "/usr/local/bin/ios")
    await RealDeviceAdapter(phone(), settings)._require_tunnel()
    return launched


async def test_a_missing_tunnel_is_started_in_userspace_without_sudo(
    settings: Settings, monkeypatch
) -> None:
    """The default now, because it needs no root: 10 of 10 launches on a phone."""
    argv = await _tunnel_argv(settings, monkeypatch, comes_up=True)
    assert argv[1:] == ["tunnel", "start", "--userspace"]
    assert "sudo" not in argv


async def test_the_kernel_tunnel_still_asks_for_sudo(settings: Settings, monkeypatch) -> None:
    argv = await _tunnel_argv(
        _with_goios(settings, tunnel_mode="kernel"), monkeypatch, comes_up=True
    )
    assert argv[0] == "sudo"
    assert "--userspace" not in argv


async def test_a_tunnel_that_dies_is_reported_without_waiting_it_out(
    settings: Settings, monkeypatch
) -> None:
    from ios_mcp.errors import TunnelDown

    with pytest.raises(TunnelDown) as exc_info:
        await asyncio.wait_for(_tunnel_argv(settings, monkeypatch, comes_up=False), timeout=5)
    assert "--userspace" in (exc_info.value.hint or "")


def _runner_app(tmp_path, bundle_id: str):
    import plistlib

    app = tmp_path / "WebDriverAgentRunner-Runner.app"
    app.mkdir()
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))
    return app


def _with_wda(settings: Settings, **wda) -> Settings:
    return settings.model_copy(update={"wda": settings.wda.model_copy(update=wda)})


def test_the_runner_bundle_id_is_read_from_the_build_when_left_at_the_default(
    settings: Settings, tmp_path
) -> None:
    """The Wi-Fi route never read it, so a setup used over Wi-Fi could leave it
    at the default and fail every launch the first time the phone was cabled."""
    app = _runner_app(tmp_path, "com.someone.WebDriverAgentRunner.xctrunner")
    device = RealDeviceAdapter(phone(), _with_wda(settings, runner_app_path=app))
    assert device._runner_bundle_id() == "com.someone.WebDriverAgentRunner.xctrunner"


def test_a_configured_runner_bundle_id_wins_over_the_build(settings: Settings, tmp_path) -> None:
    app = _runner_app(tmp_path, "com.someone.WebDriverAgentRunner.xctrunner")
    cfg = _with_wda(settings, runner_app_path=app, bundle_id="com.chosen.Runner.xctrunner")
    assert RealDeviceAdapter(phone(), cfg)._runner_bundle_id() == "com.chosen.Runner.xctrunner"


async def test_a_runner_that_exits_fails_the_wait_at_once(
    settings: Settings, monkeypatch, tmp_path
) -> None:
    """The fixed three-second sleep that used to catch this is gone, so the
    wait has to notice instead of running out the startup timeout."""
    from ios_mcp.devices.base import WdaEndpoint

    device = adapter(settings)
    log = tmp_path / "wda.log"
    log.write_text('{"level":"ERROR","msg":"app is not trusted"}')
    device._log_path = log
    device._runner_proc = _FakeProc(1)  # type: ignore[assignment]

    async def dead(_endpoint) -> bool:
        return False

    monkeypatch.setattr(device, "_runner_alive", dead)
    usb = WdaEndpoint(base_url="http://127.0.0.1:8101", port=8101, started_by_us=True)

    with pytest.raises(DeviceNotReady) as exc_info:
        await asyncio.wait_for(device._wait_for_runner(usb), timeout=5)
    assert "VPN & Device Management" in (exc_info.value.hint or "")
    assert "not trusted" in str(exc_info.value.details)


async def test_teardown_stops_the_tunnel_it_started(settings: Settings) -> None:
    device = adapter(settings)
    tunnel = _FakeProc(None)
    device._tunnel_proc = tunnel  # type: ignore[assignment]
    await device.teardown()
    assert tunnel.returncode is not None
