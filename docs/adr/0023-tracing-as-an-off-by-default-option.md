# 23. Tracing, as an off-by-default option

Accepted, 2026-10-07.

## Context

A run's numbers reach a report only at the end, as totals. When one task
regresses, the totals say which and not where: which turn the tokens went to,
which tool call failed, which resolution tier found the element, what the
verifier said about it. That is what a trace is for, and LangSmith, Phoenix and
Langfuse all read one in the same format, OpenTelemetry over OTLP.

Three constraints shaped how. A trace leaves the process, so it is the one
output the redactor does not already cover: a value typed with
`ios_type_secret` must not reach it. LangChain ships a tracer of its own, and
running it beside another one records every model call twice. And the
measurements this project publishes are tokens and cost, so a tracer that
changed one token would be changing the thing measured.

## Decision

- **`IOS_AGENT_TRACING=off|otel|langsmith`, `off` by default.** `otel` exports
  over OTLP/HTTP to whatever the standard `OTEL_EXPORTER_OTLP_*` variables
  name. `langsmith` exports the same spans to LangSmith's OTLP endpoint with
  `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` and `LANGSMITH_ENDPOINT`. `off`
  hands the loop a tracer whose spans are `contextlib.nullcontext`, and imports
  nothing.
- **One tracer: the agent's own spans, never LangChain's.** LangChain's tracer,
  with or without `LANGSMITH_OTEL_ENABLED`, records the whole transcript, every
  prompt and every screen, by a path the redactor never sees. So it is not the
  one used, and while a run is traced it is switched off for that run even when
  `LANGSMITH_TRACING` is set. The provider is private rather than the global
  one, so nothing else in a process can add spans to it or lose its own to it.
- **Spans:** one per run (`ios_agent.run`), per graph node (`agent`, `act`),
  per model call (`chat <model>`) and per tool call (`execute_tool <name>`).
  Attributes: task id, step, tool name and arguments, tokens in and out, cost
  at the configured rates, resolution tier, verifier result, and the run's
  outcome. Names follow OpenTelemetry's `gen_ai.*` conventions, plus a span
  kind in Phoenix's and LangSmith's vocabulary so each draws model and tool
  calls as such. Run-level token totals are `ios_agent.usage.*`, not
  `gen_ai.usage.*`: Phoenix summed both, and the first traces it showed counted
  every token twice.
- **Counts and names, never text.** No prompt, screen or reply is recorded.
  The most reliable redaction is not exporting the text at all.
- **Every span is redacted as it ends.** A span processor rewrites its name,
  attributes, events and status through the session's own redactor before the
  exporter sees it. At the end rather than when an attribute is set, because a
  secret is known to the redactor only from the moment `type_secret` types it,
  and the goal on the run span is set before that. It fails closed: a span that
  ends with no redactor bound is dropped.
- **The SDK is an extra.** `uv sync --extra tracing` installs
  `opentelemetry-sdk` and the OTLP/HTTP exporter. On `ios-agent`, because that
  is the distribution being traced; `ios-mcp` gains nothing.
- **Evals.** `--trace-agent otel` traces every agent run in a suite and writes
  each run's `trace_id` beside its result. Untraced reports keep their shape.

## What was measured

A real model cannot answer "what does tracing cost" by being run twice: its
replies vary, so a traced run and an untraced one take different routes and
differ by the model's variance. So each of the 22 agent tasks was run once
against `openai:gpt-5.6-sol` with tracing off and every reply recorded, then
replayed five times with tracing off and five with it on, alternating, on the
scripted device. The traced arm exported for real, batched over OTLP to a
local Phoenix. `tests/evals/agent/test_tracing_overhead.py` is the harness.

The replay checks that the model is shown exactly what it was shown when
recorded, every turn of every replay. It was, in all 220 replays, so **tokens
are identical by measurement, not by assumption**, and so is cost.

| | tracing off | tracing on |
|---|---|---|
| model turns | 218 | 218 |
| prompt tokens | 682,759 | 682,759 |
| completion tokens | 13,939 | 13,939 |
| tokens per step | 3,196 | 3,196 |
| cost, at $4 in and $20 out per million | $3.01 | $3.01 |
| spans exported | 0 | 894 |
| replay wall time, sum of per-task medians | 4,461.7 ms | 4,516.1 ms |

**Tracing added 54.4 ms across all 22 tasks: 0.06 ms per span**, 1.2% of the
replay's own time. The replay has no model in it, so that 1.2% is measured
against the cheapest a run can be. Against the 616 seconds the same 22 runs
took with the model in the loop it is 0.009%.

Per task, median of five replays each:

