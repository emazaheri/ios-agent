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
