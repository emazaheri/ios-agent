"""Which model drives the phone, and on whose infrastructure.

Layered the same way `ios_mcp.config` is: defaults, then `.env`, then
environment variables prefixed `IOS_AGENT_`. A real environment variable always
beats a `.env` entry, and `_env_file=None` ignores the file entirely, which is
what a test asserting a code default wants. Kept in this package rather than
added to `Settings`,
because agent configuration in the server's settings object would put a
model-provider concern inside the distribution that is meant not to have one.

The one thing worth understanding here is that **provider-specific parameters
must not leak into the generic path.** `effort` is an Anthropic concept and
sending it elsewhere is an error; `temperature` is ordinary almost everywhere
and is rejected outright by Claude Opus 5. So neither is sent unconditionally,
and `extra` exists for whatever a provider wants that this class has never
heard of.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Providers whose package this project declares an extra for. Anything else
#: still works if its integration package is installed; this list only decides
#: how helpful the error message is when one is missing.
KNOWN_EXTRAS: dict[str, str] = {
    "anthropic": "langchain-anthropic",
    "openai": "langchain-openai",
    "azure_openai": "langchain-openai",
    "google_genai": "langchain-google-genai",
    "google_vertexai": "langchain-google-vertexai",
    "ollama": "langchain-ollama",
    "groq": "langchain-groq",
    "mistralai": "langchain-mistralai",
    "bedrock_converse": "langchain-aws",
}

#: Parameters only Anthropic understands, skipped for every other provider.
_ANTHROPIC_ONLY = ("effort",)


class AgentSettings(BaseSettings):
    """How to reach a model. Provider-neutral by construction."""

    model_config = SettingsConfigDict(
        env_prefix="IOS_AGENT_",
        env_nested_delimiter="__",
        extra="ignore",
        protected_namespaces=(),
        env_file=".env",
        env_file_encoding="utf-8",
    )

    #: Any provider `langchain.chat_models.init_chat_model` accepts.
    provider: str = "anthropic"
    #: Default is Claude Opus 5. Change both together: a model name means
    #: nothing without the provider that serves it.
    model: str = "claude-opus-5"

    #: Thinking and the reply share this budget on models that think, so it
    #: has to leave room for both.
    max_tokens: int = Field(default=16000, gt=0)

    #: Anthropic only. Sent as `output_config={"effort": ...}` and skipped
    #: entirely for other providers. On Claude Opus 5 this is the cost lever,
    #: because `temperature` is not available.
    effort: str | None = "medium"

    #: Left unset by default and not sent when unset. Claude Opus 5, Opus 4.8,
    #: Opus 4.7 and Sonnet 5 reject it with a 400, so it cannot be a default;
    #: on providers that accept it, setting it works normally.
    temperature: float | None = None

    #: Anything this class has not heard of, passed through untouched. The
    #: escape hatch that keeps a new provider from needing a code change.
    extra: dict[str, Any] = Field(default_factory=dict)

    #: A model that has taken this many turns without finishing has lost the
    #: thread. Recovering from that is a planning problem, not a budget one.
    max_steps: int = Field(default=24, gt=0)

    def chat_kwargs(self) -> dict[str, Any]:
        """The keyword arguments to hand the provider, and nothing more.

        `extra` is applied last so it can override anything decided here,
        including a parameter this class sent that a particular model does not
        want.
        """
        kwargs: dict[str, Any] = {"max_tokens": self.max_tokens}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.provider == "anthropic" and self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        kwargs.update(self.extra)
        return kwargs

    def describe(self) -> str:
        """A one-line identity for reports, so a number names its model."""
        parts = [f"{self.provider}:{self.model}"]
        for name in _ANTHROPIC_ONLY:
            value = getattr(self, name)
            if value and self.provider == "anthropic":
                parts.append(f"{name}={value}")
        if self.temperature is not None:
            parts.append(f"temperature={self.temperature}")
        return " ".join(parts)

    def missing_package_hint(self) -> str:
        """Name the extra that fixes it, not just the package.

        Extra names normalise to hyphens when a wheel is built, so
        `google_genai` the provider is `google-genai` the extra. Printing the
        provider name here would send someone to an extra that does not exist.
        """
        package = KNOWN_EXTRAS.get(self.provider)
        if package is None:
            return (
                f"provider {self.provider!r} has no extra in this project; install its "
                "LangChain integration package directly"
            )
        # Several providers share one package. Whichever extra pulls it in works.
        extra = next(name for name, pkg in KNOWN_EXTRAS.items() if pkg == package)
        return f"install it with `uv sync --extra {extra.replace('_', '-')}` (provides {package})"


#: Prefixes this project owns. These reach code through `Settings` and
#: `AgentSettings`, where the test suite can switch the file off, so exporting
#: them into the process environment would defeat that and make a local `.env`
#: able to change what a unit test measures.
_OWN_PREFIXES = ("IOS_MCP_", "IOS_AGENT_")


def export_provider_credentials(dotenv_path: str | Path = ".env") -> list[str]:
    """Put third-party credentials from `.env` where their SDKs will find them.

    pydantic-settings reads `.env` into a settings object, not into the
    process environment, so a key written there is invisible to the vendor SDK
    that actually needs it: `AgentSettings` would resolve `openai:gpt-5.6-sol`
    from the same file while the OpenAI client saw no key at all. That split is
    surprising enough to be worth closing rather than documenting.

    Only variables this project does not own are exported. `IOS_MCP_*` and
    `IOS_AGENT_*` keep going through the settings classes, so the suite's
    hermeticity fixture still holds.

    An existing environment variable is never overwritten, which keeps the
    precedence rule the same everywhere: a real variable beats the file.

    Returns the names it set, so a caller can say what it did without printing
    a secret.
    """
    path = Path(dotenv_path)
    if not path.is_file():
        return []

    exported: list[str] = []
    for name, value in dotenv_values(path).items():
        if value is None or name.startswith(_OWN_PREFIXES) or name in os.environ:
            continue
        os.environ[name] = value
        exported.append(name)
    return exported
