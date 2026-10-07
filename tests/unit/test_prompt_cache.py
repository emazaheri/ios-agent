"""Prompt caching: asked for only where it means something, and seen where it happens.

Two halves, and the second matters more than the first. Asking Anthropic to
cache is a parameter that must not leak to any other provider, the same rule
`effort` follows. Seeing a cache hit is what makes the request worth anything:
LangChain counts cached tokens inside `input_tokens`, so a harness that reads
only the total prices a cached token like any other, and caching would save
money on the bill while every recorded figure said it saved nothing.

No network anywhere. `ChatAnthropic` builds its request payload without
sending it, which is the shape the API would receive.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from ios_agent.config import AgentSettings
from ios_agent.graph import opening_messages
from ios_agent.loop import cache_breakpoint, run_goal
from langchain.messages import AIMessage, AnyMessage, HumanMessage
from measure import RunResult
from screens import DeviceModel, build_session
from scripted_model import ScriptedModel

from ios_mcp.config import Settings

_EPHEMERAL = {"type": "ephemeral"}


def test_cache_control_is_bound_only_for_anthropic() -> None:
    assert AgentSettings().bind_kwargs() == {"cache_control": _EPHEMERAL}

    for provider in ("openai", "google_genai", "ollama", "bedrock_converse"):
        cfg = AgentSettings(provider=provider, model="whatever")
        assert cfg.bind_kwargs() == {}, f"{provider} was sent Anthropic's cache_control"
        assert not cfg.caches_prompt


def test_prompt_caching_can_be_turned_off() -> None:
    cfg = AgentSettings(prompt_cache=False)
    assert cfg.bind_kwargs() == {}
    assert "prompt_cache" not in cfg.describe()


def test_a_report_names_whether_the_prompt_was_cached() -> None:
    """The two arms of ADR 0022 must not share a model string."""
    assert AgentSettings().describe() == "anthropic:claude-opus-5 effort=medium prompt_cache=True"
    assert "prompt_cache" not in AgentSettings(provider="openai", model="gpt-5.5").describe()


def test_cache_control_is_not_a_constructor_argument() -> None:
    """It is a per-request field. Built into the client, it would not be sent."""
    assert "cache_control" not in AgentSettings().chat_kwargs()


def test_the_breakpoint_marks_the_system_prompt_and_nothing_else() -> None:
    messages = opening_messages("You drive a phone.", "Turn on Bold Text.")

    marked = cache_breakpoint(messages)

    assert marked[0].content == [
        {"type": "text", "text": "You drive a phone.", "cache_control": _EPHEMERAL}
    ]
    assert marked[1:] == messages[1:]
    # A copy: the graph's state keeps the plain prompt, so the transcript it
    # hands any provider is unchanged.
    assert messages[0].content == "You drive a phone."


def test_a_transcript_without_a_system_prompt_is_left_alone() -> None:
    messages: list[AnyMessage] = [HumanMessage(content="hi")]
    assert cache_breakpoint(messages) is messages
    assert cache_breakpoint([]) == []


def test_anthropic_receives_both_breakpoints() -> None:
    """What reaches the wire: a marked system block and the top-level field."""
    from langchain_anthropic import ChatAnthropic

    cfg = AgentSettings()
    chat = ChatAnthropic(model=cfg.model, api_key="sk-test", **cfg.chat_kwargs())  # type: ignore[arg-type]
    messages = cache_breakpoint(opening_messages("You drive a phone.", "Turn on Bold Text."))

    payload = chat._get_request_payload(messages, **cfg.bind_kwargs())

    assert payload["system"] == [
        {"type": "text", "text": "You drive a phone.", "cache_control": _EPHEMERAL}
    ]
    assert payload["cache_control"] == _EPHEMERAL


def _settings() -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    return cfg


class _CachingModel(ScriptedModel):
    """A scripted model whose every reply reports a cache hit and a cache write."""

    def __call__(self, tools: list[object]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        inner = super().__call__(tools)

        async def call(messages: list[AnyMessage]) -> AIMessage:
            reply = await inner(messages)
            reply.usage_metadata = {
                "input_tokens": 1000,
                "output_tokens": 10,
                "total_tokens": 1010,
                "input_token_details": {"cache_read": 700, "cache_creation": 200},
            }
            return reply

        return call


async def test_a_run_reports_what_its_provider_cached() -> None:
    session, _, _ = build_session(DeviceModel(), _settings())
    scripted = _CachingModel(
        [
            [("observe", {})],
            [("done", {"succeeded": True, "summary": "looked"})],
        ]
    )

    outcome = await run_goal(session, "Look at the screen.", model=scripted)

    assert outcome.turns == 2
    # The total is unchanged by caching; the cache counts split it.
    assert outcome.prompt_tokens == 2000
    assert outcome.cache_read_tokens == 1400
    assert outcome.cache_write_tokens == 400


async def test_a_provider_that_says_nothing_about_caching_reads_as_none() -> None:
    session, _, _ = build_session(DeviceModel(), _settings())

    class _Silent(ScriptedModel):
        def __call__(self, tools: list[object]) -> Any:
            inner = super().__call__(tools)

            async def call(messages: list[AnyMessage]) -> AIMessage:
                reply = await inner(messages)
                reply.usage_metadata = {
                    "input_tokens": 500,
                    "output_tokens": 5,
                    "total_tokens": 505,
                    "input_token_details": {"cache_read": None},  # type: ignore[typeddict-item]
                }
                return reply

            return call

    outcome = await run_goal(
        session, "Look.", model=_Silent([[("done", {"succeeded": True, "summary": "ok"})]])
    )

    assert outcome.prompt_tokens == 500
    assert outcome.cache_read_tokens == outcome.cache_write_tokens == 0


def _result(**tokens: int) -> RunResult:
    return RunResult(
        task="t",
        passed=True,
        observations=1,
        finds=0,
        actions=0,
        device_tokens=0,
        replans=0,
        refusals=0,
        seconds=0.0,
        floor=1,
        **tokens,  # type: ignore[arg-type]
    )


def test_a_cached_token_is_priced_at_the_cache_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IOS_AGENT_USD_PER_MTOK_IN", "4")
    monkeypatch.setenv("IOS_AGENT_USD_PER_MTOK_OUT", "20")

    plain = _result(prompt_tokens=1_000_000, completion_tokens=0)
    cached = _result(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_read_tokens=600_000,
        cache_write_tokens=200_000,
    )

    assert plain.usd == pytest.approx(4.0)
    # 200k uncached at $4, 600k read at $0.40, 200k written at $5.
    assert cached.usd == pytest.approx(0.8 + 0.24 + 1.0)
    assert cached.to_dict()["model_tokens"]["cache_read"] == 600_000
    # The same run priced as if nothing had been cached: ADR 0022's baseline.
    assert cached.usd_uncached == pytest.approx(plain.usd)


def test_cache_prices_can_be_set_for_another_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IOS_AGENT_USD_PER_MTOK_IN", "4")
    monkeypatch.setenv("IOS_AGENT_USD_PER_MTOK_CACHE_READ", "1")
    monkeypatch.setenv("IOS_AGENT_USD_PER_MTOK_CACHE_WRITE", "4")

    assert AgentSettings(_env_file=None).cache_prices == (1.0, 4.0)  # type: ignore[call-arg]
