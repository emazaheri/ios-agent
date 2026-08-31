"""Fixtures for the front-end tests.

The helper *module* these tests import from is `tui_harness`, not this file:
a module named `conftest` cannot be imported by name, because the root
`tests/conftest.py` already owns that name. This file is fixtures only.
"""

from __future__ import annotations

import pytest

from ios_mcp.devices.doctor import Check, DoctorReport

#: A machine with everything installed. What most of these tests assume, and
#: what they should not have to say out loud.
HEALTHY = [
    Check("host", "ok", "macOS 26.6 on arm64"),
    Check("python", "ok", "Python 3.12.5"),
    Check("xcode", "ok", "Xcode 26.6"),
    Check("simctl", "ok", "xcrun simctl available"),
    Check("wda-bundle", "ok", "simulator bundle ready", data={"xctestrun": "/fake.xctestrun"}),
    Check("simulators", "ok", "11 simulator(s) on iOS 26.5"),
    Check("devices", "ok", "1 device(s)"),
]


@pytest.fixture(autouse=True)
def a_healthy_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the startup toolchain check without shelling out.

    The app runs `run_doctor` before acquiring a device, which is right in
    production and wrong in a unit suite: it costs about a second per test and
    makes the result depend on whether the machine running the tests happens to
    have Xcode. The checks themselves are tested against fabricated reports in
    `test_toolchain.py`, where the interesting cases are the broken ones that
    cannot be arranged on a working Mac.
    """

    async def healthy(_settings: object = None) -> DoctorReport:
        return DoctorReport(checks=list(HEALTHY))

    monkeypatch.setattr("ios_mcp.devices.doctor.run_doctor", healthy)
