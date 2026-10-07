"""What tracing costs a run, measured so that only the tracer can differ.

A real model cannot answer this by being run twice. Its replies vary between
runs, so a traced run and an untraced one take different routes, and the
difference in their tokens and seconds is the model's variance, not the
tracer's. So each task is run once against the real model with tracing off and
every reply recorded, then replayed with tracing off and on, alternating.

The replay asserts the model is shown exactly what it was shown when recorded,
turn by turn. That is the claim tokens are identical, checked rather than
inferred: a replay that reached a different transcript would fail here, and its
token count would be a guess. What is left to differ is wall time, and the
device is the scripted one, so that difference is the tracer's.

The traced arm exports for real, over OTLP, to whatever
`OTEL_EXPORTER_OTLP_ENDPOINT` names: a local Phoenix when measured for ADR
0023. Batched on a background thread, which is what a run pays in production.

Writes `.artifacts/evals/tracing-overhead.json`. Costs one real run per task.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from statistics import median
from typing import Any

import pytest
from agent_driver import requires_a_model
from ios_agent import AgentSettings, SessionBackend, chat_model, run_goal
from ios_agent.tracing import NULL_TRACER, OtelTracer, Tracer, tracer_for
from langchain.messages import AIMessage, AnyMessage
from opentelemetry.sdk.trace import SpanProcessor
from screens import build_session
from tasks import TASKS, Task
from test_agent_evals import eval_settings

pytestmark = [pytest.mark.agent, pytest.mark.model, requires_a_model]

REPORT = Path(".artifacts/evals/tracing-overhead.json")

#: Replays per arm per task. The replay costs nothing but time.
REPLAYS = int(os.environ.get("IOS_AGENT_TRACING_REPLAYS", "5"))

Call = Callable[[list[AnyMessage]], Awaitable[AIMessage]]
Factory = Callable[[list[Any]], Call]
Turn = list[str]


def _shown(messages: list[AnyMessage]) -> Turn:
    """What one model call was given, as text that compares exactly."""
    return [
        json.dumps(
            {
                "type": m.type,
                "content": m.content,
                "tool_calls": getattr(m, "tool_calls", None),
                "tool_call_id": getattr(m, "tool_call_id", None),
            },
            sort_keys=True,
            default=str,
        )
        for m in messages
    ]


def _recording(factory: Factory, log: list[tuple[Turn, AIMessage]]) -> Factory:
    def bind(tools: list[Any]) -> Call:
        call = factory(tools)

        async def recorded(messages: list[AnyMessage]) -> AIMessage:
            reply = await call(messages)
            log.append((_shown(messages), reply.model_copy(deep=True)))
            return reply

        return recorded

    return bind


def _replaying(log: list[tuple[Turn, AIMessage]], drift: list[int]) -> Factory:
    def bind(_tools: list[Any]) -> Call:
        turns = iter(enumerate(log))

        async def replayed(messages: list[AnyMessage]) -> AIMessage:
            turn, (shown, reply) = next(turns)
            if _shown(messages) != shown:
                drift.append(turn)
            return reply.model_copy(deep=True)

        return replayed

    return bind


class _Counter(SpanProcessor):
    """Counts spans as they end, for spans per run. Exports nothing."""

    def __init__(self) -> None:
        self.ended = 0

    def on_end(self, span: Any) -> None:
        self.ended += 1


async def _run(task: Task, model: Factory, tracer: Tracer) -> tuple[Any, float]:
    device = task.model()
    session, _fake, _adapter = build_session(device, eval_settings(task))
    started = time.perf_counter()
    outcome = await run_goal(
        session,
        task.goal,
        model=model,
        backend=SessionBackend(session),
        max_steps=task.max_steps,
        task_id=task.name,
        tracer=tracer,
    )
    return outcome, time.perf_counter() - started


_rows: list[dict[str, Any]] = []
_counter = _Counter()


def _traced() -> Tracer:
    tracer = tracer_for("otel")
    assert isinstance(tracer, OtelTracer)
    if not getattr(tracer, "_counted", False):
        tracer.provider.add_span_processor(_counter)
        tracer._counted = True  # type: ignore[attr-defined]
    return tracer


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.name)
async def test_tracing_costs_time_and_nothing_else(task: Task) -> None:
    cfg = AgentSettings()
    log: list[tuple[Turn, AIMessage]] = []
    recorded, recorded_s = await _run(task, _recording(chat_model(cfg), log), NULL_TRACER)

    off_s: list[float] = []
    on_s: list[float] = []
    spans: list[int] = []
    drift: list[int] = []
    traced = _traced()
    for _ in range(REPLAYS):
        off, seconds = await _run(task, _replaying(log, drift), NULL_TRACER)
        off_s.append(seconds)
        before = _counter.ended
        on, seconds = await _run(task, _replaying(log, drift), traced)
        on_s.append(seconds)
        spans.append(_counter.ended - before)
        for replayed in (off, on):
            assert (replayed.prompt_tokens, replayed.completion_tokens) == (
                recorded.prompt_tokens,
                recorded.completion_tokens,
            )
            assert (replayed.turns, replayed.steps) == (recorded.turns, recorded.steps)
        assert off.trace_id is None and on.trace_id is not None

    assert not drift, f"{task.name}: replay showed the model a different transcript at {drift}"

    per_in, per_out = cfg.usd_per_mtok_in / 1e6, cfg.usd_per_mtok_out / 1e6
    row = {
        "task": task.name,
        "turns": recorded.turns,
        "prompt_tokens": recorded.prompt_tokens,
        "completion_tokens": recorded.completion_tokens,
        "tokens_per_step": round(
            (recorded.prompt_tokens + recorded.completion_tokens) / max(recorded.turns, 1)
        ),
        "usd": round(recorded.prompt_tokens * per_in + recorded.completion_tokens * per_out, 4),
        "recorded_seconds": round(recorded_s, 2),
        "spans_per_run": median(spans),
        "replay_ms_off": round(median(off_s) * 1000, 1),
        "replay_ms_on": round(median(on_s) * 1000, 1),
    }
    row["overhead_ms"] = round(row["replay_ms_on"] - row["replay_ms_off"], 1)
    _rows.append(row)
    print(f"\n{json.dumps(row)}")


def test_write_the_overhead_report() -> None:
    if not _rows:
        pytest.skip("no tasks ran")
    _traced().provider.force_flush()
    total = {
        key: round(sum(r[key] for r in _rows), 4)
        for key in ("turns", "prompt_tokens", "completion_tokens", "usd", "recorded_seconds")
    }
    total["spans"] = sum(r["spans_per_run"] for r in _rows)
    total["replay_ms_off"] = round(sum(r["replay_ms_off"] for r in _rows), 1)
    total["replay_ms_on"] = round(sum(r["replay_ms_on"] for r in _rows), 1)
    total["overhead_ms"] = round(total["replay_ms_on"] - total["replay_ms_off"], 1)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "model": AgentSettings().describe(),
                "replays": REPLAYS,
                "endpoint": os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
                "totals": total,
                "tasks": _rows,
            },
            indent=2,
        )
    )
    print(f"\nWrote {REPORT}: {json.dumps(total)}")
