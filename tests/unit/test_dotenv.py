"""`.env` loading, and the hermeticity it threatens.

Enabling `.env` buys convenience and costs something real: a developer with a
local file can change what a test measures without touching the test. So this
covers both halves. That the file is read, that a real environment variable
still beats it, and that `_env_file=None` opts out, which is the seam every
test asserting a *code* default has to use.

`.env.example` is checked here too. A knob that exists and is not written down
is one nobody finds, and one written down under a name that does not exist is
worse, because it looks like it works.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from ios_agent import AgentSettings

from ios_mcp.config import Settings

_REPO = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO / ".env.example"


@pytest.fixture
def in_a_directory_with_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory holding a `.env`, without touching the real one.

    Re-enables the file that the root conftest switches off for every other
    test, since reading it is what this module exists to check.
    """
    for cls in (Settings, AgentSettings):
        monkeypatch.setitem(cls.model_config, "env_file", ".env")
    (tmp_path / ".env").write_text(
        "IOS_MCP_LOG_LEVEL=FROM_DOTENV\n"
        "IOS_MCP_DIGEST__TOKEN_BUDGET=999\n"
        "IOS_AGENT_PROVIDER=from_dotenv\n"
        "IOS_AGENT_MAX_STEPS=3\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_dotenv_is_read_by_both_packages(in_a_directory_with_dotenv: Path) -> None:
    """What `.gitignore` and the docstrings have always implied, now true.

    Before this was wired, `dotenv_settings` sat in the source chain with no
    `env_file` set, so it did nothing and the documented precedence was wrong.
    """
    assert Settings().log_level == "FROM_DOTENV"
    assert AgentSettings().provider == "from_dotenv"


def test_nested_settings_come_through_the_double_underscore(
    in_a_directory_with_dotenv: Path,
) -> None:
    assert Settings().digest.token_budget == 999
    assert AgentSettings().max_steps == 3


def test_a_real_environment_variable_beats_the_file(
    in_a_directory_with_dotenv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So CI, a shell export, or a process manager wins without editing a file."""
    monkeypatch.setenv("IOS_MCP_LOG_LEVEL", "FROM_SHELL")
    monkeypatch.setenv("IOS_AGENT_PROVIDER", "from_shell")

    assert Settings().log_level == "FROM_SHELL"
    assert AgentSettings().provider == "from_shell"


def test_env_file_none_ignores_the_file(in_a_directory_with_dotenv: Path) -> None:
    """The seam that keeps a default-asserting test honest.

    Without it, a developer whose `.env` sets `IOS_AGENT_PROVIDER=openai` would
    watch the test asserting the default is Anthropic fail, for a reason that
    has nothing to do with the code.
    """
    assert Settings(_env_file=None).log_level == "INFO"
    assert AgentSettings(_env_file=None).provider == "anthropic"


def test_a_toml_file_sits_below_the_dotenv(
    in_a_directory_with_dotenv: Path, tmp_path: Path
) -> None:
    """Documented precedence, asserted end to end.

    Highest first: a real environment variable, then `.env`, then TOML, then
    the defaults in code.
    """
    (tmp_path / "ios-mcp.toml").write_text('log_level = "FROM_TOML"\ndefault_device = "sim-1"\n')

    loaded = Settings.load()

    assert loaded.log_level == "FROM_DOTENV", "the TOML file outranked .env"
    assert loaded.default_device == "sim-1", "a key only TOML sets should still apply"


def test_the_example_file_is_committed_and_the_real_one_is_not() -> None:
    ignored = (_REPO / ".gitignore").read_text().splitlines()
    assert ".env" in ignored, "a .env holding real values must never be committable"
    assert _EXAMPLE.exists(), "without an example, the knobs are undiscoverable"


def test_every_documented_setting_actually_exists() -> None:
    """A knob written down under a name nothing reads looks like it works.

    Walks each `IOS_MCP_*` / `IOS_AGENT_*` key in the example, splits off the
    prefix and the `__` nesting, and checks the field is really there.
    """
    known = {"IOS_MCP_": Settings(_env_file=None), "IOS_AGENT_": AgentSettings(_env_file=None)}
    unknown: list[str] = []

    for raw in _EXAMPLE.read_text().splitlines():
        line = raw.lstrip("# ").strip()
        if "=" not in line or not line.startswith(("IOS_MCP_", "IOS_AGENT_")):
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith("IOS_MCP_SECRET_"):
            continue  # resolved by reference at call time, not a settings field
        prefix = "IOS_MCP_" if key.startswith("IOS_MCP_") else "IOS_AGENT_"
        if prefix == "IOS_AGENT_" and key.startswith("IOS_AGENT_USD_PER_MTOK"):
            continue  # read directly by the eval harness, not a settings field
        target: object = known[prefix]
        for part in key[len(prefix) :].lower().split("__"):
            # `hasattr`, not truthiness: `temperature`, `default_device` and
            # `wda.base_url` all default to None and are perfectly real.
            if not hasattr(target, part):
                unknown.append(key)
                break
            target = getattr(target, part)

    assert unknown == [], f".env.example documents settings that do not exist: {unknown}"


def test_the_example_does_not_ship_a_real_secret() -> None:
    """Committed, so anything with a value in it is public."""
    for raw in _EXAMPLE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        # Suffix rather than substring: IOS_AGENT_MAX_TOKENS is a budget, not
        # a credential, and a substring match would flag it.
        sensitive = name.endswith(("_KEY", "_TOKEN", "_PASSWORD")) or "SECRET" in name
        if sensitive:
            assert value == "", f"{name} is uncommented with a value in a committed file"


def test_the_example_leaves_defaults_alone() -> None:
    """Copying it unedited must not change behaviour.

    An example that silently loosens a safety default would be worse than no
    example at all.
    """
    values = dict(
        line.split("=", 1)
        for line in _EXAMPLE.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["IOS_MCP_POLICY__ENABLED"] == "true"
    assert values["IOS_MCP_POLICY__CONFIRM_DESTRUCTIVE"] == "true"
    assert os.environ.get("IOS_MCP_POLICY__CONFIRM_DESTRUCTIVE") is None
