"""Prompt caching, as the meter sees it.

LangChain counts cached tokens inside `input_tokens`, so a harness that reads
only the total prices a cached token like any other. OpenAI caches a repeated
prefix without being asked, so every figure recorded against it may overstate
what the run cost. These assert that a cache hit is counted and priced as one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from ios_agent.config import AgentSettings
from ios_agent.loop import run_goal
from langchain.messages import AIMessage, AnyMessage
from measure import RunResult
from screens import DeviceModel, build_session
from scripted_model import ScriptedModel

from ios_mcp.config import Settings


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
