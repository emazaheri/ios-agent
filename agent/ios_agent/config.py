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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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

#: Where each provider's credential is usually found. Deliberately incomplete,
#: and treated as a hint rather than a requirement.
#:
#: A missing variable is not proof of a missing credential. The Anthropic SDK
#: also accepts an auth token or an `ant auth login` profile, Bedrock and Vertex
#: use their cloud's own credential chain, and Ollama runs locally and needs
#: nothing at all. An empty tuple means "this project has no way to look", which
#: is different from "there is nothing there".
_CREDENTIAL_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "azure_openai": ("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "google_genai": ("GOOGLE_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistralai": ("MISTRAL_API_KEY",),
    "ollama": (),
    "bedrock_converse": (),
    "google_vertexai": (),
}


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


@dataclass(frozen=True, slots=True)
class ProviderProbe:
    """Whether the configured model can be reached, as far as can be told
    without spending a request.

    The status vocabulary matches `ios_mcp.devices.doctor.Check` so a caller
    can render both side by side, but the type is separate: `ios_mcp` must not
    import this package, and the model is not part of the device toolchain.
    """

    #: "ok" the model can be built and a credential appears to be in place.
    #: "warn" it builds, but nothing this project knows how to look for is set.
    #: "fail" it cannot be built at all.
    status: Literal["ok", "warn", "fail"]
    detail: str
    remedy: str | None = None

    @property
    def usable(self) -> bool:
        """Warnings are not refusals.

        A provider whose credential this project cannot see is the normal case
        for Bedrock, Vertex and an Anthropic CLI profile. Refusing to start on
        a heuristic would lock out every one of them.
        """
        return bool(self.status != "fail")


def probe_provider(settings: AgentSettings | None = None) -> ProviderProbe:
    """Ask whether a model turn could happen, without making one.

    Two questions, because the providers answer them differently. Building the
    model catches a missing integration package everywhere, and catches a
    missing credential on OpenAI, which raises at construction. Anthropic does
    not: it builds happily and raises on the first call, which is minutes later
    and after a device has been acquired. So the environment is checked too,
    and reported as a warning because it cannot be conclusive.

    No network. `init_chat_model` constructs a client; it does not use it.
    """
    cfg = settings or AgentSettings()
    export_provider_credentials()

    try:
        from langchain.chat_models import init_chat_model

        init_chat_model(model=cfg.model, model_provider=cfg.provider, **cfg.chat_kwargs())
    except ImportError as exc:
        return ProviderProbe("fail", f"{cfg.describe()}: {exc}", cfg.missing_package_hint())
    except Exception as exc:
        # Most often a missing credential the provider checks eagerly, but it
        # can also be a model name the provider does not know. The message is
        # the provider's own, which says which.
        return ProviderProbe("fail", f"{cfg.describe()}: {exc}", _credential_hint(cfg.provider))

    known = _CREDENTIAL_VARS.get(cfg.provider)
    if known and not any(os.environ.get(name) for name in known):
        return ProviderProbe(
            "warn",
            f"{cfg.describe()} builds, but none of {', '.join(known)} is set",
            _credential_hint(cfg.provider),
        )
    return ProviderProbe("ok", cfg.describe())


def _credential_hint(provider: str) -> str:
    known = _CREDENTIAL_VARS.get(provider)
    if not known:
        return (
            f"{provider} resolves its own credentials; check that provider's sign-in and try again"
        )
    return f"set {known[0]} in the environment or in .env"
