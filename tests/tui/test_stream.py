"""Streaming, and the two things it quietly breaks.

Both failures here are invisible until they matter, which is why they are
asserted against what the non-streaming path produces rather than against a
hand-written expectation.

- **`usage_metadata`** is what `run_goal` reads to report the model's cost.
  A provider that omits it under streaming makes every run look free.
- **`tool_calls`** are assembled from partial chunks, and their **ids** are the
  idempotency keys every action is keyed on. An id that comes out different
  from the non-streaming one means a resumed graph taps the device twice.

The assembly under test is LangChain's own `AIMessageChunk.__add__`, fed the
chunk shapes a real provider sends. LangChain's `GenericFakeChatModel` cannot
be used here: it streams the content and drops both tool calls and usage, so it
would pass this file while proving nothing about either.
"""

from __future__ import annotations

from ios_tui.bus import ListSink
from ios_tui.events import ModelDelta, ModelTurn
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages.tool import ToolCallChunk

#: One turn, in the shape a provider actually sends it.
#:
#: LangChain's own `GenericFakeChatModel` streams the content and drops both
#: tool calls and usage, so it cannot be used here: it would pass this file
#: while telling us nothing. These chunks carry what a real stream carries.
#: The text arrives in pieces, the tool call arrives as `ToolCallChunk`
#: fragments that have to be merged by index, its arguments arrive as a
#: partial JSON string, and usage lands on the final chunk the way OpenAI and
#: Anthropic both send it.
#:
#: The assembly under test is LangChain's `AIMessageChunk.__add__`, which is
#: the real code that turns those fragments back into a tool call.
CHUNKS = [
    AIMessageChunk(content="Opening "),
    AIMessageChunk(content="Accessibility now."),
    AIMessageChunk(
        content="",
        tool_call_chunks=[ToolCallChunk(name="tap", args="", id="call-abc", index=0)],
    ),
    AIMessageChunk(
        content="",
        tool_call_chunks=[ToolCallChunk(name=None, args='{"target": ', id=None, index=0)],
    ),
    AIMessageChunk(
        content="",
        tool_call_chunks=[ToolCallChunk(name=None, args='"Accessibility"}', id=None, index=0)],
    ),
    AIMessageChunk(
        content="",
        usage_metadata={"input_tokens": 1200, "output_tokens": 42, "total_tokens": 1242},
    ),
]

#: What the same turn looks like when it is not streamed. Every assertion below
#: compares against this rather than against a hand-written expectation, so the
#: test says "streaming changes nothing" rather than "streaming produces what I
#: happened to write down".
WHOLE = AIMessage(
    content="Opening Accessibility now.",
    tool_calls=[
        {"name": "tap", "args": {"target": "Accessibility"}, "id": "call-abc", "type": "tool_call"}
    ],
    usage_metadata={"input_tokens": 1200, "output_tokens": 42, "total_tokens": 1242},
)


async def _stream_once(sink: ListSink) -> AIMessage:
    """The body of `streaming_chat_model`'s call, over the chunks above.

    The factory resolves a provider from configuration, so its loop is
    exercised here directly against the chunks rather than by monkeypatching
    `init_chat_model`, which would test the patch.
    """
    assembled: AIMessageChunk | None = None
    for chunk in CHUNKS:
        assembled = chunk if assembled is None else assembled + chunk
        if chunk.text:
            sink.emit(ModelDelta(text=chunk.text))
    assert assembled is not None
    sink.emit(
        ModelTurn(text=assembled.text, tool_calls=tuple(c["name"] for c in assembled.tool_calls))
    )
    return assembled


async def test_the_text_arrives_in_fragments_that_add_up_to_the_whole_turn() -> None:
    sink = ListSink()
    assembled = await _stream_once(sink)

    deltas = sink.of_type(ModelDelta)
    assert len(deltas) > 1, "nothing was streamed; this test would prove nothing"
    assert "".join(d.text for d in deltas) == assembled.text
    assert assembled.text == WHOLE.text


async def test_a_streamed_turn_is_still_an_ai_message() -> None:
    """`ios_agent.loop` asserts this, and an `AIMessageChunk` satisfies it."""
    assembled = await _stream_once(ListSink())
    assert isinstance(assembled, AIMessage)


async def test_the_tool_call_ids_survive_assembly() -> None:
    """The ids are the idempotency keys, so this is not a cosmetic property.

    LangGraph replays the node an interrupt was raised from, and every action
    keys its idempotency cache on the tool call id. An id that differs between
    the streamed and non-streamed paths means a resumed run taps Send twice.
    """
    streamed = await _stream_once(ListSink())

    assert [c["id"] for c in streamed.tool_calls] == [c["id"] for c in WHOLE.tool_calls]
    assert [c["name"] for c in streamed.tool_calls] == [c["name"] for c in WHOLE.tool_calls]
    assert [c["args"] for c in streamed.tool_calls] == [c["args"] for c in WHOLE.tool_calls]
    assert streamed.tool_calls[0]["id"] == "call-abc"


async def test_the_turn_event_names_the_tools_the_model_asked_for() -> None:
    sink = ListSink()
    await _stream_once(sink)

    turns = sink.of_type(ModelTurn)
    assert len(turns) == 1
    assert turns[0].tool_calls == ("tap",)


async def test_usage_metadata_is_not_dropped_by_streaming() -> None:
    """Otherwise the cost line reads zero and looks like a free run.

    If this fails for a real provider it is a provider setting, not a bug here:
    OpenAI needs `stream_usage=True`, which belongs in `IOS_AGENT_EXTRA` rather
    than in this package.
    """
    streamed = await _stream_once(ListSink())

    assert streamed.usage_metadata == WHOLE.usage_metadata
    assert streamed.usage_metadata is not None
    assert streamed.usage_metadata["input_tokens"] == 1200
