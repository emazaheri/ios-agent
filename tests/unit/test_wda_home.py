"""Where the WebDriverAgent build is looked for, wherever the server is started.

Every lookup used to be `vendor/wda` relative to the working directory, so
`uvx ios-mcp`, the way the MCP registry starts the server, found nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ios_mcp.config import Settings
from ios_mcp.devices import wda_home as home_module
from ios_mcp.devices.doctor import _discover_xctestrun
from ios_mcp.devices.wda_home import PREPARE_SCRIPT, REPO_HOME, USER_HOME, wda_home


def test_a_clone_keeps_its_vendor_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vendor" / "wda").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert wda_home(Settings()) == REPO_HOME


def test_an_installed_copy_uses_the_user_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert wda_home(Settings()) == USER_HOME


def test_the_setting_wins_over_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "vendor" / "wda").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    cfg = Settings()
    cfg.wda.home = tmp_path / "elsewhere"

    assert wda_home(cfg) == tmp_path / "elsewhere"


def test_a_build_is_found_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure itself: a built runner, and a server started somewhere else."""
    built = tmp_path / "wda" / "DerivedData" / "Build" / "Products"
    built.mkdir(parents=True)
    bundle = built / "WebDriverAgentRunner_iphonesimulator27.0-arm64.xctestrun"
    bundle.write_text("")
    monkeypatch.setattr(home_module, "USER_HOME", tmp_path / "wda")
    monkeypatch.chdir(tmp_path / "wda" / "DerivedData")  # anywhere but a clone

    assert _discover_xctestrun(Settings()) == bundle


def test_the_build_script_ships_inside_the_package() -> None:
    """The wheel packages ios_mcp only; a script outside it never reached users."""
    assert PREPARE_SCRIPT.is_file()
    assert Path(home_module.__file__).parent in PREPARE_SCRIPT.parents
    assert "WDA_HOME" in PREPARE_SCRIPT.read_text()


def test_the_device_runner_is_the_one_at_the_top_of_the_home(tmp_path: Path) -> None:
    """DerivedData holds an unsigned simulator build of the same name.

    Searching the whole tree could report that one, and with it a phone that
    had a signed runner as having none.
    """
    from ios_mcp.devices.doctor import _discover_wda_app

    cfg = Settings()
    cfg.wda.home = tmp_path
    sim_build = (
        tmp_path / "DerivedData" / "Debug-iphonesimulator" / "WebDriverAgentRunner-Runner.app"
    )
    sim_build.mkdir(parents=True)
    assert _discover_wda_app(cfg) is None

    device = tmp_path / "WebDriverAgentRunner-Runner.app"
    device.mkdir()
    assert _discover_wda_app(cfg) == device


def test_a_simulator_build_leaves_the_device_runner_alone() -> None:
    """Read from the script: only a device build may write the top-level app."""
    script = PREPARE_SCRIPT.read_text()
    simulator_exit = script.index('if [ "$TARGET" = "simulator" ]; then\n  XCTESTRUN=')
    copy = script.index('ditto "$RUNNER" "$DEST"')
    assert simulator_exit < copy, "a simulator build reaches the copy over the device runner"
