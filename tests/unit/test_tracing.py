"""Tracing: what a run exports, and what it must never export.

Spans go to an in-memory exporter and are read back, so every assertion here
is about what would actually have left the process, after the redacting
processor has had its turn, rather than about what the code meant to send.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from ios_agent.backend import SessionBackend
from ios_agent.config import AgentSettings
from ios_agent.loop import run_goal
from ios_agent.tracing import NULL_TRACER, OtelTracer, build_tracer
from langchain.messages import AIMessage, AnyMessage
from screens import DeviceModel, build_session
from scripted_model import ScriptedModel

from ios_mcp.config import Settings

InMemorySpanExporter = pytest.importorskip(
    "opentelemetry.sdk.trace.export.in_memory_span_exporter"
).InMemorySpanExporter

#: Long, unusual and nowhere on any scripted screen, so finding it anywhere in
#: a span can only mean it leaked.
SECRET = "plantedSecret-7Q4xZ"

#: The same route `test_agent_loop` drives, ending in Bold Text switched on.
BOLD_TEXT = [
    [("observe", {})],
    [("tap", {"target": "Accessibility"})],
    [("tap", {"target": "Display & Text Size"})],
    [("set_value", {"value": "on", "target": "Bold Text"})],
    [("done", {"succeeded": True, "summary": "Bold Text is on"})],
]


def _settings() -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    cfg.policy.loop_detection_window = 50
    cfg.policy.confirm_destructive = False
    return cfg


class BilledModel(ScriptedModel):
    """A scripted model whose replies carry usage, as a real provider's do."""

    def __call__(self, tools: list[object]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        inner = super().__call__(tools)

        async def call(messages: list[AnyMessage]) -> AIMessage:
            reply = await inner(messages)
            reply.usage_metadata = {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}
            reply.response_metadata = {"model_name": "scripted-1"}
            return reply

        return call


def _agent_settings(**overrides: Any) -> AgentSettings:
    return AgentSettings(_env_file=None, usd_per_mtok_in=4.0, usd_per_mtok_out=20.0, **overrides)


def _traced() -> tuple[OtelTracer, Any]:
    exporter = InMemorySpanExporter()
    tracer = build_tracer("otel", exporter=exporter)
    assert isinstance(tracer, OtelTracer)
    return tracer, exporter


def _everything_in(span: Any) -> str:
    """Every piece of text a span would carry to a backend."""
    return json.dumps(
        {
            "name": span.name,
            "attributes": dict(span.attributes or {}),
            "events": [[e.name, dict(e.attributes or {})] for e in span.events],
            "status": span.status.description,
        },
        default=str,
    )


def _by_name(spans: Any, name: str) -> list[Any]:
    return [s for s in spans if s.name == name]


async def test_a_planted_secret_never_reaches_an_exported_span(monkeypatch) -> None:
    """Typed with `type_secret`, then handed back to every place a span reads.

    The goal, a tool argument, a failed tap's error and the model's own call
    all carry the secret. None of it may come out, and `[secret]` must, or the
    test would pass just as well if those attributes were never recorded.
    """
    monkeypatch.setenv("IOS_MCP_SECRET_PLANTED", SECRET)
    model = DeviceModel()
    session, fake, _ = build_session(model, _settings())
    await session.observe()
    await session.type_secret("planted", target="Search")
    assert SECRET in fake.typed(), "the device never received it, so this proves nothing"

    tracer, exporter = _traced()
    scripted = BilledModel(
        [
            [("tap", {"target": SECRET})],
            [("type_text", {"text": SECRET, "target": "Search"})],
            [("observe", {})],
            [("done", {"succeeded": False, "summary": SECRET})],
        ]
    )
    outcome = await run_goal(
        session,
        f"Find the setting called {SECRET}",
        model=scripted,
        backend=SessionBackend(session),
        settings=_agent_settings(),
        task_id=f"task-{SECRET}",
        tracer=tracer,
    )

    spans = exporter.get_finished_spans()
    assert outcome.trace_id is not None
    assert _by_name(spans, "ios_agent.run"), "no run span was exported at all"
    exported = "\n".join(_everything_in(s) for s in spans)
    assert SECRET not in exported
    assert "[secret]" in exported, "nothing carrying the secret was recorded"


async def test_every_run_node_tool_and_model_call_is_a_span() -> None:
    session, _, _ = build_session(DeviceModel(), _settings())
    tracer, exporter = _traced()

    outcome = await run_goal(
        session,
        "Turn on Bold Text",
        model=BilledModel(BOLD_TEXT),
        backend=SessionBackend(session),
        settings=_agent_settings(),
        task_id="enable_bold_text",
        tracer=tracer,
    )
    spans = exporter.get_finished_spans()

    (root,) = _by_name(spans, "ios_agent.run")
    assert root.parent is None
    assert {s.context.trace_id for s in spans} == {root.context.trace_id}
    assert outcome.trace_id == f"{root.context.trace_id:032x}"

    attrs = dict(root.attributes or {})
    assert attrs["ios_agent.task_id"] == "enable_bold_text"
    assert attrs["ios_agent.usage.input_tokens"] == outcome.prompt_tokens == 5000
    assert attrs["ios_agent.usage.output_tokens"] == outcome.completion_tokens == 250
    assert attrs["ios_agent.cost_usd"] == pytest.approx(5000 * 4e-6 + 250 * 20e-6)
    assert attrs["ios_agent.verified"] is True
    assert "gen_ai.usage.input_tokens" not in attrs, "a backend summing a trace counts it twice"

    # One per turn for the model node, and one for the tool node of every
    # turn but the last, which `done` ends without a trip back to the model.
    assert len(_by_name(spans, "agent")) == outcome.turns == 5
    assert len(_by_name(spans, "act")) == 5
    chats = _by_name(spans, "chat claude-opus-5")
    assert [dict(s.attributes or {})["ios_agent.step"] for s in chats] == [1, 2, 3, 4, 5]
    assert all(dict(s.attributes or {})["gen_ai.usage.input_tokens"] == 1000 for s in chats)
    assert all(
        s.parent.span_id in {a.context.span_id for a in _by_name(spans, "agent")} for s in chats
    )

    (switch,) = _by_name(spans, "execute_tool set_value")
    tool = dict(switch.attributes or {})
    assert tool["gen_ai.tool.name"] == "set_value"
    assert tool["ios_agent.step"] == 4
    assert tool["ios_agent.verifier.result"] == "progressed"
    assert tool["ios_agent.resolution.tier"]
    assert json.loads(str(tool["gen_ai.tool.call.arguments"])) == {
        "value": "on",
        "target": "Bold Text",
    }

    # `observe` records no outcome, so it must not inherit the previous one's.
    (look,) = _by_name(spans, "execute_tool observe")
    assert "ios_agent.verifier.result" not in dict(look.attributes or {})


async def test_tracing_changes_nothing_the_model_is_shown() -> None:
    """Off and on, the same script sees byte-identical transcripts.

    The cost of tracing has to be time only. If a span changed a single
    message the model reads, the token counts of every traced eval would
    be measuring the tracer.
    """

    async def once(tracer: Any) -> tuple[list[list[str]], Any]:
        session, _, _ = build_session(DeviceModel(), _settings())
        scripted = BilledModel(BOLD_TEXT)
        outcome = await run_goal(
            session,
            "Turn on Bold Text",
            model=scripted,
            backend=SessionBackend(session),
            settings=_agent_settings(),
            tracer=tracer,
        )
        seen = [[f"{m.type}:{m.content}" for m in turn] for turn in scripted.seen]
        return seen, outcome

    off_seen, off = await once(NULL_TRACER)
    on_seen, on = await once(_traced()[0])

    assert off_seen == on_seen
    assert (off.prompt_tokens, off.completion_tokens) == (on.prompt_tokens, on.completion_tokens)
    assert (off.steps, off.turns, off.stats) == (on.steps, on.turns, on.stats)
    assert off.trace_id is None and on.trace_id is not None


def test_tracing_is_off_unless_asked_for() -> None:
    assert AgentSettings(_env_file=None).tracing == "off"
    assert build_tracer("off") is NULL_TRACER


def test_a_span_ending_without_a_redactor_is_dropped() -> None:
    """Fail closed: nothing bound to scrub with means nothing exported."""
    tracer, exporter = _traced()
    with tracer.span("loose", "chain", {"note": SECRET}):
        pass
    assert exporter.get_finished_spans() == ()

    with (
        tracer.bind(lambda text: text.replace(SECRET, "[secret]")),
        tracer.span("bound", "chain", {"note": SECRET}),
    ):
        pass
    (span,) = exporter.get_finished_spans()
    assert dict(span.attributes or {})["note"] == "[secret]"


def test_langchain_tracing_is_off_inside_a_traced_run(monkeypatch) -> None:
    """One tracer. LangChain's own would duplicate every model call, unredacted."""
    from langsmith.utils import get_env_var, tracing_is_enabled

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    # `langsmith` caches every variable it reads, for the life of the process.
    get_env_var.cache_clear()
    try:
        tracer, _ = _traced()
        assert tracing_is_enabled() is True
        with tracer.bind(str):
            assert tracing_is_enabled() is False
        assert tracing_is_enabled() is True
    finally:
        monkeypatch.delenv("LANGSMITH_TRACING")
        get_env_var.cache_clear()


def test_langsmith_mode_needs_a_key(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(ValueError, match="LANGSMITH_API_KEY"):
        build_tracer("langsmith")


def test_the_base_install_runs_without_the_tracing_extra() -> None:
    """Without the SDK installed, off works and on names the extra to install.

    Checked in a fresh interpreter with the SDK and exporter made unimportable,
    which is what the base install looks like. `langsmith` imports the SDK
    itself whenever it is present, so a check on `sys.modules` in this
    environment would measure that and not this package.
    """
    probe = """
import sys
class Absent:
    def find_spec(self, name, path=None, target=None):
        if name.startswith(("opentelemetry.sdk", "opentelemetry.exporter")):
            raise ModuleNotFoundError(name)
sys.meta_path.insert(0, Absent())
from ios_agent.tracing import NULL_TRACER, build_tracer
import ios_agent.loop
assert build_tracer("off") is NULL_TRACER
try:
    build_tracer("otel")
except ImportError as exc:
    print(exc)
"""
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert "uv sync --extra tracing" in out


async def test_an_approval_pause_is_traced_as_a_pause_and_the_resume_as_a_run() -> None:
    """`interrupt()` raises through the tool span. That is not a failure.

    The node re-runs on resume, so the paused call appears twice: once marked
    interrupted, once completed. Neither may carry an error status, and the
    whole run stays one trace with one root.
    """
    from opentelemetry.trace import StatusCode

    model = DeviceModel(screen="general")
    settings = _settings()
    settings.policy.confirm_destructive = True
    session, fake, _ = build_session(model, settings)
    tracer, exporter = _traced()
    scripted = BilledModel(
        [
            [("tap", {"target": "Reset"}), ("tap", {"target": "Erase All Content and Settings"})],
            [("done", {"succeeded": True, "summary": "done"})],
        ]
    )

    async def yes(_request: dict[str, object]) -> bool:
        return True

    outcome = await run_goal(
        session,
        "Reset, then erase.",
        model=scripted,
        approve=yes,
        settings=_agent_settings(),
        tracer=tracer,
    )
    spans = exporter.get_finished_spans()

    assert outcome.approvals_asked == 1
    assert len(fake.taps()) == 2
    assert len(_by_name(spans, "ios_agent.run")) == 1
    assert len({s.context.trace_id for s in spans}) == 1
    erase = _by_name(spans, "execute_tool tap")
    interrupted = [s for s in erase if dict(s.attributes or {}).get("ios_agent.interrupted")]
    assert len(interrupted) == 1, "the pause was not marked on the call that raised it"
    assert all(s.status.status_code is not StatusCode.ERROR for s in spans)


async def test_a_crash_is_recorded_as_an_error_and_still_scrubbed(monkeypatch) -> None:
    """An exception's message is span text too, and can repeat a secret."""
    from opentelemetry.trace import StatusCode

    monkeypatch.setenv("IOS_MCP_SECRET_PLANTED", SECRET)
    session, _, _ = build_session(DeviceModel(), _settings())
    await session.observe()
    await session.type_secret("planted", target="Search")

    class Exploding(SessionBackend):
        async def tap(self, target: str, *, idem_key: str) -> str:
            raise RuntimeError(f"the driver fell over holding {SECRET}")

    tracer, exporter = _traced()
    with pytest.raises(RuntimeError):
        await run_goal(
            session,
            "Tap anything",
            model=BilledModel([[("tap", {"target": "General"})]]),
            backend=Exploding(session),
            settings=_agent_settings(),
            tracer=tracer,
        )
    spans = exporter.get_finished_spans()

    failed = [s for s in spans if s.status.status_code is StatusCode.ERROR]
    assert {s.name for s in failed} >= {"execute_tool tap", "act", "ios_agent.run"}
    assert all("[secret]" in (s.status.description or "") for s in failed)
    assert SECRET not in "\n".join(_everything_in(s) for s in spans)


async def test_two_runs_at_once_are_each_scrubbed_by_their_own_session(monkeypatch) -> None:
    """The redactor is bound per run, so concurrent runs must not share one.

    Each session knows only its own secret. A span scrubbed by the wrong
    session's redactor would carry the other secret out unchanged.
    """
    import asyncio

    first, second = "firstSecret-9KdQ", "secondSecret-3VmX"
    tracer, exporter = _traced()

    async def one(name: str, secret: str) -> str | None:
        monkeypatch.setenv(f"IOS_MCP_SECRET_{name}", secret)
        session, _, _ = build_session(DeviceModel(), _settings())
        await session.observe()
        await session.type_secret(name.lower(), target="Search")
        outcome = await run_goal(
            session,
            f"Look for {secret}",
            model=BilledModel(
                [[("observe", {})], [("done", {"succeeded": False, "summary": "x"})]]
            ),
            backend=SessionBackend(session),
            settings=_agent_settings(),
            tracer=tracer,
        )
        return outcome.trace_id

    ids = await asyncio.gather(one("FIRST", first), one("SECOND", second))
    spans = exporter.get_finished_spans()

    assert len(set(ids)) == 2
    assert len(_by_name(spans, "ios_agent.run")) == 2
    exported = "\n".join(_everything_in(s) for s in spans)
    assert first not in exported and second not in exported
    assert exported.count("Look for [secret]") == 2


def test_langsmith_mode_posts_otlp_to_langsmith_with_its_key(monkeypatch) -> None:
    """On the wire, against a stand-in for LangSmith's OTLP endpoint."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[tuple[str, dict[str, str], bytes]] = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Endpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key")
        monkeypatch.setenv("LANGSMITH_PROJECT", "ios-agent-tests")
        monkeypatch.setenv("LANGSMITH_ENDPOINT", f"http://127.0.0.1:{server.server_port}/")
        tracer = build_tracer("langsmith")
        assert isinstance(tracer, OtelTracer)
        with (
            tracer.bind(lambda text: text.replace(SECRET, "[secret]")),
            tracer.span("ios_agent.run", "agent", {"ios_agent.goal": f"find {SECRET}"}),
        ):
            pass
        assert tracer.provider.force_flush(5000)
        tracer.provider.shutdown()
    finally:
        server.shutdown()

    (path, headers, body) = received[0]
    assert path == "/otel/v1/traces"
    lowered = {k.lower(): v for k, v in headers.items()}
    assert lowered["x-api-key"] == "ls-test-key"
    assert lowered["langsmith-project"] == "ios-agent-tests"
    assert b"ios_agent.run" in body and b"[secret]" in body
    assert SECRET.encode() not in body


async def test_a_secret_learned_mid_run_is_scrubbed_from_what_was_set_before_it(
    monkeypatch,
) -> None:
    """Why redaction runs when a span ends, not when an attribute is set.

    The goal is on the run span from the first moment. The secret becomes
    known to the redactor only when it is typed, a turn later. Scrubbing at
    `set_attribute` would have let the goal out with the value in it.
    """
    monkeypatch.setenv("IOS_MCP_SECRET_PLANTED", SECRET)
    session, _, _ = build_session(DeviceModel(), _settings())
    tracer, exporter = _traced()
    inner = BilledModel([[("observe", {})], [("done", {"succeeded": True, "summary": ""})]])

    def typing_first(tools: list[object]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        call = inner(tools)

        async def turn(messages: list[AnyMessage]) -> AIMessage:
            if inner.turns == 0:
                await session.type_secret("planted", target="Search")
            return await call(messages)

        return turn

    await run_goal(
        session,
        f"Sign in with {SECRET}",
        model=typing_first,
        backend=SessionBackend(session),
        settings=_agent_settings(),
        tracer=tracer,
    )
    (root,) = _by_name(exporter.get_finished_spans(), "ios_agent.run")
    assert dict(root.attributes or {})["ios_agent.goal"] == "Sign in with [secret]"


def test_an_unreachable_endpoint_is_reported_once_not_per_retry() -> None:
    """A dead endpoint used to log three lines every few seconds, forever.

    Read off a real process's stderr, with the CLI's own `basicConfig`, which
    is where those lines landed: across the full-screen front end. In process,
    pytest's capture handlers sit on the exporter's logger and would see
    records that never reach stderr.
    """
    probe = """
import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
from ios_agent.tracing import _reporting_once
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

class Refusing(SpanExporter):
    _endpoint = "http://127.0.0.1:9/v1/traces"
    def export(self, spans):
        logging.getLogger("opentelemetry.exporter.otlp.proto.http").warning("retrying")
        return SpanExportResult.FAILURE

class Raising(SpanExporter):
    def export(self, spans):
        raise ConnectionError("refused")

refusing = _reporting_once(Refusing())
for _ in range(3):
    assert refusing.export([]) is SpanExportResult.FAILURE
assert _reporting_once(Raising()).export([]) is SpanExportResult.FAILURE
"""
    err = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stderr
    assert "retrying" not in err, "the exporter's own retry lines still reach stderr"
    assert err.count("127.0.0.1:9") == 1
    assert "ConnectionError: refused" in err


async def test_a_cached_prompt_is_priced_in_the_span_as_the_harness_prices_it() -> None:
    """A cache read is part of the input total, billed at its own rate."""

    class Cached(BilledModel):
        def __call__(
            self, tools: list[object]
        ) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
            inner = super().__call__(tools)

            async def call(messages: list[AnyMessage]) -> AIMessage:
                reply = await inner(messages)
                reply.usage_metadata = {
                    "input_tokens": 1000,
                    "output_tokens": 50,
                    "total_tokens": 1050,
                    "input_token_details": {"cache_read": 800},
                }
                return reply

            return call

    session, _, _ = build_session(DeviceModel(), _settings())
    tracer, exporter = _traced()
    outcome = await run_goal(
        session,
        "Turn on Bold Text",
        model=Cached(BOLD_TEXT),
        backend=SessionBackend(session),
        settings=_agent_settings(usd_per_mtok_cache_read=0.4),
        tracer=tracer,
    )
    spans = exporter.get_finished_spans()

    chat = dict(_by_name(spans, "chat claude-opus-5")[0].attributes or {})
    assert chat["gen_ai.usage.cache_read.input_tokens"] == 800
    assert chat["ios_agent.cost_usd"] == pytest.approx((200 * 4 + 800 * 0.4 + 50 * 20) / 1e6)
    (root,) = _by_name(spans, "ios_agent.run")
    attrs = dict(root.attributes or {})
    assert attrs["ios_agent.usage.cache_read_tokens"] == outcome.cache_read_tokens == 4000
    assert attrs["ios_agent.cost_usd"] == pytest.approx(5 * chat["ios_agent.cost_usd"])
