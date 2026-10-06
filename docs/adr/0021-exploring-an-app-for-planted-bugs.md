# 21. Exploring an app for planted bugs

Rejected, 2026-10-06, on criteria written, committed and pushed before any
model ran against the task, so that they could not be fitted to the result.
Self-evident recall was 60% against a bar of 80%.

## Context

Every task in the agent eval has a fixed goal and is judged on the device:
was the switch moved, was the screen reached. That measures the device layer,
and it is not the job people most often describe wanting an agent on a phone
for, which is closer to "tell me whether my app works".

One published number exists for that job. mobile-mcp
[#450](https://github.com/mobile-next/mobile-mcp/issues/450) ran a coding
agent over an eight-step Flutter signup with planted bugs and the goal "sign
up, get through onboarding, tell me anything that seems broken": 186 turns,
71 screenshots, 16.3M input tokens, $4.28 on an iOS simulator. About 80% of the
time went on checking whether the last action had done anything. Its observed
failures were a typed email losing its first character, a terms checkbox
below the fold, and a dead Done button.

A text-only agent cannot be expected to find every bug, and how many it can
find was estimated first. Bug-labelled issues from four open-source iOS apps,
[element-x-ios](https://github.com/element-hq/element-x-ios/issues),
[WordPress-iOS](https://github.com/wordpress-mobile/WordPress-iOS/issues),
[mastodon-ios](https://github.com/mastodon/mastodon-ios/issues) and
[firefox-ios](https://github.com/mozilla-mobile/firefox-ios/issues), 202 in
all, were classified by title in one pass by one person, so the counts are
rough. Of the 160 a person could reach through the UI, 112 (70%) are visible
without pixels and 48 are purely visual. But most of the behavioural ones are
bugs only to someone who knows what correct looks like; about a third announce
themselves on screen: a dead control, an error, a spinner that never ends, an
empty state, a screen that contradicts itself, success claimed for a failure.

So the question is narrow. Given no spec, does an agent that reads the
accessibility tree find the bugs that announce themselves? And does it call
working things broken?

## The fixture

Sprout, a six-screen onboarding app on the scripted device
(`tests/evals/agent/sprout.py`): Welcome, Account, Profile, Interests, Photo,
Review. Seven bugs are planted, chosen by class so that the result can be
read and not only counted:

| bug | class |
|---|---|
| Review's Done takes the tap and changes nothing | self-evident |
| Profile says "Profile saved"; Review says "Name: Not set" | self-evident |
| Choosing a photo shows "Uploading photo..." forever | self-evident |
| "N selected" is one more than the chips switched on | self-evident |
| The email field loses its first character (the #450 bug) | self-evident |
| The name field takes 38 characters | needs a spec |
| The Welcome caption is white on white | visual |

The terms switch is below the fold, as in #450: an obstacle, not a bug.

Two tasks share the app and the route. `explore_signup` states that names are
limited to 30 characters; `explore_signup_no_spec` does not. Both give the
same account details, including a 38-character name, and end: "report anything
that seems broken, one line per problem". Both start inside Sprout, with a
step budget of 60.

The oracle walks every screen in 15 actions and one observation, triggers each
bug, and writes the report a careful reader of the screen would. Its floors
are asserted by equality like every other task's.

Two things about the fixture are stated rather than hidden. Email addresses
are redacted by the policy layer, so the email bug arrives as a failed type
whose field reads `[redacted]`, not as the address with a letter missing. And
the visual bug is invisible to the tree by construction; it is there to
measure that a reader of the tree does not claim to see it.

## How a report is scored

Fixed in `tests/evals/agent/report.py` and tested in `test_sprout.py`.

- The report is the agent's own `done` summary, split into lines and
  sentences.
- A planted bug counts as reported when **one** line matches every pattern the
  bug carries: what was involved, and what was wrong with it. Done mentioned
  in passing beside a different complaint does not score the Done bug.
- A **false report** is a line that claims something is wrong and names no
  planted bug. The scorer lists candidates; each is read by hand and the
  reading is recorded with the result. A suggestion about style is not a
  failure. Calling a working control broken is.
- Every report is also read in full by hand. Where the reading and the
  patterns disagree, both are recorded, and the reading decides.

## The criteria

On `openai:gpt-5.6-sol`, the model the suite's other numbers were measured on,
three runs of each task, six in all:

1. **Self-evident recall at least 80%**, pooled: 5 bugs over 6 runs is 30, so
   at least 24 found.
2. **No more than one false report** across all six runs, after the hand
   reading.

Both hold: exploring for self-evident bugs is something this stack does, and
the README says so with the numbers. Either fails: finding bugs in an app is
a question of model judgement rather than of the tool, and it stays out of
scope.

Reported either way, without a bar: recall of the spec bug with the spec and
without it, which is the price of not having one; the visual bug, expected at
zero; turns, observations, actions and cost per run, beside #450's with its
caveats (a Flutter app, a different model and harness, and bugs not published
in full).

## The first attempt was void

The six runs first made on 2026-10-06 did not run what this record
describes, and are not the result. Two defects, both in the harness and
neither in the model:

- **The step budget was not 60.** The task raised the agent's turn budget,
  and a separate cap on actions, which `run_goal` never passed on, stayed at
  its default of 24. Five of the six runs ended "gave up after 24 actions".
  The action cap now follows the turn budget.
- **The account form did not look like one.** Its terms switch was absent from
  the tree until the page scrolled, with no scroll view around it, so nothing
  on the screen said there was more of it. A real UIKit form keeps an
  off-screen control in the tree, not visible, inside a scroll view the
  digest marks `scrollable`. The fixture now has that shape. The switch is
  still below the fold and still out of the digest until it is scrolled to;
  the obstacle is the same, the false absence of any sign of it is gone.

Building the second fix found a third defect, in the product: a scroll view
whose content is one large block, centred near its own centre, was merged
into that block and dropped from the digest. That is why the real Contacts
list showed no scrollable container either. Scroll containers no longer
merge with their content; the golden flows cost about 20 tokens more per
screen for it and all thirteen pass.

What the void attempt showed is kept, because it is honest about the agent
too: in every run it typed the account details, found Continue disabled, and
neither scrolled nor reported the page as possibly longer. Two runs reported
the disabled button as the bug.

The criteria, the bugs, the scoring rules and the model are unchanged.

## So was the second

The second attempt ran with both fixes and was void for a third defect, also
the fixture's: Sprout was not in the device's list of installed apps, to keep
other tasks' opening tokens unchanged. One run did what the goal said, tried to
open "the Sprout app", was told no such app was installed, and reported that.
The baseline suite then refused to record the set, by design, since a run that
never touched the device measures the infrastructure, and with it went the
other five reports: it writes once, at the end.

Sprout is now installed for the runs that use it and for no others, and the
exploratory runs go through `tests/evals/agent/run_exploration.py`, which
writes each run to disk as it finishes and marks an unusable one rather than
dropping the set. Unchanged again: criteria, bugs, scoring, model.

## Result

The third attempt, six usable runs on `gpt-5.6-sol`, scored by the patterns
and then read in full by hand:

| bug | class | found |
|---|---|---|
| Done does nothing | self-evident | 6 / 6 |
| "Profile saved", then "Name: Not set" | self-evident | 6 / 6 |
| count one more than the chips | self-evident | 6 / 6 |
| upload never finishes | self-evident | **0 / 6** |
| email loses its first character | self-evident | **0 / 6** |
| name over the stated 30-character cap | needs a spec | 3 / 3 with it, 0 / 3 without |
| caption white on white | visual | 0 / 6 |

**Self-evident recall: 18 of 30, 60%. Criterion 1 fails.** False reports: one,
within criterion 2's limit. So, as written before the runs: exploring an app
for its bugs is not a claim this stack makes, and it stays out of scope.

| | `explore_signup` | `explore_signup_no_spec` | mobile-mcp #450 |
|---|---|---|---|
| observations per run | 1, 1, 1 | 1, 1, 1 | 71 screenshots |
| actions | 33, 43, 36 | 39, 22, 27 | |
| turns | 40, 49, 43 | 42, 25, 31 | 186 |
| cost | $0.80, $1.10, $1.00 | $0.79, $0.30, $0.43 | $4.28 |

The comparison carries #450's caveats: a Flutter app, another model and
harness, and bugs not published in full.

**Where the reading and the patterns disagreed.** Three reports described the
count bug as "shows 3 when exactly two are enabled", with the number spelled
out, which the pattern's digits did not match. The reading decides, and they
are counted.

**The one false report.** "Typing appends instead of replacing" calls an
ordinary text field broken. The same line, and a line in another run, said
Delete inserted backspace characters; that was true of the fixture, whose
`FakeField` takes `\b` as a character where a real field deletes. It is a
defect of the fake and not counted against the agent.

**Why the two misses missed, which the rejection does not depend on.**

- *Upload:* the goal did not ask for a photo, and no run chose one. Every run
  skipped the photo screen, so the bug was never triggered. The agent
  explored the path the goal forced and not the branches off it.
- *Email:* the read-back reported the mismatch, but the policy layer redacts
  email addresses, so the field read `[redacted]` and Review showed the same.
  Nothing on screen let the agent see what was wrong, and it did not report
  the failed type. Redaction working as designed hid the evidence.

Every planted bug on the forced path was found in every run, at one
observation per run. That is the narrower claim the numbers support, and it
is a statement about verification, the one pillar the agent kept, rather than
about exploration.

**What would reopen it:** a goal or prompt that makes the agent exercise
optional branches, measured against these same bugs and criteria; or a
redaction rule that lets a read-back say *how* a redacted value differs
without saying what it is.

## Also found while building it

- A disabled control was unreachable by name. The digest showed `Continue
  disabled`, and a tap on "Continue" answered "nothing on screen matches",
  because resolution looks only at actionable elements and a disabled one is
  not. It now resolves, so the action can say it is disabled.
- A typed-text mismatch was recorded with a code no fault class knew, so the
  audit trail could not say whose failure it was. It is `text_mismatch` now,
  attributed to the device.
