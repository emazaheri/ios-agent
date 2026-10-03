"""Pure-function tests for device discovery parsing."""

from __future__ import annotations

import pytest

from ios_mcp.devices.discovery import _needs_tunnel, _runtime_to_version


@pytest.mark.parametrize(
    ("runtime", "expected"),
    [
        ("com.apple.CoreSimulator.SimRuntime.iOS-18-2", "18.2"),
        ("com.apple.CoreSimulator.SimRuntime.iOS-17-0", "17.0"),
        ("com.apple.CoreSimulator.SimRuntime.iOS-26-1", "26.1"),
        ("com.apple.CoreSimulator.SimRuntime.watchOS-11-0", None),
        ("com.apple.CoreSimulator.SimRuntime.tvOS-18-0", None),
        ("com.apple.CoreSimulator.SimRuntime.xrOS-2-0", None),
        ("garbage", None),
    ],
)
def test_runtime_to_version(runtime: str, expected: str | None) -> None:
    assert _runtime_to_version(runtime) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("17.0", True),
        ("18.5", True),
        ("26.1", True),
        ("16.7", False),
        ("unknown", False),
        ("", False),
    ],
)
def test_needs_tunnel(version: str, expected: bool) -> None:
    assert _needs_tunnel(version) is expected


# -- a cabled phone and its tunnel ------------------------------------------


async def _cabled_phone(monkeypatch, *, auto_start: bool, tunnel: bool):
    from types import SimpleNamespace

    import ios_mcp.devices.discovery as module
    from ios_mcp.config import Settings

    async def probe(_binary, *args, **_kw):
        if args[0] == "list":
            return SimpleNamespace(ok=True, json=lambda: {"deviceList": ["PHONE-1"]})
        info = {"DeviceName": "Phone", "ProductVersion": "26.6", "ProductType": "iPhone18,2"}
        return SimpleNamespace(ok=True, json=lambda: info)

    async def tunnel_for(_cfg, udid):
        return {"udid": udid} if tunnel else None

    monkeypatch.setattr(module, "which", lambda _name: "/usr/local/bin/ios")
    monkeypatch.setattr(module, "probe", probe)
    monkeypatch.setattr(module, "tunnel_for", tunnel_for)
    cfg = Settings()
    cfg.goios.auto_start_tunnel = auto_start
    (device,) = await module._from_goios(cfg)
    return device


async def test_a_cabled_phone_is_ready_when_a_tunnel_will_be_started(monkeypatch) -> None:
    """Every cabled iOS 17+ phone used to be marked blocked, tunnel or not."""
    device = await _cabled_phone(monkeypatch, auto_start=True, tunnel=False)
    assert device.ready


async def test_a_cabled_phone_is_ready_when_a_tunnel_is_already_up(monkeypatch) -> None:
    device = await _cabled_phone(monkeypatch, auto_start=False, tunnel=True)
    assert device.ready


async def test_a_cabled_phone_is_blocked_only_when_nothing_will_tunnel(monkeypatch) -> None:
    device = await _cabled_phone(monkeypatch, auto_start=False, tunnel=False)
    assert not device.ready
    assert any("--userspace" in blocker for blocker in device.blockers)
