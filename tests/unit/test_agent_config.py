"""Provider selection, and keeping one vendor's parameters out of another's.

The interesting failures here are silent ones. Sending `effort` to OpenAI is
an error, and sending `temperature` to Claude Opus 5 is a 400, so a settings
object that emits everything it knows about would break on whichever provider
it was not written for. These assert that each parameter is sent only where it
belongs.
"""

from __future__ import annotations

import pytest
from ios_agent.config import KNOWN_EXTRAS, AgentSettings


def test_the_default_is_claude_but_the_loop_does_not_require_it() -> None:
    """Anthropic is a default, not an assumption.

    It is what this project's numbers were measured on, which is a reason to
    default to it and not a reason to hardcode it.
    """
    cfg = AgentSettings()
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-opus-5"
    assert cfg.describe() == "anthropic:claude-opus-5 effort=medium"


def test_effort_is_sent_only_to_anthropic() -> None:
    """`output_config` is an Anthropic concept and an error anywhere else."""
    assert AgentSettings().chat_kwargs()["output_config"] == {"effort": "medium"}

    for provider in ("openai", "google_genai", "ollama"):
        kwargs = AgentSettings(provider=provider, model="whatever").chat_kwargs()
        assert "output_config" not in kwargs, f"{provider} was sent Anthropic's effort parameter"


def test_temperature_is_omitted_unless_it_is_set() -> None:
    """Claude Opus 5 rejects `temperature` with a 400, so it cannot default.

    On providers that accept it, setting it explicitly still works. The rule
    is only that it is never sent implicitly.
    """
    assert "temperature" not in AgentSettings().chat_kwargs()

    warm = AgentSettings(provider="openai", model="gpt-5.5", temperature=0.7)
    assert warm.chat_kwargs()["temperature"] == 0.7
    assert "temperature=0.7" in warm.describe()


def test_effort_can_be_turned_off() -> None:
    """Not every Anthropic model takes an effort level."""
    assert "output_config" not in AgentSettings(effort=None).chat_kwargs()


def test_extra_wins_over_everything_this_class_decided() -> None:
    """The escape hatch that keeps a new provider from needing a code change."""
    cfg = AgentSettings(
        provider="openai",
        model="gpt-5.5",
        max_tokens=100,
        extra={"max_tokens": 4096, "reasoning_effort": "high"},
    )
    kwargs = cfg.chat_kwargs()
    assert kwargs["max_tokens"] == 4096
    assert kwargs["reasoning_effort"] == "high"


def test_the_provider_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching vendor is configuration, not an edit."""
    monkeypatch.setenv("IOS_AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("IOS_AGENT_MODEL", "llama3.3")
    monkeypatch.setenv("IOS_AGENT_MAX_STEPS", "8")

    cfg = AgentSettings()

    assert cfg.describe() == "ollama:llama3.3"
    assert cfg.max_steps == 8
    assert "output_config" not in cfg.chat_kwargs()


@pytest.mark.parametrize("provider", sorted(KNOWN_EXTRAS))
def test_every_known_provider_names_a_real_extra(provider: str) -> None:
    """A hint pointing at an extra that does not exist is worse than none.

    Extra names normalise to hyphens in a built wheel, so the provider
    `google_genai` is installed by the extra `google-genai`.
    """
    import tomllib
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[2] / "agent" / "pyproject.toml"
    declared = tomllib.loads(manifest.read_text())["project"]["optional-dependencies"]
    normalised = {name.replace("_", "-") for name in declared}

    hint = AgentSettings(provider=provider, model="x").missing_package_hint()
    named = hint.split("--extra ")[1].split("`")[0]
    assert named in normalised, f"{provider} points at extra {named!r}, which is not declared"


def test_an_unknown_provider_still_gets_a_usable_message() -> None:
    """Not being in the list is not an error; init_chat_model may still know it."""
    hint = AgentSettings(provider="cohere", model="command-r").missing_package_hint()
    assert "cohere" in hint
    assert "--extra" not in hint
