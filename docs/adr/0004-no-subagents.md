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
