"""Where the WebDriverAgent build lives, and the script that makes it.

Every lookup used to read `vendor/wda` relative to the working directory, and
the build script lived in the repository's `scripts/`, which the wheel does not
ship. A clone worked; `uvx ios-mcp`, which is how the MCP registry entry starts
the server, could neither find a runner nor build one.

The order below keeps a clone exactly as it was and gives an installed copy a
home that does not depend on where a client happens to start it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ios_mcp.config import Settings

#: A clone's build, where `scripts/prepare_wda.sh` has always put it.
REPO_HOME = Path("vendor/wda")

#: An installed copy's build. macOS only, like everything that drives iOS.
USER_HOME = Path.home() / "Library" / "Application Support" / "ios-mcp" / "wda"

#: Shipped inside the package so an installed copy can build its own runner.
PREPARE_SCRIPT = Path(__file__).with_name("prepare_wda.sh")


def wda_home(settings: Settings) -> Path:
    """The directory holding the WebDriverAgent checkout and its builds.

    `IOS_MCP_WDA__HOME` when set; else a clone's `vendor/wda` when the server
    runs from one; else the per-user directory.
    """
    if settings.wda.home is not None:
        return settings.wda.home.expanduser()
    if REPO_HOME.is_dir():
        return REPO_HOME
    return USER_HOME


def prepare_command(target: str) -> str:
    """What to tell a person to run, for remedies and hints."""
    return f"ios-mcp prepare-wda {target}"


def run_prepare(settings: Settings, target: str) -> int:
    """Build WebDriverAgent for `target` into `wda_home`. Returns the exit code.

    Runs in the foreground with the caller's terminal, because a device build
    can ask for keychain access, and the person has to see that prompt.
    """
    home = wda_home(settings).resolve()
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "WDA_HOME": str(home)}
    return subprocess.run(["bash", str(PREPARE_SCRIPT), target], env=env, check=False).returncode
