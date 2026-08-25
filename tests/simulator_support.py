"""Detects whether a real iOS Simulator runtime is usable on this machine.

Kept out of any conftest so both the integration and eval suites can import it
without the module-name collision that two conftests would create.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest


def simulator_available() -> bool:
    """True when Xcode is present *and* an iOS runtime is installed.

    Xcode 26 ships without any simulator runtime, so the presence of simctl
    alone is not enough to conclude anything can be booted.
    """
    if shutil.which("xcrun") is None:
        return False
    try:
        listing = subprocess.run(
            ["xcrun", "simctl", "list", "runtimes", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if listing.returncode != 0:
        return False
    try:
        runtimes = json.loads(listing.stdout).get("runtimes", [])
    except ValueError:
        return False
    return any(r.get("isAvailable") and "iOS" in r.get("name", "") for r in runtimes)


requires_simulator = pytest.mark.skipif(
    not simulator_available(),
    reason="no iOS simulator runtime installed (run: xcodebuild -downloadPlatform iOS)",
)
