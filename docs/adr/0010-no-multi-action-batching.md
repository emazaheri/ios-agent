# 10. No multi-action batching, but the path it runs on is now guarded

Accepted, 2026-09-20.

## Context

Observations have sat at the oracle's floor since S1, and ADR 0002 rejected
planning because the total headroom was one action in twenty. Both axes this
project reports were spent. **Model turns** was the axis nothing had measured,
and it is the only one grouping actions could move: batching changes no device
work at all, by construction.

`browser-use` solves the same problem with `multi_act` (`agent/service.py:2729`),
where the model returns a list of actions and two guards abort the remainder
when the page moves underneath them. A comparison of the two projects put it
first on a list of things worth taking from theirs.

The reframe that shaped the work: **batching already executed here, and it was
unguarded.** `graph.act` has always looped over `last.tool_calls`, a test has
scripted two taps in one turn since approvals were wired up, and the operator
prompt never said anything about how many calls a turn may carry.

## Decision

**The prompt does not invite batching.** The measurement says it buys nothing
and costs 25%.

Everything built to measure it stays, because it is load-bearing on its own:
the turn counter, the batch guard in `graph.act`, `batch.py`, the `turn_floor`
derived from the guard, and `DEFAULT_MAX_ACTIONS`.

## What the measurement said

Two runs of the 13-task set, 3 runs each, `gpt-5.6-sol`, identical in every
respect but the prompt paragraph:

| | s7-before | s7-after | |
|---|---|---|---|
| success | 39/39 | 39/39 | held |
| observations | 40 | 42 | +5.0% |
| actions | 135 | 140 | +3.7% |
| **turns** | **238** | **248** | **+4.2%** |
| device tokens | 25,682 | 26,485 | +3.1% |
| prompt tokens | 336,901 | 419,463 | +24.5% |
| cost | $2.007 | $2.517 | +25.4% |
| seconds | 500.0 | 559.1 | +11.8% |

The threshold set before the run was turns falling at least 15%, with success,
observations, actions and refusals not rising. Turns rose. Three of the four
guards also tripped. Any one of them was a rejection.

**The mechanism is not that batching failed to pay. It is that the model would
not batch.** Tracing three tasks with the invitation in place, grouping every
tool call by the turn that requested it:

    TURNS WITH >1 CALL: 0    turns with exactly 1: 24

Zero, including `three_switches_three_panes`, the task whose ceiling said
eleven turns could collapse to three and which alone carried a third of the
entire predicted win. `actions / turns` was 0.57 before the invitation and 0.56
after it. The paragraph changed the bill and not the behaviour.

So the extra cost is the paragraph itself: a longer system prompt on more
turns, compounding, for a capability the model declined to use.

## Why the machinery stays

Rejecting the feature does not restore the prior state, because the prior state
had a latent bug and an unbounded path.

- **The guard.** `graph.act` runs every call in a turn and decided nothing
  between them. A model that emits two calls today is guarded; before this it
  was not. `batch.py` states the rule, and the rule is not browser-use's: on
  iOS the valuable batch is navigational, so a screen that *changed* is the
  expected case and a screen that did **not** is the abort.
- **`done` mid-turn.** As call 1 of 3 it let calls 2 and 3 run. Reachable only
  by batching, which is why nobody had hit it.
- **`DEFAULT_MAX_ACTIONS`.** `max_steps` counted turns, and one action per turn
  meant it bounded the device side too. Any multi-call turn breaks that link,
  so the bound on how many things get done to a stranger's phone is now stated
  rather than implied.
- **`turn_floor`.** Derived by replaying the oracle's recorded outcomes through
  the guard, so it measures the guard rather than a script written to satisfy
  it, and it is asserted by equality in the free CI series.

Counting turns also paid for itself immediately, before any of this was
decided: the edge out of `act` went unconditionally back to `agent`, so every
run ever measured spent one extra model call after `done` on a transcript whose
last word was "recorded". That is 39 wasted calls in the S5 figures.

## What would reopen this

A model that batches when invited. Nothing here says grouping is a bad idea;
it says `gpt-5.6-sol` did not do it, so the ceiling was never tested. The
headroom is real and unclaimed: 238 turns against a batched-oracle ceiling of
120.

Worth trying again on a different model, or by making the batch the shape of
the request rather than a permission: a verb that takes a route, say, rather
than a paragraph saying several calls are allowed. That is a different design
and would need its own measurement; this record rejects the paragraph, not
the idea.

`tests/evals/history.jsonl` carries both slices under `agent-model` so the
comparison survives without rerunning it.
