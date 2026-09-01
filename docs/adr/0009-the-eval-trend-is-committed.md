# 9. The eval trend is committed, and CI checks it rather than writing it

Accepted, 2026-09-01.

## Context

Every quality signal in this project was a per-run one. `tests/evals/harness.py`
names the leading indicator in its own docstring, a flow drifting from `exact`
toward `text-fuzzy`, and then throws the evidence away: reports land in
`.artifacts/`, which is gitignored, under a filename overwritten in place, with
no commit recorded and no schema version. Three report shapes accumulated there
with no reader.

So the ceilings caught a run that was already bad and nothing caught a run
getting worse. A digest change costing 5% more per screen passes
`MAX_TOKENS_PER_STEP = 900` from 320 tokens per step for a very long time.

## Decision

One append-only committed history, `tests/evals/history.jsonl`, one JSON object
per line, fixed key order. `scripts/eval_trend.py` appends a report to it,
prints the series, and compares a fresh report against the last recorded run
for its suite.

CI runs the one series that costs nothing (the oracle against a scripted
device: no model, no network, no hardware) and runs `check` against the
committed baseline. Hand-run slices, model-backed or against a simulator, go
into the same file under a different `suite` and are appended deliberately.

## The guard is exact, not a band

Every metric it checks is a count on a fixed route: observations, actions,
passes, refusals, runner recoveries, the tier histogram, the fault histogram,
and `device_tokens`, which is `len(json.dumps(payload)) // 4` over a scripted
accessibility tree. None of them has a model, a network or a clock in it.

A tolerance band on a deterministic number is a licence to drift, and this
repository has already taken that position twice: `test_agent_evals.py` asserts
the observation and action floors by equality, and `tests/tui/test_cost.py`
says it asserts by equality "in the spirit of the agent eval oracles".

`seconds` is recorded and printed and never checked, because it is the one
number the machine running the suite decides.

## CI compares, it never appends

Three reasons, in order of weight:

1. A bot commit destroys the mechanism. The value of a committed trend is that
   a person decided the new numbers were correct, in the commit that changed
   them. A workflow that appends automatically turns the history into a machine
   log that ratchets to whatever the code currently does, which is exactly the
   drift this exists to catch.
2. Pull requests from forks carry a read-only token, so a push step would work
   on `main` and fail or silently skip everywhere else. One workflow, two
   behaviours.
3. Pushing from CI fights the `cancel-in-progress` concurrency group and races
   between back-to-back merges.

The loop is therefore: change something, CI fails naming the metric and the old
and new values, decide whether that is a regression or the price of the change,
and if it is the price, run the `append` command the failure prints and commit
the new line in the same pull request.

## Consequences

A cost change is now exactly one added line in a diff, directly under the line
it replaces, with the numbers aligned column-wise because the key order is
fixed. Nothing is ever reformatted, so no change can produce a whole-file diff.

Both report writers gained a `schema_version`, and `flatten` refuses a version
it does not know rather than mis-reading it into a poisoned baseline.

## What would reopen this

A nondeterministic suite entering the guarded series. The model-backed slices
are deliberately outside it for that reason: their success rate and cost move
run to run, so guarding them by equality would fail constantly and guarding
them by a band needs a measured variance nobody has collected. If one is ever
brought in, the exact check has to become a band and this argument has to be
remade with that variance in hand.
