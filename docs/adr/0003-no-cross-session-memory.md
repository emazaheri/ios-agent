# 3. No cross-session memory

Accepted, 2026-08-25.

## Context

Memory was the plan's third pillar: learned app layouts and successful traces,
so an agent that has reached Wi-Fi settings once does not rediscover the route.

The S2 numbers narrowed it before anything was built. The agent sits at a
hand-written oracle's action floor on eight of ten tasks, so a remembered route
recovers nothing. The entire remaining gap across the set is five actions, all
of them on the dead-switch injection, where roughly six actions go on
discovering that a control accepts input and does nothing.

So the implementation remembered *failures* rather than routes, which was the
only version the numbers could support. Notes came from the verifier rather
than from the model, a single no-op was never recorded, and a control that
moved deleted its note immediately.

## What was measured

Nothing in the suite measured a repeat, because every task starts from a fresh
device and a fresh agent. A harness was added to run a task twice with notes
carried across, three trials per arm, on `gpt-5.6-sol`. Actions on the second
encounter:

| framing | second encounter | outcome |
|---|---|---|
| no memory (control) | 7 `[6, 13, 7]` | passes |
| hedged | 11 `[11, 16, 7]` | passes, worse than no memory |
| assertive | 3 `[6, 3, 0]` | **fails** |

`enable_bold_text`, the control task with no headroom, was 3 actions in every
run of every arm. Memory neither helped nor hurt it.

The **hedged** briefing said a control "may not work", told the agent the
screen outranks the note, and to ignore a note that turns out to be wrong. It
measured *worse* than remembering nothing. The reading that fits: a hedged note
does not remove the investigation, it motivates one. The agent is handed a
suspicion and spends actions confirming it.

The **assertive** briefing said to treat the notes as settled and not re-test
them. It was much faster, and one run in three took **zero actions**: the agent
read the note, concluded the switch was dead, and finished without touching the
device. Reproduced twice more directly, deterministically.

That run failed the suite, and it failed on a guard added in S2 for an
unrelated reason: an unachievable task only passes if the agent actually
attempted it. Without that guard this would have been recorded as memory making
the task three times cheaper.

## Decision

No cross-session memory. `memory.py` and its wiring are removed.

The two framings are not two attempts at one design, they are the two ends of
one dial, and the measurement says the dial has no good setting:

- Hedge it enough to be safe, and it costs more than not remembering.
- Assert it enough to save the work, and the agent stops checking the device.

The second is the serious one. A control noted as dead may have been fixed, the
app may have been updated, or the note may have been wrong when written. An
agent that skips the attempt reports a failure it did not observe. On a
simulator that is a bad number; on someone's phone it is a confident false
statement about their device.

That is the same principle the perception layer already enforces, where
`RefTable` exists so a stale ref is detected rather than trusted, and every
action re-reads the screen before acting rather than reusing coordinates from
the last observation. Remembered state that outranks live perception is the
thing this codebase has spent the most effort avoiding, and cross-session
memory is that idea with a longer time horizon.

## Alternatives rejected

**Tune the wording until it lands between the two.** The gap being aimed at is
between "worse than nothing" and "stops checking", against a metric whose
control arm has a range of 6 to 13 on three runs. There is no evidence a stable
middle exists, and prompt-tuning against a noisy metric until a number improves
is how a result gets manufactured.

**Keep it off by default.** A pillar nobody runs is not a pillar, and it still
has to be maintained and reasoned about. The eval numbers would come from the
path with it disabled.

**Remember routes instead of failures.** This was the plan's original idea and
the numbers foreclose it: eight of ten tasks are already at the floor, so there
is no route knowledge worth carrying.

## Consequences

- No agent state persists between sessions. Every run starts from what it can
  see, which is what the rest of the stack already assumes.
- The repeat-encounter harness goes with it. It exists in the commit below and
  is worth restoring if the question is reopened.
- The implementation, its eleven unit tests, and the harness are recoverable
  from commit `f55e31e`, "Build memory, and measure what it actually does".
- What would reopen this: a task set where first-encounter exploration is
  genuinely expensive, such as an unfamiliar third-party app with deep
  navigation, where the control arm would show real discovery cost to recover.
  On this set there is five actions of it in total.
- The S2 attempt guard turned out to be doing more than its own job. It is the
  only reason the assertive arm was recorded as a failure rather than as a
  threefold improvement, and it is worth keeping in mind that eval guards catch
  things they were not written for.
