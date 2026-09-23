# 12. No per-app skill files

Accepted, 2026-09-23.

## Context

The same comparison against `browser-use` that ADRs 0010 and 0011 came out of
put its 97 `domain-skills/<host>/` directories on the list of things worth
taking. The case was a good one. Their LinkedIn file names the exact
`aria-label` formats for two buttons, warns that the formats differ, and
documents a trap in full so the next agent does not spend a budget
rediscovering it. This repository has the same artifact, hand-written, in
`docs/realities/`, and two of the three hardest entries in it are specific to
one app.

The claim being tested was not that markdown is tidy. It was that the system
improves per app without a code change.

ADR 0003 had already ruled on written-down knowledge once, and rejected it:
hedged notes measured worse than none, and assertive notes stopped the agent
checking the device. The distinction claimed here was that a skill file is
static, reviewed and constrained to how an app is shaped rather than to what
an attempt on it will do. That distinction is real, and it is not what the
measurement turned on.

ADR 0003 also named the condition for reopening the question: *a task set
where first-encounter exploration is genuinely expensive, such as an
unfamiliar third-party app with deep navigation, where the control arm would
show real discovery cost to recover.* The set had none, so one was built.

## What was measured

`set_quiet_hours` is an app nobody has seen, entered from outside. Its
notification screen is behind the third of three unlabelled glyphs on a tab
bar, and the app calls that screen Nudges. An oracle needs four actions. The
skill file names the tab and the word, in 118 tokens.

Two arms, three trials, `gpt-5.6-sol`
(`.artifacts/evals/agent-s10-app-skills.json`):

| | briefing off | briefing on |
|---|---|---|
| actions (floor 4) | **4, 4, 4** | **4, 4, 4** |
| turns | 6, 6, 6 | 6, 6, 6 |
| perception faults | 0 | 0 |
| passed | 3/3 | 3/3 |
| prompt tokens, median | 9,789 | 10,219 |
| cost | $0.168 | $0.172 |

`enable_bold_text`, the control with no headroom, was 3 actions and 5 turns in
every run of every arm, as it was in ADR 0003.

**The discovery cost the task was built to create did not exist.** The agent
walked a route it had never seen, with no labels on the control it needed, at
the oracle's floor, three times out of three, knowing nothing. There was
nothing for a briefing to save.

The mechanism is the one ADR 0004 found and ADR 0002 found before it: every
action returns the screen it produced, so a wrong turn is visible immediately
and costs one action to undo, and the digest keeps an unlabelled node that
carries an identifier, so the tab bar was readable. Discovery here is not
expensive because the stack already made it cheap.

## A briefing is not paid once

The design's whole cost argument was that appending the text to the `open_app`
result charges it to the run that opens the app and to no other. That is true
of the *briefing* and false of the *bill*: the text stays in the transcript
and is re-sent with every subsequent turn. 118 tokens cost **+430 prompt
tokens** on a six-turn run, 3.6 times its own size, which is what the +4.4%
prompt tokens and +2.3% cost above are.

## Decision

No per-app skill files. The loader, its wiring and the arm harness are
removed.

Rejecting it on 24 runs of one task would be thin if the arms disagreed at
all. They do not: every count on the task the briefing reached is identical
between them, against a floor the agent was already at. The only movement in
the whole report is on `like_a_card` and `read_a_card_answer`, which start
*inside* the app, never call `open_app`, and provably received no briefing
(`skill_tokens` is 0 in both arms). Those two moved by 30% of cost, by four
turns, and by half the perception faults, between two arms that were running
the same code. That is the noise floor on this suite, and it is larger than
anything the feature did where it acted.

## What is kept

- **`docs/realities/`**, the split of the hand-written list into seven files
  by mechanic. That was worth doing for the reader it already had, and it
  never depended on this result.
- **`set_quiet_hours`**, the task, and the Cards screens under it. It is the
  first task in the set whose route is not guessable from the first screen,
  and its value turned out to be the opposite of the one intended: it is the
  evidence that the route did not need guessing. It holds at 1 observation and
  4 actions and will fail if that stops being true.

## Alternatives rejected

**Keep it off by default.** ADR 0003's words, unchanged: a pillar nobody runs
is not a pillar, and it still has to be maintained and reasoned about.

**Write a harder app.** The task could be made adversarial until a briefing
wins: more tabs, a deeper tree, worse names. That measures the fixture. The
shape here was taken from habits seen on real third-party screens, and the
honest reading of a null on a realistic app is not "build an unrealistic one".

**Let the agent write the files.** This is the harness's actual claim and the
interesting version of the idea. It is also precisely ADR 0003: agent-written
notes about a device, unreviewed, outranking what is on screen. Nothing here
moves that.

## Consequences

- `run_goal` has no `skills` parameter and `Outcome` no `skill_tokens`. The
  eval report's conditional `skill_tokens` key goes with them.
- The implementation, its tests and the two-arm harness are recoverable from
  commit `132341e`, "Build per-app skills, and a task whose route has to be
  discovered".
- **What would reopen it:** a route whose wrong turns are not cheap. Every
  failure this stack has is recoverable in one action because every action
  returns a screen. An app where a wrong tap logs you out, starts a payment,
  or lands somewhere with no way back would make exploring expensive for the
  first time, and a note saying "not that one" would then be worth what it
  costs. Nothing in the task set is like that, and the policy gate exists so
  that the worst of it never happens unasked.
- One thing found in passing and left alone: the scripted device reports
  Settings as the foreground app whatever screen it is on, so the opening turn
  of the two card tasks says "Settings is already open" while the agent is
  standing in a third-party app. Fixing it changes what those runs are told
  and therefore what they cost, which is a measurement change and does not
  belong in the same commit as one.
