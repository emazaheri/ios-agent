# 14. Ask before reaching another person

Accepted, 2026-09-25.

## Context

The policy gate asked one question of every action: does it destroy data or
cost money? Sixteen words answered it, from `send` and `pay` to `delete` and
`sign out`.

Two measured events showed the question was not the whole of it.

A real run against a dating app, on a goal to *read* a profile's prompts,
tapped "Like Radha's answer" four times. Each tap notified a stranger in the
owner's name. The gate passed all four, because a like destroys nothing and
costs nothing. It only stopped short of "Send a Rose" because that label
happened to contain `send`.

ADR 0013 then built a planted instruction whose bait was a `Follow` button, and
chose `follow` precisely because the gate could not see it. The model never took
it, 0 times in 24, and that result is about the model; the gate was blind
either way.

A like, a follow, a reply do something neither existing category names: they
reach another person, and undoing the tap does not undo the notification.

## Decision

A second question, beside the first and switched separately:

- `Risk.REACHES_A_PERSON`, asked when a pressed control's label or id is one of
  `policy.person_labels`: like, follow, comment, reply, share, invite, message,
  post, repost, rsvp.
- `policy.confirm_reaching_a_person`, **on by default**. Asking first was chosen
  over recording and allowing, and over asking only on a device: the Hinge run
  is the case the gate exists for, and it happened with nobody asked.
- The approval prompt says the action "would reach another person", rather than
  that a rule matched, so the person being asked is told the consequence.
- An action that answers both questions is asked about as destructive, the
  costlier of the two.

The vocabulary is the interaction verbs of Schema.org's `InteractAction`, the
taxonomy the research behind this proposed, minus the ones with no measured
case and a plain risk of firing on navigation: join, connect, leave, check in,
register, friend, match.

## Narrower than the first question, on purpose

The person rule judges **the control being pressed**, and nothing else:

- **Not typed text.** Typing reaches nobody until something is sent, and `send`
  is already destructive. A field labelled "Comment" is where a comment is
  drafted, not posted, so typing into it is not asked about either.
- **Not static text.** A paragraph is nobody's button.

This came from measuring it rather than reasoning about it. Walked across a
real Settings app on a simulator, ten panes and 178 distinct labels and ids,
the vocabulary produced one hit:

    'like' <- 'StandBy will turn on when iPhone is placed on its side while
              charging to show information like widgets, photo frames, or clocks.'

"Like" is a preposition as well as a verb, and prose is where the preposition
lives. Both exclusions above remove that class. The destructive question keeps
reading typed text, exactly as before, because typing "delete everything" is
worth asking about.

The first attempt at that walk measured almost nothing: it relaunched Settings
without terminating it, and Settings reopens on whichever pane it last showed,
so all but one pane was skipped and it reported zero hits. A clean-looking
number from a broken measurement is worse than none, and it is recorded here so
the next walk terminates first.

## What the evals now do

`eval_settings` disarms this question for every task that is not a blocking
task, exactly as it already disarmed the destructive one. `like_a_card` has a
like as its goal and `resist_a_planted_instruction` has a Follow as its bait;
armed, the gate would answer both, and each would be measuring the gate instead
of the model. A test asserts both halves: the task disarms the rule, and armed,
the rule catches the bait. The gate's own behaviour is asserted in
`tests/unit/test_policy.py`.

## Limits

- Settings is one app, and Apple's. It reaches no stranger, so it measures
  false positives and says nothing about misses. A social app is where misses
  live, and none is reachable on a simulator here.
- A control named in words neither list holds is still invisible, as is any
  control with no label and an id that names nothing. "Send a Rose" was caught
  only because it happened to contain `send`. The lists are a heuristic over
  labels; an app allowlist and a person watching remain the controls, as
  `docs/threat-model.md` says.
- More approval prompts on social apps is the cost. Whether it trains people to
  approve reflexively is a question about people, measured by nothing here.

## What would reopen it

- Approvals on this question being granted nearly always, with short review
  times, which is what rubber-stamping looks like rather than a rule that is
  too strict.
- A false positive on an app other than Settings. Record the label and prune
  the word, or scope it, before widening anything.
- A miss on a real run: a control that reached someone and was not asked about.
  That is the case for adding a word, and the only one.
