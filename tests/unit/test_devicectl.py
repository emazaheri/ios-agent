"""CoreDevice parsing, which is how a Wi-Fi device is discovered at all."""

from __future__ import annotations

import pytest

from ios_mcp.devices.devicectl import _parse, _transport


def entry(**overrides) -> dict:
    base = {
        "hardwareProperties": {
            "udid": "00008150-0011648C2150C01C",
            "productType": "iPhone18,2",
            "platform": "iOS",
        },
        "deviceProperties": {"name": "Ehsan's iPhone", "osVersionNumber": "26.6"},
        "connectionProperties": {
            "transportType": "localNetwork",
            "pairingState": "paired",
            "localHostnames": ["Ehsans-iPhone.coredevice.local"],
        },
    }
    for section, values in overrides.items():
        base.setdefault(section, {}).update(values)
    return base


def test_a_network_device_is_parsed_and_marked_as_such() -> None:
    device = _parse(entry())
    assert device is not None
    assert device.name == "Ehsan's iPhone"
    assert device.os_version == "26.6"
    assert device.transport == "network"
    assert device.is_wired is False
    assert device.paired is True


def test_a_cabled_device_is_marked_wired() -> None:
    device = _parse(entry(connectionProperties={"transportType": "wired"}))
    assert device is not None
    assert device.is_wired is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("wired", "wired"),
        ("usb", "wired"),
        ("localNetwork", "network"),
        ("network", "network"),
        (None, "unknown"),
        ("something-new", "unknown"),
    ],
)
def test_transport_is_normalised(raw: str | None, expected: str) -> None:
    assert _transport(raw) == expected


def test_an_unpaired_device_is_reported_as_such() -> None:
    """Pairing is what a device needs before anything can drive it."""
    device = _parse(entry(connectionProperties={"pairingState": "unpaired"}))
    assert device is not None
    assert device.paired is False


def test_entries_without_a_udid_are_skipped() -> None:
    assert _parse(entry(hardwareProperties={"udid": None})) is None


def test_non_ios_devices_are_skipped() -> None:
    """Paired watches and Macs appear in the same listing."""
    assert _parse(entry(hardwareProperties={"platform": "watchOS"})) is None
    assert _parse(entry(hardwareProperties={"platform": "macOS"})) is None


def test_ipados_counts_as_drivable() -> None:
    assert _parse(entry(hardwareProperties={"platform": "iPadOS"})) is not None


def test_a_missing_name_falls_back_to_the_udid() -> None:
    device = _parse(entry(deviceProperties={"name": None}))
    assert device is not None
    assert device.name == "00008150"
