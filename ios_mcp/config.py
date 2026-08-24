"""Configuration for the iOS automation server.

Layered: defaults -> TOML file (``ios-mcp.toml`` or ``$IOS_MCP_CONFIG``) ->
environment variables prefixed ``IOS_MCP_``. Nested settings use ``__`` as the
delimiter, e.g. ``IOS_MCP_SNAPSHOT__MAX_DEPTH=30``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class SnapshotSettings(BaseModel):
    """WDA session settings that govern accessibility snapshot cost.

    These are the main levers that make snapshots fast enough for an agent
    loop. They are configuration rather than constants because the right values
    differ wildly between a stock Apple app and a deeply nested React Native
    view hierarchy.
    """

    max_depth: int = Field(default=30, ge=1, le=100)
    max_children: int = Field(default=64, ge=1)
    custom_snapshot_timeout_s: float = Field(default=5.0, ge=0.0)
    wait_for_idle_timeout_s: float = Field(default=2.0, ge=0.0)
    use_first_match: bool = True
    excluded_attributes: tuple[str, ...] = ("visible", "accessible", "frame")


class DigestSettings(BaseModel):
    """Budgets and thresholds for the UI Digest."""

    token_budget: int = Field(default=1500, ge=200)
    max_nodes: int = Field(default=120, ge=10)
    collapse_repeats_after: int = Field(default=3, ge=1)
    include_coordinates: bool = True
    min_element_size_px: int = Field(default=4, ge=0)


class StabilizeSettings(BaseModel):
    """Post-action settle loop."""

    min_delay_s: float = Field(default=0.15, ge=0.0)
    poll_interval_s: float = Field(default=0.2, gt=0.0)
    max_wait_s: float = Field(default=6.0, gt=0.0)
    stable_samples: int = Field(default=2, ge=1)


class PolicySettings(BaseModel):
    """Safety gate. Deliberately restrictive by default."""

    enabled: bool = True
    app_allowlist: tuple[str, ...] = ()
    app_blocklist: tuple[str, ...] = (
        "com.apple.Passbook",
        "com.apple.stocks",
        "com.apple.PassbookUIService",
    )
    confirm_destructive: bool = True
    destructive_labels: tuple[str, ...] = (
        "send",
        "pay",
        "buy",
        "purchase",
        "delete",
        "remove",
        "erase",
        "confirm",
        "transfer",
        "sign out",
        "log out",
        "subscribe",
        "order",
        "checkout",
        "block",
        "report",
    )
    max_consecutive_failures: int = Field(default=5, ge=1)
    loop_detection_window: int = Field(default=6, ge=2)
    redact_screenshots: bool = False
    redact_patterns: tuple[str, ...] = (
        r"\b\d{13,19}\b",  # card-like numbers
        r"\b[\w.+-]+@[\w-]+\.[\w.]+\b",  # emails
    )


class WdaSettings(BaseModel):
    host: str = "127.0.0.1"
    port_range: tuple[int, int] = (8100, 8199)
    connect_timeout_s: float = Field(default=10.0, gt=0.0)
    request_timeout_s: float = Field(default=60.0, gt=0.0)
    startup_timeout_s: float = Field(default=90.0, gt=0.0)
    bundle_id: str = "com.facebook.WebDriverAgentRunner.xctrunner"
    runner_app_path: Path | None = None
    auto_heal: bool = True


class GoIosSettings(BaseModel):
    binary: str = "ios"
    tunnel_api_host: str = "127.0.0.1"
    tunnel_api_port: int = 28100
    auto_start_tunnel: bool = False  # requires sudo; opt in explicitly


class ServerSettings(BaseModel):
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    auth_jwks_uri: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None


class TomlSource(PydanticBaseSettingsSource):
    """Reads a TOML file as a settings source ranked below the environment."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path | None) -> None:
        super().__init__(settings_cls)
        self.path = path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        raise NotImplementedError  # __call__ supplies the whole mapping at once

    def __call__(self) -> dict[str, Any]:
        if self.path is None or not self.path.is_file():
            return {}
        return tomllib.loads(self.path.read_text())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IOS_MCP_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Set by ``load``; consumed by ``settings_customise_sources``.
    _toml_path: ClassVar[Path | None] = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Precedence, highest first: init kwargs, env, .env, TOML file, defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlSource(settings_cls, cls._toml_path),
            file_secret_settings,
        )

    artifacts_dir: Path = Path(".artifacts")
    log_level: str = "INFO"
    default_device: str | None = None

    snapshot: SnapshotSettings = SnapshotSettings()
    digest: DigestSettings = DigestSettings()
    stabilize: StabilizeSettings = StabilizeSettings()
    policy: PolicySettings = PolicySettings()
    wda: WdaSettings = WdaSettings()
    goios: GoIosSettings = GoIosSettings()
    server: ServerSettings = ServerSettings()

    @classmethod
    def load(cls, config_path: Path | None = None) -> Settings:
        """Load settings, layering an optional TOML file *under* the environment."""
        previous = cls._toml_path
        cls._toml_path = config_path or _default_config_path()
        try:
            return cls()
        finally:
            cls._toml_path = previous


def _default_config_path() -> Path | None:
    import os

    env = os.environ.get("IOS_MCP_CONFIG")
    if env:
        return Path(env)
    local = Path("ios-mcp.toml")
    return local if local.is_file() else None


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def set_settings(settings: Settings) -> None:
    """Override the singleton. Used by the CLI and by tests."""
    global _settings
    _settings = settings
