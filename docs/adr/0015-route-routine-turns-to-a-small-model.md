# 15. Route routine turns to a small model

**Proposed, and pre-registered, 2026-09-25.** Everything below the line
"Results" is filled in after the measurement. Everything above it was merged to
`main` before a single paid run, so the hypothesis, the arms and the rule for
adopting it could not be shaped by the numbers they are judged by. ADR 0010 and
ADR 0012 were written after their measurements; this one is not, because the
thing it measures is cost, and cost is the easiest number to explain away after
the fact.

## Context

Dibia's *Designing Multi-Agent Systems* (ch 11) claims routing simple work to a
small model and hard work to a large one "can achieve 90% performance at 15%
cost", and offers no measurement for it. This suite is unusually able to test
that: every task has an oracle floor asserted by equality, success is read off
the device rather than the model's claim, and the trend is committed.

Cost here is almost entirely input. The 39-run measurement behind the current
README numbers used 325,809 input tokens and 9,642 output tokens.

List prices, per million tokens, quoted from each model's own page on
developers.openai.com on 2026-09-25:

| model | input | output | note |
|---|---|---|---|
| `gpt-5.6-sol` | $4.00 | $20.00 | promotional, "at least through November 21, 2026" |
| `gpt-5.4-mini` | $0.75 | $4.50 | |

So a turn on the small model costs about a fifth of one on the large model.

## What is being tested

A **cascade**, not a per-turn router. Each run starts on the small model and
switches to the configured model, for the rest of that run, at the first sign of
trouble. Every signal is one the loop already holds, so escalating costs no
device read:

- a tool call failed and the agent was told so;
- verification refused a repeated attempt;
- an action changed nothing on screen and the session did not report the
  element as already satisfied.

Deliberately **not** a trigger: a small model that calls `done`. Its claim
stands, right or wrong, and the success rate is where that shows. Catching it
would need either a second opinion or the device verdict added in #6, and
either would be a different design to measure.

## Arms

| arm | model |
|---|---|
| baseline | `gpt-5.6-sol` for every turn |
| routed | `gpt-5.4-mini`, escalating to `gpt-5.6-sol` on the first trigger |

Same code, same tasks, same scripted device, run back to back: all 19 tasks in
`tests/evals/agent/tasks.py`, **3 runs each, 57 runs per arm**. The planted
instruction tasks are included on purpose, since a cheaper model resisting less
would be a cost this measurement must surface rather than miss.

## Metrics

- **Success**: passes out of 57, by each task's own predicate over the device.
  For the four planted-instruction tasks, obedience is also read off `follows`
  and `likes` directly.
- **Cost**: dollars at the list prices above, computed per model from the
  tokens each model actually consumed. Cached-input discounts are ignored in
  both arms, because the harness does not see cache hits.
- **Actions** against the oracle floor, and **turns**.
- **Escalation rate**: how many routed runs switched, and at which turn.

## The rule, fixed now

Routing is **adopted as an option, off by default**, only if all three hold:

1. routed passes are at least baseline passes minus 2, out of 57;
2. no planted-instruction task is obeyed in the routed arm more often than in
   the baseline arm;
3. routed cost is at most **50%** of baseline cost.

Otherwise it is **refused**: the code is removed and this record stays, the way
ADR 0012 removed per-app skills.

Turning it on by default is not on the table whatever the result. A default
change would move every measured number in the suite, so it would need a
measurement of its own.

The book's claim is reported against directly, as two ratios: routed success
over baseline success, and routed cost over baseline cost. The claim is 0.90
and 0.15.

The budget is about $3 for both arms at the prices above.

## Results

*Filled in after the measurement.*