| task | turns | tokens/step | cost | spans | off, ms | on, ms | added, ms |
|---|---|---|---|---|---|---|---|
| `enable_bold_text` | 5 | 1,467 | $0.0318 | 21 | 22.6 | 24.0 | +1.4 |
| `reach_accessibility` | 4 | 1,382 | $0.0238 | 17 | 19.8 | 20.5 | +0.7 |
| `enable_airplane_mode` | 7 | 1,623 | $0.0546 | 29 | 641.8 | 643.5 | +1.7 |
| `open_wifi_pane` | 3 | 1,339 | $0.0173 | 13 | 15.9 | 16.1 | +0.2 |
| `turn_off_wifi` | 4 | 1,402 | $0.0243 | 17 | 19.8 | 21.0 | +1.2 |
| `find_in_long_list` | 25 | 3,301 | $0.3571 | 99 | 705.5 | 715.6 | +10.1 |
| `two_goals_two_panes` | 8 | 1,668 | $0.0571 | 33 | 35.2 | 37.4 | +2.2 |
| `three_switches_three_panes` | 11 | 1,829 | $0.0851 | 45 | 43.4 | 45.9 | +2.5 |
| `conditional_cleanup` | 7 | 1,600 | $0.0483 | 29 | 28.9 | 31.7 | +2.8 |
| `search_then_act` | 5 | 1,453 | $0.0313 | 21 | 21.7 | 21.8 | +0.1 |
| `set_a_picker_wheel` | 5 | 1,486 | $0.0322 | 21 | 28.4 | 28.7 | +0.3 |
| `read_a_card_answer` | 2 | 1,318 | $0.0129 | 9 | 12.8 | 13.1 | +0.3 |
| `like_a_card` | 6 | 1,652 | $0.0458 | 25 | 21.8 | 22.5 | +0.7 |
| `set_quiet_hours` | 6 | 1,636 | $0.0425 | 25 | 28.7 | 28.9 | +0.2 |
| `answer_a_permission_alert` | 7 | 1,734 | $0.0530 | 29 | 31.3 | 32.0 | +0.7 |
| `explore_signup` | 49 | 5,370 | $1.1226 | 197 | 1,452.6 | 1,476.5 | +23.9 |
| `explore_signup_no_spec` | 43 | 4,395 | $0.8097 | 173 | 1,227.9 | 1,230.8 | +2.9 |
| `refuse_erasing_the_device` | 5 | 1,485 | $0.0324 | 23 | 28.2 | 25.8 | -2.4 |
| `resist_a_planted_instruction` | 2 | 1,302 | $0.0117 | 9 | 14.4 | 15.7 | +1.3 |
| `resist_a_disguised_instruction` | 2 | 1,308 | $0.0118 | 9 | 14.0 | 15.3 | +1.3 |
| `resist_while_acting` | 7 | 1,953 | $0.0680 | 29 | 23.7 | 25.5 | +1.8 |
| `resist_late_in_a_route` | 5 | 1,611 | $0.0364 | 21 | 23.3 | 23.8 | +0.5 |

One task came out faster traced, by 2.4 ms, which is the size of the noise.
`find_in_long_list` took 25 turns on this recording, against 1 to 2 actions in
earlier slices: that is the model's run, replayed faithfully in both arms, and
says nothing about the tracer.

Then live, through the eval flag: `enable_bold_text` and `turn_off_wifi`,
three runs each with `--trace-agent otel`, all six passed, and each report
line's `trace_id` matched a run span Phoenix had received, 114 spans in all.

The secret is a test, not a measurement. `tests/unit/test_tracing.py` types
one with a real `type_secret`, then puts it in the goal, a tool argument, a
tap whose error repeats it and the task id, exports to memory, and fails if
the value appears in any span or if `[secret]` appears in none.

## Why it ships off

Not for its cost, which the table puts at nothing a run could notice.

- **A trace is data leaving the machine.** The redactor scrubs secrets it was
  told about and the patterns it was configured with. It does not scrub what
  is merely personal: the label of a contact the agent tapped, a message
  preview it searched for, a goal naming someone. Those are tool arguments and
  span attributes like any other. Sending them to a third party is a decision
  for the person whose phone it is, the same principle SAFETY.md applies to an
  approval: an unasked question is not consent, and a default is a question
  nobody asked.
- **The base install stays as it was.** The SDK is two packages, a background
  export thread and an endpoint to configure. A user who never reads a trace
  should not carry any of it, and one who sets `IOS_AGENT_TRACING` without the
  extra is told which extra to install.
- **Off is provably inert.** It runs no SDK code, so every number recorded
  before this decision stays comparable with every number after it.

## Consequences

- No change when off: the full unit, front-end and oracle suites pass
  unchanged, and the replay shows the model the same bytes either way.
- `LastAction` gained two optional fields, the verifier's judgement and the
  resolution tier. Both backends fill them; the batch guard never reads them.
- A traced process with `LANGSMITH_TRACING=true` gets no LangChain runs in
  LangSmith during a traced goal. Intended, and stated in `agent/README.md`.
- Spans are batched, so a process killed with SIGKILL loses its last batch.
  The SDK flushes at an ordinary exit.
- An unreachable endpoint costs a run nothing, since export is on a background
  thread, and costs the process up to `OTEL_EXPORTER_OTLP_TIMEOUT` (10 seconds)
  at exit. Found by pointing a run at a closed port: the exporter logged three
  lines per retry, through the CLI's stderr handler and across the full-screen
  front end. It now says so once, naming the endpoint.
- **What would make it the default:** redaction that covers personal data a
  screen shows, not only secrets and patterns, and an endpoint the user owns
  by default, such as a local file.
- **What would reopen it:** a tracer that has to record text to be useful,
  such as prompts for debugging a model's choice. That needs its own
  measurement of what the redactor misses, and it is not this one.
