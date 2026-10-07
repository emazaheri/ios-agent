# The agent: what was kept, and what was rejected

The bundled agent was specified in the shape LangChain calls a deep agent:
planning, subagents for context isolation, a filesystem as memory, and a
system prompt. One rule governed the build:

> Adopt a pillar because a measured constraint demands it, never because the
> framework offers it. If a pillar cannot be justified that way, leave it out
> and say why.

Applied honestly, the rule rejected most of the design. The measurement was
built before the agent, which is the only reason any of this is knowable: a
set of goal-directed tasks, some injecting failures taken from real hardware,
each declaring the number of actions a hand-written oracle needs, and asserted
against that oracle so the floor cannot drift.

## The four pillars

| Pillar | Outcome | Evidence |
|---|---|---|
| Verification | **Kept** | Total actions 85 to 53 (-38%), cost $1.21 to $0.74, success unchanged at 21/21 |
| Planning | Rejected | Already at the oracle's floor on 8 of 10 tasks; the whole headroom on three long tasks was 1 action in 20. [ADR 0002](adr/0002-no-explicit-planner.md) |
| Subagents | Rejected | 5,864 prompt tokens per run against a window about 171 times larger, no screenshots ever taken, and element resolution already free on the server. [ADR 0004](adr/0004-no-subagents.md) |
| Memory | Rejected | A hedged note measured worse than no memory; an assertive one made the agent stop checking the device and fail. [ADR 0003](adr/0003-no-cross-session-memory.md) |

Verification, the one thing that helped, was not one of the four pillars. It
came from reading eval traces.

### Verification, kept

The first measurement put the agent at 1.08x the oracle on six of seven
tasks, and at 21 actions against a floor of 1 on the seventh. That task
injects a dead switch: the device accepts the tap, reports success, and never
moves. The agent retried until its step budget stopped it. The defect was
never navigation; the loop could not tell that the device was lying to it.

The obvious fix, checking that the switch reached the requested value, cannot
be done from the response. A dead switch and a switch already in the requested
state return identical payloads, because setting a value is state-aware and a
switch already on is correctly a no-op. So the verifier counts instead: an
attempt that has changed nothing twice should not be repeated, whether the
goal was already met or the device will not comply.

| | Before | After |
|---|---|---|
| Dead-switch task | 21 actions | 6 actions |
| All tasks | 85 actions | 53 actions |
| Other six against the oracle | 1.08x | 0.92x |
| Success | 21/21 | 21/21 |
| Cost | $1.21 | $0.74 |

21 runs on `gpt-5.6-sol`, recorded in commit `f493f94`. The prompt was left
untouched in the same change, so the difference belongs to verification alone.

## Two results that overturned the plan

**The agent already looked only once.** The skeleton was predicted to observe
before every move. It spent exactly one observation per run, the oracle's
floor, because every action already returns the screen it produced. The lever
the whole phase was designed around was at its limit before anything was
built.

**A guard that measured the wrong thing.** Observation overhead
(observations divided by actions) was meant to stay at or below 0.25. When
verification cut actions from 85 to 53, observations stayed at 21 and the
ratio rose to 0.40: making the loop more efficient made the efficiency metric
look worse. The guard is now absolute, one observation per run, and the ratio
survives only as a description. That was recorded rather than quietly
re-baselined, because a criterion edited to match its result is not a
criterion.

## Later ideas, measured and turned down

| Idea | What the measurement said | Record |
|---|---|---|
| Group several actions into one model turn | Invited to, the model did it in 0 of 24 turns, at +4.2% turns and +25% cost | [ADR 0010](adr/0010-no-multi-action-batching.md) |
| A briefing file per app | Changed no action count on a task built to reward it, and cost 2.3% more | [ADR 0012](adr/0012-no-per-app-skill-files.md) |
| Route routine turns to a smaller model | About half the cost, which tied its own bar rather than clearing it; kept as an option, off by default | [ADR 0015](adr/0015-route-routine-turns-to-a-small-model.md) |

One idea was kept on the same terms: `ios_find`, which reads the part of the
accessibility tree the digest leaves out. It moved actions from 1.25x to 1.14x
the oracle, entirely in the two tasks that use it, and costs about 190 prompt
tokens on every turn whether used or not. That price is why there is no
eleventh verb. [ADR 0011](adr/0011-a-find-that-reads-the-tree-the-digest-threw-away.md)

## The shape that survived

- A LangGraph loop with no planner, no subagents and no memory, over the same
  `IosSession` the MCP server uses. It is a peer of the server, not a layer on
  top of it. [ADR 0001](adr/0001-the-agent-is-a-peer-of-the-server.md),
  [ADR 0005](adr/0005-langgraph-core-not-deepagents.md)
- A verifier that stops an attempt the device is ignoring.
- The model provider as configuration rather than a dependency: two
  environment variables and an extra. See [Choose a model](../agent/README.md).

The current numbers for the whole task set are on
[Measured results](../README.md#measured-on-real-hardware), and every decision
is in the [decision records](adr/).
