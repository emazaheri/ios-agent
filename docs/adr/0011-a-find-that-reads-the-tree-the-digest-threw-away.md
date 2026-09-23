# 11. A find that reads the tree the digest threw away

Accepted, 2026-09-20.

## Context

The same comparison against `browser-use` that ADR 0010 came out of put its
zero-cost queries on the list of things worth taking, and predicted this one
would be rejected:

> Given the observation floor, the honest prediction is that it does not
> [change anything], and it becomes a rejected feature with an ADR.

The prediction was wrong, and the reason it was wrong is worth more than the
feature.

Two facts had to be established before anything was built.

**A filtered observation already existed.** `build_digest` takes `query=` and
the MCP tool exposes it (`perception/digest.py:227`, `server/tools_perceive.py:23`).
Searching the digest was therefore already shipped, so a find had to be
something else or it was a duplicate. Reading `browser-use`'s source settles
what: `search_page` walks the live DOM with a `TreeWalker`
(`tools/service.py:202-262`), not the serialised tree the model was shown. It
reaches what the serialiser dropped. That is the variant that did not exist
here.

**ADR 0004 had already ruled on finding elements**, on the grounds that
resolution runs server-side through six tiers and so costs zero model tokens.
That ruling stands, and it is why this is not a find that returns refs.

## What the measurement said before the feature

The deciding evidence came from attributing the S7 faults rather than from
reading `browser-use`. All 20 perception faults in
`.artifacts/evals/agent-s7-before.json` fall on two tasks, `read_a_card_answer`
and `like_a_card`. The other eleven have none.

Reproduced offline against the same scripted screen, the mechanism is sharper
than "the digest dropped something":

    actionable pool: ['e4', 'e6']
    pool nodes carrying any prose: []

Every tappable element on that screen is an unlabelled image carrying only an
accessibility id. `resolve._prose` excludes identifiers deliberately, so the
`closest` list in an `element_not_found` is empty by construction, and **every
miss on that screen tells the agent nothing about what it missed**, however
close its guess was. `faults._not_found` then attributes the miss to
perception, correctly.

That is not a resolution problem. Resolution behaved exactly as designed. It is
that nothing in the tool surface could answer "what is actually on this screen,
under the names it really has".

## Decision

`ios_find` is kept. It searches the raw accessibility tree before noise
removal, dedupe, collapsing and the token budget, and tags each match `shown`
or `hidden`.

Three properties are load-bearing:

- **It returns no refs.** `RefTable.update()` is called only when a digest is
  returned to the agent, because that memory is what detects a ref pointing at
  a different element than the agent was shown. Handing refs back would force
  an update and make a find an observation wearing a different name. A caller
  acts on a result by passing the reported label or id as an ordinary target,
  which the six tiers already handle.
- **It is counted apart from observations.** The oracles never call it, so
  every floor asserted by equality in `test_agent_evals.py` is untouched, and
  `finds` is its own committed metric.
- **`shown` is judged under the caller's own budget.** See below; this was
  wrong twice and hardware caught it both times.

## The numbers

`gpt-5.6-sol`, 13 tasks x 3 runs. S8 is the feature with a prompt paragraph
that named it after a refused tap; S9 is the same feature with that paragraph
scoped more tightly and shortened by a line.

| | s7-before | s8-find | s9-find-prompt |
|---|---|---|---|
| success | 39/39 | 39/39 | 39/39 |
| observations (floor 39) | 40 | 41 | 41 |
| finds | 0 | 11 | 4 |
| actions | 135 | 127 | **123** |
| turns | 238 | 236 | **216** |
| perception faults | 20 | 11 | **6** |
| model faults | 2 | 4 | **0** |
| tiers | exact only | **text-fuzzy 1** | exact only |
| cost | $2.01 | $2.14 | **$1.87** |
| wall clock | 500s | 426s | **399s** |

S9 against S7: **-8.9% actions, -9.2% turns, -6.8% cost, -20% wall clock, and
70% fewer perception faults**, at unchanged success.

### The gain is where the feature is used, and only there

The obvious objection is that S9 changed the prompt as well as adding a verb,
so the gain could be the prompt. Splitting the suite by whether a task ever
called `find` answers it:

| | turns | actions |
|---|---|---|
| the 2 tasks that called `find` | 43 -> 27 (**-37%**) | 10 -> 3 (**-70%**) |
| the 11 tasks that never did | 195 -> 189 (-3%) | 125 -> 120 (-4%) |

