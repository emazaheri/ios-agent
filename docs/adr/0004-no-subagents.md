# 4. No subagents

Accepted, 2026-08-25.

## Context

Subagents were the plan's second pillar, and CLAUDE.md gave them the most
concrete justification of the four:

> Context isolation pays here because screenshots are expensive. A "find this
> element" subagent can burn tokens on annotated screenshots and return one
> ref, leaving the parent's context clean.

Both halves of that stopped being true during S1 and S2, and neither stopped
because of anything the pillar did.

## What the numbers say

Measured across 21 runs on `gpt-5.6-sol`, the S2 agent:

| | |
|---|---|
| prompt tokens per run | 5,864 |
| completion tokens per run | 239 |
| device tokens per run | 498 |
| longest task | 9 actions |

The context window is roughly **171 times** the largest run. There is no
context pressure, so there is nothing for context isolation to relieve.

The justification fails more specifically than that.

**The agent has never taken a screenshot.** `screenshot` was left out of the
eight-tool surface in S1, deliberately, on the grounds that a large confusable
tool set degrades tool selection and that a tool should be added when a task
fails without it. No task has. The expensive thing the subagent was meant to
contain does not happen.

**Finding an element already costs zero model tokens.** Resolution runs
server-side through six tiers, so a stale or fuzzy target is re-resolved inside
`IosSession` without a round trip. The "find this element" subagent's entire
job is done by layer 3, which is where this project put the value on purpose.

## Decision

No subagents. Nothing is built.

This is the same disposal as ADR 0002 and for the same reason: the outcome is
arithmetic rather than an open question. Isolation cannot reduce a context that
is already 0.6% of the window, and a subagent adds a model call per delegation
on top.

## Alternatives rejected

**Build it and measure it anyway**, for consistency with "measure every
pillar". Rejected because measuring is how you resolve uncertainty, and there
is none here. Spending a day and a couple of dollars to produce a foregone
negative is theatre, not rigour. ADR 0002 rejected planning the same way, on
the grounds that nothing beats an optimum.

**Add screenshots first, so the pillar has something to contain.** This is
backwards: it would introduce a cost in order to justify the mechanism for
relieving it. If a task ever fails for want of vision, the screenshot tool gets
added on that evidence, and the context question can be asked again then.

## Consequences

- Three of the four deep-agent pillars are now rejected with numbers, and one,
  verification, was kept with a measured 38% reduction in actions. That ratio
  is the honest outcome of building the measurement first, and is more useful
  than four implemented pillars would have been.
- What would reopen this: a task set where the agent needs vision, or one long
  enough that the transcript approaches the window. Neither is true at 5,864
  tokens and nine actions.
- The remaining unexamined risk is not a pillar. Every number in this phase
  comes from a scripted in-process fake, and this project's own testing
  convention says no fake has ever caught a perception-geometry or
  device-lifecycle bug. That is what the next slice addresses.

## Addendum, 2026-08-26: half of the reopening condition is now met

This record says vision gets added "if a task ever fails for want of it", and
that the decision is reopened by "a task set where the agent needs vision".

A real screen that the tree cannot describe has now been captured, which is
*not* the same thing, and the difference is worth keeping straight.

On a real Hinge Discover screen on an iPhone 17 Pro Max, a filter chip renders
the word **"Signals"**. Its accessibility label is
`discover_circleMembersFilter_accessibilityLabel` — the raw localisation key.
The word a person reads appears nowhere in the accessibility tree, so
`target="Signals"` cannot match at any of the six tiers, and no perception rule
can fix that: the string is not there to be found. `ios_screenshot` would show
it immediately.

What that establishes: the premise behind "the expensive thing the subagent was
meant to contain does not happen" is now contingent rather than settled. There
exists a real control reachable only by eye.

What it does not establish: no eval task has failed for it. No task in
`tests/evals/agent/tasks.py` asks the agent to tap that chip, so the agent has
still never taken a screenshot and the token arithmetic above is unchanged.
Writing a task that requires a control nameable only by sight, and watching it
fail, is the step that would finish the case. Until then this is an observation
with a date on it, not a decision.

The decision stands. The evidence file for reversing it is open.
