"""Fixtures for tests that need a real simulator.

Everything here skips cleanly when the toolchain is not present, so the suite
stays runnable on a machine without Xcode (or on Linux).
"""

from __future__ import annotations

import pytest

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    cfg = Settings()
    # Real WebDriverAgent needs longer than the fakes do.
    cfg.wda.startup_timeout_s = 240.0
    cfg.stabilize.max_wait_s = 8.0
    # Automating a stock simulator is not risky, and prompts would hang a
    # non-interactive test run.
    cfg.policy.confirm_destructive = False
    return cfg


@pytest.fixture(scope="session")
async def pool(integration_settings: Settings):
    p = DevicePool(integration_settings)
    yield p
    await p.release_all()


@pytest.fixture
async def session(pool: DevicePool, integration_settings: Settings) -> IosSession:
    """A live session showing the Settings root screen.

    Terminating first is required: iOS 26 leaves Settings on whatever sub-pane
    it was last showing, even when asked to open `App-prefs:root`.
    """
    lease = await pool.acquire()
    ios = IosSession(lease, integration_settings)
    await ios.terminate_app("com.apple.Preferences")
    await ios.launch_app("com.apple.Preferences", fresh=True)
    await ios.observe()
    return ios