The eleven tasks that never call it barely move, and what movement there is
sits almost entirely in `enable_airplane_mode`, the dead-switch injection that
was already the noisiest task in the set (28, 22 turns). `like_a_card` reaches
its oracle action floor in **all three runs**, not on a median, having taken
2, 5 and 1 actions before.

### It costs a fixed tax whether used or not

The sharpest number here, and the one that generalises past this feature.
Across the tasks that never called `find` in either slice, prompt tokens per
turn:

    s7 1307   ->   s8 1521 (+16.4%)   ->   s9 1498 (+14.6%)

A tenth verb costs about 190 prompt tokens on **every turn of every run**,
including the ten tasks it never helps. Shortening its prompt paragraph by one
line recovered 23 of them; the rest is the tool definition itself.

This is the eight-verb surface's own argument, now with a price on it. The tax
is paid for here only because the turn count fell 9.2%, and a verb that did not
earn its keep somewhere would show up as pure loss. `tools.py` says a tool is
"deliberately absent until a task fails without them, so that adding one is a
decision with a number behind it". 190 tokens a turn is that number.

### One regression, kept in the record

S8's prompt told the agent to call `find` when a tap reported nothing matching.
On `read_a_card_answer`, whose answer is readable from the first observation
and whose `action_floor` is 0, that turned a wasted tap into a wasted search:
turns 12 -> 17 across three runs, cost $0.093 -> $0.159, five finds. The agent
had been given a new way to procrastinate on a screen it could already read.

Adding one clause, that a screen already showing what was asked for should be
answered from rather than searched, took it to 10 turns and one find, below
where it started. S8 stays in `history.jsonl` so the regression is on the
record rather than tuned away quietly.

### What is still above floor

Observations are 41 against a floor of 39, and the whole excess is
`like_a_card` spending 5 across three runs where the oracle needs 3. The agent
re-reads the screen after a find, which is reasonable given a find deliberately
is not a screen and does not update `last_screen`. One observation of the two
was already there in S7. Not fixed, not hidden.

## The alternative that was not measured

`resolve._prose` excludes identifiers, which is why `closest` was empty on the
screen that motivated all of this. **Adding ids to that hint is roughly one
line, carries no prompt tax at all, and would also have told the agent what it
missed.** It is untested.

It is named here because this record would otherwise claim `find` beat nothing.
The two are not equivalent: a hint can only describe elements the digest kept,
so it could not report a `hidden` match at all, and every case in `docs/adr/0007`
would stay unanswerable. But on *this* screen, where the elements were present
and merely unnameable, it might well have been enough. Separating the two
effects needs a third paid slice, and at a predicted-negative feature's price
that was judged not worth it.

Anyone reopening this should run the one-line version first.

## What hardware caught that the fakes did not

Three defects, none of which any fixture reproduced. Recorded because
`docs/realities/` already claims no fake has ever caught a perception-geometry
bug, and this is three more.

1. **`shown` was decided against the wrong digest.** `find` built its own at
   default settings, so a caller who observed at `budget=180` was told all five
   dropped elements were `shown`: true of the digest find had just built, false
   of the one they were looking at. Telling someone they can name what they
   were never given is the one direction this result must not be wrong in.
   `budget` now travels with the question.
2. **`shown` was a property of the query, not the element.** It asked whether
   any digest node contained the *needle*, so searching "Contact 0" let one
   surviving row vouch for ten. It now compares the element's own matched field.
3. **Containment alone is not reachability.** At a tight budget real Settings
   drops the row labelled `Siri` while `Optimizing Search and Siri` survives
   468 points up the screen. Containment called the dropped row reachable, and
   an agent targeting "Siri" then resolves by substring onto the wrong row.
   The rule is now: same id, or the same text exactly, or text contained **at
   overlapping coordinates**. Exact-match keeps the Airplane Mode echo working,
   where `_dedupe_colocated` legitimately moves the geometry; the overlap
   clause refuses the Siri coincidence while keeping a button's child text.

## What would reopen this

**The tax, against a bigger verb set.** 190 prompt tokens a turn is affordable
at ten verbs. It is the same argument against the eleventh, and the twelfth,
and nothing here licenses them.

**A screen where `find` cannot help either.** Two of the three shapes recorded
in `docs/realities/third-party-apps.md` are answered by this. The third is not: a Hinge filter chip reads
"Signals" while its label is the raw key
`discover_circleMembersFilter_accessibilityLabel`. No search of the tree can
find a word the tree does not contain. That remains an argument for the
screenshot path.

**The one-line hint.** If it turns out to recover most of the gain, the
honest conclusion is that a tenth verb was bought at 190 tokens a turn for
something a hint could do free, and this record should be revisited rather than
defended.
