# ios-agent

A goal-directed agent that drives an iPhone by consuming the `ios-mcp`
library. It is a peer of the MCP server, not a layer above it: both are
consumers of the same `IosSession`.

Kept as a separate distribution so `ios-mcp` never acquires a dependency on
LangGraph or on a model provider. `tests/unit/test_layering.py` asserts the
dependency only ever points one way. See
`docs/adr/0001-the-agent-is-a-peer-of-the-server.md`.

## Choosing a model

The provider is configuration, not a dependency. The base install commits to
no vendor: the loop builds its model through LangChain's `init_chat_model`, so
switching is two environment variables and an extra.

```bash
uv sync --extra anthropic       # the default, and what the numbers were measured on
uv sync --extra openai
uv sync --extra ollama          # runs locally, no API key involved
```

```bash
export IOS_AGENT_PROVIDER=openai
export IOS_AGENT_MODEL=gpt-5.5
```

| Setting | Default | Notes |
|---|---|---|
| `IOS_AGENT_PROVIDER` | `anthropic` | Anything `init_chat_model` accepts |
| `IOS_AGENT_MODEL` | `claude-opus-5` | Change together with the provider |
| `IOS_AGENT_MAX_TOKENS` | `16000` | Thinking and the reply share this budget |
| `IOS_AGENT_EFFORT` | `medium` | Anthropic only, silently skipped elsewhere |
| `IOS_AGENT_PROMPT_CACHE` | `true` | Anthropic only, see below |
| `IOS_AGENT_TEMPERATURE` | unset | Never sent unless set, see below |
| `IOS_AGENT_MAX_STEPS` | `24` | Turns before the loop gives up |
| `IOS_AGENT_EXTRA` | `{}` | Passed through untouched, overrides everything above |

Two parameters are deliberately conditional, because sending either to the
wrong model is an error rather than a no-op:

- **`effort`** is an Anthropic concept, sent as `output_config`. It is skipped
  entirely for every other provider.
- **`prompt_cache`** sends `cache_control`, Anthropic only: one breakpoint at
  the end of the system prompt, which every run shares, and automatic caching
  on the transcript, which every turn re-sends. Other providers cache without
  being asked and are sent nothing. See `docs/adr/0022`.
- **`temperature`** is never sent unless you set it. Claude Opus 5, Opus 4.8,
  Opus 4.7 and Sonnet 5 reject it with a 400, so it cannot be a default. On
  providers that accept it, setting it works normally.

Anything this package has not heard of goes in `extra`, which is applied last
and can override any of the above. A new provider should not need a code
change.

Extras are declared for `anthropic`, `openai`, `azure_openai` (via `openai`),
`google_genai`, `google_vertexai`, `ollama`, `groq`, `mistralai` and
`bedrock_converse`. A provider outside that list still works if its LangChain
integration package is installed; the list only decides how specific the error
message is when one is missing.

## Approval

Destructive actions pause the graph with a LangGraph `interrupt()` and resume
with the answer. `run_goal(..., approve=callback)` supplies the decision;
omitting it refuses everything destructive, which is the correct default for an
unattended run.

The pause is safe because the policy gate classifies *before* acting, so
nothing has reached the device when the question is asked. Resuming re-runs the
whole node, so every action keys its idempotency cache on the tool call id,
which LangGraph replays unchanged. Keying on a step counter instead produces a
new key on the re-run and taps the device twice.

## Two ways to reach the device

`SessionBackend` calls `IosSession` in-process and is the production path.
`McpBackend` speaks the protocol to a `fastmcp.Client`, which is how any other
consumer would reach the server, and exists to prove the server is not
privileged from the inside.

Measured on the same route, median of five runs: **+1.6 ms per call and zero
extra tokens**. Payloads are identical because the server serialises the same
dict the library returns, and a test asserts that equality so the two sets of
numbers stay comparable.

## Running the evals

```bash
uv run pytest tests/evals/agent              # scripted device, no model, no hardware
uv run pytest tests/evals/agent -m model -s  # with a model in the loop
```

The second skips with a reason naming the configured model when no provider is
reachable. Token cost is priced at Claude Opus 5's rates by default; set
`IOS_AGENT_USD_PER_MTOK_IN` and `IOS_AGENT_USD_PER_MTOK_OUT` for anything else,
since nothing here can know what a given vendor charges. Cached prompt tokens
are priced apart, at `IOS_AGENT_USD_PER_MTOK_CACHE_READ` and
`IOS_AGENT_USD_PER_MTOK_CACHE_WRITE`, which default to Anthropic's 5-minute
multipliers of the input price.

## Tracing

Off by default. `IOS_AGENT_TRACING=otel` or `langsmith`, after
`uv sync --extra tracing`, records every run as OpenTelemetry spans:

| span | carries |
|---|---|
| `ios_agent.run` | task id, goal, model, total tokens in and out, cached and written to cache (as `ios_agent.usage.*`, so a viewer summing the trace does not count them twice), cost, turns, actions, verified |
| `agent`, `act` | one per graph node execution, with its step |
| `chat <model>` | step, tokens in and out, cache reads and writes, cost, the tools the reply asked for |
| `execute_tool <name>` | step, arguments, resolution tier, verifier result, any error |

Names follow OpenTelemetry's `gen_ai.*` conventions, and each span also says
what kind it is in Phoenix's and LangSmith's vocabulary, so all three draw model
and tool calls as such.

**One tracer.** `langsmith` sends the same spans to LangSmith's OTLP endpoint,
using `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` and `LANGSMITH_ENDPOINT`. It
does not use LangChain's own tracer, and switches it off for the run even when
`LANGSMITH_TRACING` is set: that tracer would record every model call a second
time, with the whole transcript, by a path the redactor never sees.

**Redaction.** Every span is rewritten by the session's redactor as it ends,
name, attributes, events and status alike, and a span that ends with no
redactor bound is dropped. Secrets typed with `type_secret` and the configured
patterns are scrubbed exactly as they are from the transcript.

**Phoenix, locally:**

```bash
uvx --from arize-phoenix phoenix serve       # http://localhost:6006
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006 IOS_AGENT_TRACING=otel \
  uv run ios-agent "turn on bold text"
```

**An endpoint that is down** never slows a run: spans are exported on a
background thread. It is reported once, as one warning naming the endpoint,
rather than on every retry, and the process waits up to
`OTEL_EXPORTER_OTLP_TIMEOUT` (10 seconds by default) at exit before giving the
last batch up.

**Evals.** `--trace-agent otel` (or `langsmith`) traces every agent run in the
suite and writes each run's `trace_id` beside its result in the report.
