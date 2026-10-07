# 22. Cache the prompt prefix

**Proposed, pre-registered, 2026-10-07.** Everything above the line "Results"
is merged to `main` before a single paid run, for the reason ADR 0015 gives:
the thing measured is cost, and cost is the easiest number to explain away
after the fact.

## Context

Cost here is almost all input. The 39-run measurement behind the README used
325,809 input tokens and 9,642 output tokens. Every turn re-sends the tool
definitions, the operator prompt and the whole transcript so far, so most of
what a turn pays for is a prefix the provider was sent a moment earlier.

Two things were wrong with how that was counted.

**The harness could not see a cache hit.** LangChain reports cached tokens
inside `input_tokens` and breaks them out separately in `input_token_details`,
which nothing read. ADR 0015 said so ("the harness does not see cache hits").
Every figure recorded so far was measured on `openai:gpt-5.6-sol`, which caches
a repeated prefix without being asked, so those dollar figures price tokens
OpenAI may have billed at a discount. By how much is not known, and this
measurement is the first that can say.

**Anthropic caches only when asked**, with `cache_control`. The default
provider is Anthropic, and the loop never asked.

## What changes

Metering, on every provider, with no change in behaviour: `Outcome` and the
eval reports carry `cache_read_tokens` and `cache_write_tokens` as a breakdown
of the prompt total. They are priced at `usd_per_mtok_cache_read` and
`usd_per_mtok_cache_write`, which default to Anthropic's 5-minute rates of 0.1x
and 1.25x the input price. Totals stay comparable with every earlier report.

Caching, on Anthropic only, as `prompt_cache` (on by default, sent to no other
provider, the same rule `effort` follows). It uses two breakpoints with a
5-minute TTL:

- one at the end of the system prompt, so tools and prompt, the same for every
  run, are a cache hit from the first turn of every run after the first;
- top-level automatic caching, which moves a breakpoint to the end of the
  transcript, so each turn reads the previous turn's prefix.

Claude Opus 5's minimum cacheable prefix is 512 tokens, and tools plus prompt
come to about 2,500, so both breakpoints qualify from the first turn. The
prefix is stable by construction: the goal arrives as a user turn so the system
prompt is byte-identical across tasks, the tool order is fixed, the transcript
only grows, and `effort` is pinned for the run.

## Design: one arm, priced twice

Caching changes what a token costs, not what the model is shown, so a cached
run and an uncached run of the same task produce the same token counts. That
makes a second arm unnecessary. Each cached run is priced twice from its own
counts:

- **cached**: uncached tokens at the input rate, cache reads and writes at
  their own rates, output at the output rate;
- **uncached**: every prompt token at the input rate, output at the output
  rate, which is what the same run would have cost without caching.

A paired comparison is also the fairer one. This suite has measured cost
moving 30% between two arms of identical code (ADR 0015), which is noise on
the order of the effect, and pricing one run two ways has none.

The run: `anthropic:claude-opus-5` with `IOS_AGENT_PROMPT_CACHE=true`, all 19
tasks in `tests/evals/agent/tasks.py`, **3 runs each, 57 runs**, back to back
on the scripted device, routing off. Routing stays off because caches are
model-scoped: a routed run that escalates starts the large model's cache cold,
so the two levers interact and are measured one at a time.

## Metrics

- **Success**: passes out of 57, by each task's own predicate over the device.
- **Cost**, both ways above, at Claude Opus 5's list prices. The report
  carries both, as `usd` and `usd_uncached`.
- **Cache hit rate**: cache-read tokens over prompt tokens.
- **Turns**, and **actions** against the oracle floor.

Separately, and not part of the rule: the cache hit rate of one
`gpt-5.6-sol` slice, to say what the earlier, cache-blind figures cost.

## The rule, fixed now

Caching stays **on by default** only if both hold:

1. at least 53 of 57 runs pass: ADR 0015's baseline on the same tasks, 55,
   with the same tolerance of 2. Caching should not move this; the rule is there to catch a cached
   request that breaks something, not to measure the model;
2. cost priced as cached is at most **60%** of the same runs priced as
   uncached.

Otherwise it is kept as an option and **turned off by default**. The bar is
60% rather than ADR 0015's 50% because a cache write costs more than an
uncached token: a short run that writes its transcript and finishes before
reading much of it back can cost more cached than not, and the bar must leave
room for the task set's short runs.

The budget is about $4: ADR 0015's $2.61 for 57 runs at `gpt-5.6-sol` prices,
scaled to Opus 5's, then cut by caching, plus the OpenAI slice.

## Results

Not yet run.

## What would reopen it

- **The 1-hour TTL**, if runs are ever spaced further apart than five minutes,
  as in a hand-run session where a person reads each result.
- **A behaviour change under caching.** The design assumes the model is shown
  the same thing either way. If rule 1 fails, that assumption is what to
  check first, and a two-arm run is what would test it.
- **Routing together with caching**, which needs its own measurement because
  each escalation starts a cold cache.
- **A provider other than Anthropic** that needs to be asked, as Anthropic does.
