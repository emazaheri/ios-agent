# 15. Route routine turns to a small model

**Accepted as an option, off by default, 2026-09-25.** Pre-registered first:
everything below the line "Results" was filled in after the measurement. Everything above it was merged to
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

Run exactly as registered: 19 tasks, 3 runs each, 57 per arm, back to back on
the same code (`.artifacts/evals/agent-s15-routing.json`).

| | baseline | routed |
|---|---|---|
| passed | 55/57 | **57/57** |
| cost | $2.614 | **$1.278** |
| actions against the floor | 1.30x | 1.14x |
| turns | 335 | 297 |
| planted baits obeyed | 0/12 | 0/12 |
| runs that escalated | | 28/57 |

**The rule:**

1. routed passes at least baseline minus 2: 57 against 53. **Holds.**
2. no planted task obeyed more often: 0 and 0. **Holds.**
3. routed cost at most 50% of baseline: **48.9%. Holds, barely.**

So routing is adopted, as registered: an option, off by default, set with
`IOS_AGENT_ROUTE_MODEL`.

**The margin on rule 3 is 1.1 points, and it should be read as a tie.** This
suite has measured cost moving 30% between two arms of identical code, so a
second run could land on either side of 50%. The rule was fixed in advance so
that a result this close would be decided by the rule rather than by argument,
and the rule says adopt. It does not say routing reliably halves cost.

The two extra passes are not an improvement either. Baseline's two misses, one
run scrolling a long list 17 times and one taking 7 actions on a read with a
floor of 0, are ordinary model misses on tasks routed happened not to miss.

**The book's claim.** Success held: routed over baseline is 1.04 against a
claimed 0.90. Cost did not: 0.49 against a claimed 0.15. Two things put the
floor well above 15%. A small-model turn here costs about a fifth of a
large-model one, so even a run that never escalates cannot reach 15%. And half
the runs escalated: the 29 that never did cost $0.11 between them, the 28 that
did cost $1.17, and `gpt-5.6-sol` accounts for $1.10 of the routed arm's $1.28.

**Why runs escalated.** Almost always at turn 2, right after the first action,
and on eight tasks every time. A diagnostic rerun of three of them, not part of
the registered measurement, found two kinds:

- a real small-model mistake: batching a tap into Wi-Fi with a tap on
  "Accessibility" in the same turn, when the second no longer exists on the
  screen the first led to;
- a false trigger: tapping a search field to focus it changes nothing visible,
  so it reads as a no-op, though nothing went wrong.

**What is not measured.** The small model's own claim about its result. In a
smoke run before the measurement, `gpt-5.4-mini` turned Bold Text on and then
reported failure. The suite judges the device, so that counts as a pass, but
`ios-agent run` would exit 1 on it, since the exit code follows the verdict on
the claim. How often a routed run under-claims was not registered and is not
known.

The measurement cost $3.89, over the ~$3 estimated, because the baseline arm
alone cost $2.61.

## What would reopen it

- **Turning it on by default** needs its own measurement, as registered.
- **Fewer false triggers.** Not treating a focus tap as a no-op, or tolerating
  one recoverable error before escalating, would keep more runs small. Both are
  changes to the mechanism, so each needs a new pre-registered measurement
  rather than a tweak to this one.
- **A different pair of models**, or a change in `gpt-5.6-sol`'s promotional
  price, which ends "at least" November 21, 2026 and would move rule 3.
- **A measurement of under-claiming**, if routing is to be used where the exit
  code matters.
