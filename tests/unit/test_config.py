"""Configuration layering."""

from __future__ import annotations

from pathlib import Path

from ios_mcp.config import Settings


def test_defaults_are_sane() -> None:
    s = Settings()
    assert s.snapshot.max_depth == 30
    assert s.digest.token_budget >= 200
    assert s.policy.enabled is True
    assert s.policy.confirm_destructive is True


def test_toml_file_overrides_defaults(tmp_path: Path) -> None:
    cfg = tmp_path / "ios-mcp.toml"
    cfg.write_text(
        """
        log_level = "DEBUG"

        [snapshot]
        max_depth = 12

        [policy]
        enabled = false
        """
    )
    s = Settings.load(cfg)
    assert s.log_level == "DEBUG"
    assert s.snapshot.max_depth == 12
    assert s.policy.enabled is False
    # untouched values keep their defaults
    assert s.digest.token_budget == 1500


def test_env_overrides_toml(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "ios-mcp.toml"
    cfg.write_text("[snapshot]\nmax_depth = 12\n")
    monkeypatch.setenv("IOS_MCP_SNAPSHOT__MAX_DEPTH", "44")
    s = Settings.load(cfg)
    assert s.snapshot.max_depth == 44
