"""OpenTelemetry spans for a run, off unless asked for.

`IOS_AGENT_TRACING` picks one of three modes. `off`, the default, builds
nothing and imports nothing: the loop is handed `NULL_TRACER`, whose spans are
`contextlib.nullcontext`, so a run with tracing off executes the same code it
did before this module existed. `otel` exports over OTLP/HTTP to whatever the
standard `OTEL_EXPORTER_OTLP_*` variables name, which is how Phoenix, Langfuse
or a collector receive it. `langsmith` exports the same spans to LangSmith's
OTLP endpoint, authenticated from `LANGSMITH_API_KEY`.

## One tracer

There are two ways to get a LangChain agent into LangSmith, and using both on
the same calls records every model turn twice. LangChain's own callback tracer
(`LANGSMITH_TRACING`, with or without `LANGSMITH_OTEL_ENABLED`) is the one not
used, for a reason stronger than the duplicates: it exports the whole
transcript, every prompt and every screen, by a path that never passes through
the redactor. So while this module traces a run it switches that tracer off for
the run, and LangSmith receives exactly what Phoenix would.

The provider is private, never installed as the global one. Anything else in
the process that instruments itself through the global API stays wherever its
owner pointed it, and nothing here can pick up a span this module did not make.

## Redaction is applied when a span ends, not when an attribute is set

Every span is rewritten by `session.redactor` on its way to the exporter: its
name, every string attribute, every event and the status message. At the end
rather than at `set_attribute`, because a secret typed with `type_secret` is
only known to the redactor from that moment, and an attribute set before it,
such as the goal on the run span, would otherwise have gone out unscrubbed.

It fails closed. The redactor is bound per run in a context variable, and a
span that ends where none is bound is dropped rather than exported raw. The
redactor is the session's own, so the same secrets and the same configured
patterns that keep a value out of the transcript keep it out of a trace.

What is recorded is counts and names: tokens, cost, tool names and arguments,
resolution tiers, verdicts. Not prompts, not screens, not model replies. The
most reliable redaction is not exporting the text at all.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
    from opentelemetry.sdk.trace.export import SpanExporter

TraceMode = Literal["off", "otel", "langsmith"]

#: Turns a string into what may leave the process. Bound per run.
Scrub = Callable[[str], str]

_SCRUB: ContextVar[Scrub | None] = ContextVar("ios_agent_trace_scrub", default=None)

#: LangSmith's OTLP ingestion path, appended to `LANGSMITH_ENDPOINT`.
_LANGSMITH_OTLP_PATH = "/otel/v1/traces"
_LANGSMITH_DEFAULT_ENDPOINT = "https://api.smith.langchain.com"

#: How each backend is told what kind of span it is looking at. OpenTelemetry's
#: own `gen_ai.*` names carry the numbers and Langfuse reads those; Phoenix
#: and LangSmith each also want a kind in their own vocabulary to draw an LLM
#: or tool span as one. Three short strings per span, against three viewers
#: that otherwise render everything as a generic box.
_KINDS: dict[str, tuple[str, str]] = {
    # ours: (OpenInference, LangSmith)
    "agent": ("AGENT", "chain"),
    "chain": ("CHAIN", "chain"),
    "llm": ("LLM", "llm"),
    "tool": ("TOOL", "tool"),
}


class SpanHandle(Protocol):
    def set(self, key: str, value: Any) -> None: ...


class Tracer(Protocol):
    """What the loop and the graph call. Two implementations: off and on."""

    @property
    def enabled(self) -> bool: ...

    def span(
        self, name: str, kind: str, attributes: Mapping[str, Any] | None = None
    ) -> contextlib.AbstractContextManager[SpanHandle]: ...

    def bind(self, scrub: Scrub) -> contextlib.AbstractContextManager[None]: ...

    def trace_id(self) -> str | None: ...


class _NullSpan:
    def set(self, key: str, value: Any) -> None:
        return None


class _NullTracer:
    """Tracing off. Every method is a no-op, and nothing is imported."""

    enabled = False

    def span(
        self, name: str, kind: str, attributes: Mapping[str, Any] | None = None
    ) -> contextlib.AbstractContextManager[SpanHandle]:
        return contextlib.nullcontext(_NullSpan())

    def bind(self, scrub: Scrub) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def trace_id(self) -> str | None:
        return None


NULL_TRACER: Tracer = _NullTracer()


class _OtelSpan:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set(self, key: str, value: Any) -> None:
        cleaned = _attribute(value)
        if cleaned is not None:
            self._span.set_attribute(key, cleaned)


class OtelTracer:
    """Spans on a private provider, scrubbed by `_Redacting` as they end."""

    enabled = True

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self._tracer = provider.get_tracer("ios_agent")

    @contextlib.contextmanager
    def span(
        self, name: str, kind: str, attributes: Mapping[str, Any] | None = None
    ) -> Iterator[SpanHandle]:
        from langgraph.errors import GraphBubbleUp
        from opentelemetry import context as otel_context
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        openinference, langsmith = _KINDS[kind]
        span = self._tracer.start_span(name)
        handle = _OtelSpan(span)
        handle.set("openinference.span.kind", openinference)
        handle.set("langsmith.span.kind", langsmith)
        for key, value in (attributes or {}).items():
            handle.set(key, value)
        token = otel_context.attach(trace.set_span_in_context(span))
        try:
            yield handle
        except GraphBubbleUp:
            # An interrupt is the graph pausing to ask a human, not a failure.
            # The node re-runs on resume, so the replay appears as its own span.
            handle.set("ios_agent.interrupted", True)
            raise
        except BaseException as exc:
            # Recorded by hand rather than by `record_exception`, which would
            # attach a traceback nobody asked for. The message is scrubbed at
            # the end like everything else.
            handle.set("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            raise
        finally:
            otel_context.detach(token)
            span.end()

    @contextlib.contextmanager
    def bind(self, scrub: Scrub) -> Iterator[None]:
        """Scrub with `scrub`, and keep LangChain's own tracer out, for one run."""
        token = _SCRUB.set(scrub)
        try:
            with _langchain_tracing_off():
                yield
        finally:
            _SCRUB.reset(token)

    def trace_id(self) -> str | None:
        """The current trace, as the 32 hex digits every OTLP viewer searches by."""
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        return f"{context.trace_id:032x}" if context.is_valid else None


