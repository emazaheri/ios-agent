"""Doctor report aggregation and provisioning-profile parsing."""

from __future__ import annotations

import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ios_mcp.devices.doctor import Check, DoctorReport, _provisioning_expiry


def _report(**statuses: str) -> DoctorReport:
    return DoctorReport(checks=[Check(name, st, "") for name, st in statuses.items()])


def test_simulator_readiness_needs_xcode_simctl_and_a_wda_build() -> None:
    """Xcode alone is not enough: WebDriverAgent has to exist to drive anything."""
    ready = {"xcode": "ok", "simctl": "ok", "wda-bundle": "ok"}
    assert _report(**ready).can_use_simulator
    assert not _report(**{**ready, "xcode": "fail"}).can_use_simulator
    assert not _report(**{**ready, "simctl": "fail"}).can_use_simulator
    assert not _report(**{**ready, "wda-bundle": "warn"}).can_use_simulator


def test_real_device_readiness_requires_goios_and_an_attached_device() -> None:
    assert _report(**{"go-ios": "ok", "devices": "ok"}).can_use_real_device
    # go-ios installed but nothing plugged in is not "ready"
    assert not _report(**{"go-ios": "ok", "devices": "warn"}).can_use_real_device
    assert not _report(**{"go-ios": "warn", "devices": "skip"}).can_use_real_device


def test_missing_check_is_treated_as_failing() -> None:
    assert not _report(python="ok").can_use_simulator


def test_summary_counts_and_capability() -> None:
    report = _report(
        xcode="ok",
        simctl="ok",
        python="ok",
        **{"wda-bundle": "ok"},
        tunnel="warn",
        devices="fail",
    )
    assert "4 ok" in report.summary
    assert "1 warning" in report.summary
    assert "1 blocking" in report.summary
    assert "simulator" in report.summary


def test_render_shows_remedy_only_for_problems() -> None:
    report = DoctorReport(
        checks=[
            Check("good", "ok", "fine", remedy="never shown"),
            Check("bad", "fail", "broken", remedy="fix it like this"),
        ]
    )
    rendered = report.render()
    assert "fix it like this" in rendered
    assert "never shown" not in rendered


def test_provisioning_expiry_parses_cms_wrapped_plist(tmp_path: Path) -> None:
    expiry = datetime(2026, 12, 1, 12, 0, 0, tzinfo=UTC)
    payload = plistlib.dumps({"ExpirationDate": expiry.replace(tzinfo=None), "Name": "test"})
    # Real .mobileprovision files wrap the plist in CMS binary noise.
    blob = b"\x30\x82\x0b\xde\x06\x09" + payload + b"\x00\x01\x02trailing"
    profile = tmp_path / "embedded.mobileprovision"
    profile.write_bytes(blob)

    parsed = _provisioning_expiry(profile)
    assert parsed is not None
    assert parsed.year == 2026 and parsed.month == 12 and parsed.day == 1
    assert parsed.tzinfo is not None


def test_provisioning_expiry_returns_none_on_garbage(tmp_path: Path) -> None:
    profile = tmp_path / "embedded.mobileprovision"
    profile.write_bytes(b"not a plist at all")
    assert _provisioning_expiry(profile) is None


def test_provisioning_expiry_detects_expired_profile(tmp_path: Path) -> None:
    past = datetime.now(UTC) - timedelta(days=3)
    payload = plistlib.dumps({"ExpirationDate": past.replace(tzinfo=None)})
    profile = tmp_path / "embedded.mobileprovision"
    profile.write_bytes(b"\x30\x82" + payload)
    parsed = _provisioning_expiry(profile)
    assert parsed is not None
    assert (parsed - datetime.now(UTC)).days < 0
