# 2. No explicit planner

Accepted, 2026-08-25.

## Context

The phase plan listed planning as the first of four deep-agent pillars, and
CLAUDE.md describes it as the pillar where agentic behaviour is most visible:
decompose "book a table for two at seven" into steps, and replan when a tap
does not do what the plan assumed.

It also set the rule this record applies: *adopt a pillar because a measured
constraint demands it, never because the framework offers it. If a pillar
cannot be justified that way, leave it out and say why.*

After S2 there was no measured constraint left for a planner to relieve. Six of
seven tasks sat at or below a hand-written oracle's action count, and the
longest task in the set was three actions. Concluding anything about planning
from that set would have measured the set, not the planner.

## What was measured

Three long-horizon tasks were added first, designed so that a planner would
have something to be right about: several sub-goals, in panes up to three
levels apart, one with a true branch and a false one. Their oracle routes are
5, 9 and 5 actions, each asserted against the oracle so the floor cannot drift.

The **existing S2 agent**, which has no planner, was then measured on them.
Three runs each on `gpt-5.6-sol`:

| task | oracle | agent | range | |
|---|---|---|---|---|
| `three_switches_three_panes` | 9 | 9 | 9-9 | exactly optimal |
| `conditional_cleanup` | 5 | 5 | 5-5 | exactly optimal |
| `two_goals_two_panes` | 5 | 6 | 6-6 | one extra action |
| **total** | **19** | **20** | | **1.05x** |

Success was 10/10 on every task, observations stayed at one per run, and the
range on all three was a single value: zero variance across nine runs.

The nine-action task is the whole argument. Three sub-goals across three panes,
one of them two levels deep, solved in exactly the optimal sequence, three
times out of three, with no planner and no plan.

`conditional_cleanup` is the second half of it. Bluetooth starts on and
VoiceOver starts off; passing requires acting on the first and, having
navigated to check, correctly *not* acting on the second. Turning VoiceOver on
to "complete" the task fails the predicate. It passed every run.

## Decision

No explicit planner. `planner.py` is not written.

The arithmetic forecloses it. Two of the three long tasks are at 1.00x, and
nothing can beat an optimum. The entire headroom a planner could recover is the
**one extra action** on `two_goals_two_panes`, five percent of that task set,
against a planning call charged on every task.

That one action is not clearly an error either. The oracle crosses between
panes with a deep link; the agent navigates with a Back tap instead. Deep links
are exactly what `open_wifi_pane` injects as broken, because iOS 26 accepts
`App-prefs:root=WIFI` and ignores it, so the agent's route is the one that
survives a device that lies. Optimising it away would be optimising toward a
route this project has already been burned by.

Replanning is rejected on the same evidence. Its three intended triggers are
all in the task set and all already handled: `reach_accessibility` (Settings
opening on a stale pane) runs at 1.00x, `open_wifi_pane` (the dead deep link)
at 0.50x, and `enable_airplane_mode` (the dead switch) was resolved in S2 by
verification, which is a cheaper mechanism than a replanning loop.

## Alternatives rejected

**Build it and measure it.** The usual rigorous move, and it is unnecessary
here: a planner cannot improve on an optimum, and two of three tasks are at
one. Spending the work to demonstrate that arithmetic would be theatre.

**Adopt it anyway because the shape expects it.** This is the specific failure
CLAUDE.md names. A planner nobody can show a number for is a tutorial.

**Keep extending the task set until planning wins.** Tasks can always be made
harder until a mechanism looks necessary. That is fitting the benchmark to the
conclusion, and the conclusion would not transfer.

## Consequences

- One fewer model call per task, and no plan in the context window.
- The claim this phase can make is narrower and true: on tasks up to nine
  actions across three panes, including three failure modes taken from real
  hardware, an explicit planner has no measured room to improve. It is not a
  claim that planning never helps.
- The condition that would overturn this is stated rather than left open.
  Genuinely long horizons, on the order of twenty or thirty steps, or an
  unfamiliar third-party app where the route cannot be inferred from one
  screen. Neither is in the current set, and adding them is the honest way to
  reopen this decision.
- S3's slot in the plan is spent on the measurement rather than the mechanism,
  which is what the phase's own rule asks for.
