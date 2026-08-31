"""Watching a `Backend` without changing it.

`EventBackend` wraps any `Backend` and emits around every verb. Three things
about how it does that are deliberate, and all three exist to serve one rule:
**a front end may not change what a run costs.**

- It **delegates** `stats` and `last_screen` rather than mirroring them, so
  `Outcome.stats` (built from `run.backend.stats` in `ios_agent.loop`) is the
  inner object and nothing is counted twice or drifts apart.
- It adds **no `await` of its own**. `emit` is synchronous, so wrapping
  introduces no scheduling point that the bare backend did not have.
- It never touches the device, asks the verifier anything, or reads a screen.
  Everything it reports is read back off the inner backend after the fact.

`tests/tui/test_cost.py` asserts the result by equality against an unwrapped
run rather than trusting any of the above.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ios_agent import AgentSettings, export_provider_credentials
from ios_agent.backend import Backend, BackendStats
from ios_agent.loop import ModelFactory
from langchain.messages import AIMessage, AnyMessage

from ios_tui.bus import EventSink
from ios_tui.events import (
    ActionFinished,
    ActionStarted,
    ApprovalAnswered,
    ModelDelta,
    ModelTurn,
    Observed,
    ScreenUpdated,
    StatsSnapshot,
)


def _describe(exc: BaseException) -> str:
    """The error, as short as it can be said.

    `IosAutomationError.message` rather than `str(exc)`, which prefixes the
    code and appends the hint: a row has one line, and the hint is a sentence
    telling you what to do instead. Whoever shows the row can show the hint
    beside it if there is room.
    """
    message = getattr(exc, "message", None)
    return str(message or exc) or type(exc).__name__


class EventBackend:
    """A `Backend` that says what it is doing."""

    def __init__(self, inner: Backend, sink: EventSink) -> None:
        self._inner = inner
        self._sink = sink

    # `Backend` declares `stats` and `last_screen` as variables rather than
    # read-only properties, so satisfying the protocol needs setters too: a
    # property without one is not assignable, and mypy rejects the class as an
    # implementation. They delegate rather than shadow, which is what keeps a
    # single set of counters in play.

    @property
    def stats(self) -> BackendStats:
        return self._inner.stats

    @stats.setter
    def stats(self, value: BackendStats) -> None:
        self._inner.stats = value

    @property
    def last_screen(self) -> str:
        return self._inner.last_screen

    @last_screen.setter
    def last_screen(self, value: str) -> None:
        self._inner.last_screen = value

    # -- perception --------------------------------------------------------

    async def observe(self) -> str:
        rendered = await self._inner.observe()
        self._sink.emit(Observed(rendered=rendered, stats=StatsSnapshot.of(self.stats)))
        self._sink.emit(ScreenUpdated(text=self.last_screen))
        return rendered

    # -- actions -----------------------------------------------------------

    async def tap(self, target: str, *, idem_key: str) -> str:
        return await self._wrap(
            "tap", {"target": target}, lambda: self._inner.tap(target, idem_key=idem_key)
        )

    async def type_text(self, text: str, target: str | None, *, idem_key: str) -> str:
        return await self._wrap(
            "type_text",
            {"text": text, "target": target or ""},
            lambda: self._inner.type_text(text, target, idem_key=idem_key),
        )

    async def set_value(self, value: str, target: str, *, idem_key: str) -> str:
        return await self._wrap(
            "set_value",
            {"value": value, "target": target},
            lambda: self._inner.set_value(value, target, idem_key=idem_key),
        )

    async def scroll(self, direction: str, until: str | None, *, idem_key: str) -> str:
        return await self._wrap(
            "scroll",
            {"direction": direction, "until": until or ""},
            lambda: self._inner.scroll(direction, until, idem_key=idem_key),
        )

    async def press_button(self, name: str, *, idem_key: str) -> str:
        return await self._wrap(
            "press_button",
            {"name": name},
            lambda: self._inner.press_button(name, idem_key=idem_key),
        )

    async def open_url(self, url: str) -> str:
        return await self._wrap("open_url", {"url": url}, lambda: self._inner.open_url(url))

    # -- approval and stopping ---------------------------------------------

    def approve(self, signature: str) -> None:
        self._inner.approve(signature)
        # Emitted here rather than where the question was asked, because this
        # is the point at which a yes actually took effect.
        self._sink.emit(ApprovalAnswered(signature=signature, allowed=True))

    def stop_reason(self) -> str | None:
        return self._inner.stop_reason()

    # -- internals ---------------------------------------------------------

    async def _wrap(
        self, verb: str, args: Mapping[str, str], call: Callable[[], Awaitable[str]]
    ) -> str:
        self._sink.emit(ActionStarted(verb=verb, args=dict(args)))
        before_screen = self.last_screen
        before_refusals = self.stats.refusals
        started = time.monotonic()
        try:
            rendered = await call()
        except Exception as exc:
            # Reported as an action that errored, not as a failed run. The
            # agent catches these and hands them to the model, which usually
            # fixes an ambiguous target or a stale ref on the next turn; the
            # run in the screenshot that prompted this went on to succeed.
            # Calling that a failure told someone their run had died three
            # times while it was working.
            #
            # Re-raised so the agent's own handling still gets it. If the run
            # really does die, `GoalRunner.run` reports that.
            self._sink.emit(
                ActionFinished(
                    verb=verb,
                    args=dict(args),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error=_describe(exc),
                    hint=str(getattr(exc, "hint", "") or ""),
                    stats=StatsSnapshot.of(self.stats),
                )
            )
            raise
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # Verification refuses before the device is touched and records it by
        # incrementing this counter. Reading the delta is indirect, but the
        # alternative is either parsing the reply text or reaching into the
        # backend's `Verifier`, and both couple the front end to internals
        # that `Backend` deliberately does not expose.
        refused = self.stats.refusals > before_refusals

        # `SessionBackend` only replaces `last_screen` when the action returned
        # a full digest. When the new screen is similar to the old one it
        # returns a delta instead, and the stored screen is deliberately left
        # alone. So this comparison is not "did the screen change", it is "do
        # we have a new screen to show", and the difference matters: a consumer
        # that treats the two as the same will display a screen the phone has
        # already left.
        refreshed = self.last_screen != before_screen

        self._sink.emit(
            ActionFinished(
                verb=verb,
                args=dict(args),
                rendered=rendered,
                elapsed_ms=elapsed_ms,
                refused=refused,
                screen_refreshed=refreshed,
                stats=StatsSnapshot.of(self.stats),
            )
        )
        if refreshed:
            self._sink.emit(ScreenUpdated(text=self.last_screen))
        return rendered


def streaming_chat_model(settings: AgentSettings, sink: EventSink) -> ModelFactory:
    """The configured provider, streamed.

    A sibling of `ios_agent.chat_model` rather than a wrapper around it: that
    one returns an already-bound callable, so there is no `.astream()` left to
    reach from outside. The construction is duplicated deliberately and kept
    identical, including the import error that names the extra to install.

    What it adds is a `ModelDelta` per chunk. The assembled `AIMessageChunk` is
    returned unchanged rather than rebuilt into an `AIMessage`: it is already a
    subclass of one, and rebuilding risks dropping fields that matter. Two of
    them matter a great deal.

    - **`usage_metadata`**, which `run_goal` reads to report what the model
      cost. Lose it and the token line silently reads zero, which looks like a
      free run rather than a broken meter.
    - **`tool_calls`**, whose ids are assembled from partial chunks and are the
      idempotency keys every action is keyed on. An id that differs between a
      call and its replay taps the device twice.

    `tests/tui/test_stream.py` asserts both against what `ainvoke` produces,
    because neither is visible until it is wrong.
    """
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import AIMessageChunk

    export_provider_credentials()

    def factory(tools: list[Any]) -> Callable[[list[AnyMessage]], Awaitable[AIMessage]]:
        try:
            chat = init_chat_model(
                model=settings.model, model_provider=settings.provider, **settings.chat_kwargs()
            )
        except ImportError as exc:
            raise ImportError(f"{exc}\n{settings.missing_package_hint()}") from exc

        bound = chat.bind_tools(tools)

        async def call(messages: list[AnyMessage]) -> AIMessage:
            assembled: AIMessageChunk | None = None
            async for chunk in bound.astream(messages):
                assert isinstance(chunk, AIMessageChunk)
                assembled = chunk if assembled is None else assembled + chunk
                if chunk.text:
                    sink.emit(ModelDelta(text=chunk.text))
            if assembled is None:
                # A provider that streamed nothing at all. Better an empty turn
                # the graph can end on than an exception out of the model node.
                return AIMessage(content="")
            sink.emit(
                ModelTurn(
                    text=assembled.text,
                    tool_calls=tuple(c["name"] for c in assembled.tool_calls),
                )
            )
            return assembled

        return call

    return factory