@contextlib.contextmanager
def _langchain_tracing_off() -> Iterator[None]:
    """No LangChain callback tracing inside a traced run, whatever the env says.

    `langsmith` is a dependency of `langchain-core`, so it is always present.
    An explicit `enabled=False` is checked before `LANGSMITH_TRACING`.
    """
    from langsmith.run_helpers import tracing_context

    with tracing_context(enabled=False):
        yield


def _attribute(value: Any) -> Any:
    """Something OpenTelemetry accepts as an attribute, or None to skip it."""
    if value is None:
        return None
    if isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Sequence) and all(isinstance(v, str) for v in value):
        return list(value)
    return json.dumps(value, default=str, sort_keys=True)


def _scrubbed_value(value: Any, scrub: Scrub) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(scrub(v) if isinstance(v, str) else v for v in value)
    return value


def _scrubbed_attributes(attributes: Mapping[str, Any] | None, scrub: Scrub) -> dict[str, Any]:
    return {key: _scrubbed_value(value, scrub) for key, value in (attributes or {}).items()}


def scrubbed(span: ReadableSpan, scrub: Scrub) -> ReadableSpan:
    """A copy of `span` with every piece of text it carries passed through `scrub`."""
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace import Status

    status = span.status
    if status.description:
        status = Status(status.status_code, scrub(status.description))
    return ReadableSpan(
        name=scrub(span.name),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_scrubbed_attributes(span.attributes, scrub),
        events=[
            Event(scrub(e.name), _scrubbed_attributes(e.attributes, scrub), e.timestamp)
            for e in span.events
        ],
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _redacting(inner: SpanProcessor) -> SpanProcessor:
    """Wrap `inner` so it only ever sees scrubbed spans.

    Built in a function so the SDK is imported only when tracing is on.
    """
    from opentelemetry.sdk.trace import SpanProcessor

    class _Redacting(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            inner.on_start(span, parent_context=parent_context)

        def on_end(self, span: ReadableSpan) -> None:
            scrub = _SCRUB.get()
            if scrub is None:
                # Fail closed. No redactor is bound, so nothing can say what
                # this span is allowed to carry.
                return
            inner.on_end(scrubbed(span, scrub))

        def shutdown(self) -> None:
            inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return inner.force_flush(timeout_millis)

    return _Redacting()


def build_tracer(mode: TraceMode, exporter: SpanExporter | None = None) -> Tracer:
    """A tracer for `mode`, exporting to `exporter` when one is given.

    `exporter` is for tests, which pass an in-memory one and read the spans
    back synchronously. Otherwise the exporter is OTLP over HTTP, batched on a
    background thread so a run never waits on the network.
    """
    if mode == "off":
        return NULL_TRACER
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError as exc:
        raise ImportError(
            f"IOS_AGENT_TRACING={mode} needs the tracing extra: `uv sync --extra tracing` "
            f"(provides opentelemetry-sdk and the OTLP exporter)"
        ) from exc

    # `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` are read by
    # `Resource.create`; a name passed here would outrank the former.
    named = {} if os.environ.get("OTEL_SERVICE_NAME") else {"service.name": "ios-agent"}
    provider = TracerProvider(resource=Resource.create(named))
    if exporter is not None:
        provider.add_span_processor(_redacting(SimpleSpanProcessor(exporter)))
    else:
        exporting = _reporting_once(_otlp_exporter(mode))
        provider.add_span_processor(_redacting(BatchSpanProcessor(exporting)))
    return OtelTracer(provider)


#: The exporter's own loggers. They warn on every retry and every failed batch.
_EXPORTER_LOGGERS = ("opentelemetry.exporter.otlp",)


def _reporting_once(inner: SpanExporter) -> SpanExporter:
    """Say once that traces are not arriving, instead of once per retry.

    An unreachable endpoint made the exporter log three lines every few
    seconds for the life of the process. Through the CLI's stderr handler
    those lines paint straight across the full-screen front end, and in a
    plain run they bury the agent's own output. A failed export is reported
    here, once, naming where it was going; the run is never affected either
    way, because export happens on a background thread.
    """
    import logging

    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    for name in _EXPORTER_LOGGERS:
        quiet = logging.getLogger(name)
        quiet.propagate = False
        # Without a handler of its own, a logger that does not propagate falls
        # back to `logging.lastResort`, which is stderr again.
        if not any(isinstance(h, logging.NullHandler) for h in quiet.handlers):
            quiet.addHandler(logging.NullHandler())
    log = logging.getLogger(__name__)
    where = getattr(inner, "_endpoint", "the configured endpoint")

    class _ReportingOnce(SpanExporter):
        warned = False

        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            try:
                result = inner.export(spans)
            except Exception as exc:
                self._warn(f"{type(exc).__name__}: {exc}")
                return SpanExportResult.FAILURE
            if result is SpanExportResult.FAILURE:
                self._warn("the endpoint did not accept them")
            return result

        def _warn(self, why: str) -> None:
            if not self.warned:
                self.warned = True
                log.warning("tracing: spans could not be exported to %s (%s)", where, why)

        def shutdown(self) -> None:
            inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return inner.force_flush(timeout_millis)

    return _ReportingOnce()


def _otlp_exporter(mode: TraceMode) -> SpanExporter:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    if mode == "otel":
        # Endpoint, headers, timeout and compression all come from the
        # standard `OTEL_EXPORTER_OTLP_*` variables, which the exporter reads
        # itself. Nothing here restates their defaults.
        return OTLPSpanExporter()
    key = os.environ.get("LANGSMITH_API_KEY")
    if not key:
        raise ValueError("IOS_AGENT_TRACING=langsmith needs LANGSMITH_API_KEY")
    base = os.environ.get("LANGSMITH_ENDPOINT", _LANGSMITH_DEFAULT_ENDPOINT).rstrip("/")
    headers = {"x-api-key": key}
    if project := os.environ.get("LANGSMITH_PROJECT"):
        headers["Langsmith-Project"] = project
    return OTLPSpanExporter(endpoint=base + _LANGSMITH_OTLP_PATH, headers=headers)


_cached: dict[TraceMode, Tracer] = {}


def tracer_for(mode: TraceMode) -> Tracer:
    """One provider per mode per process, built on first use.

    Cached because a provider owns a background export thread, and an eval
    suite runs forty goals in one process. The SDK flushes it at exit.
    """
    if mode not in _cached:
        _cached[mode] = build_tracer(mode)
    return _cached[mode]


def scrub_with(redactor: Any) -> Scrub:
    """The session's redactor as a plain function. `""` for a None it returns."""

    def scrub(text: str) -> str:
        return str(redactor.text(text) or "")

    return scrub
