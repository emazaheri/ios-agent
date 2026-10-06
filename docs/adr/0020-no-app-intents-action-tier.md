# 20. No App Intents action tier

Accepted, 2026-10-05.

## Context

Apps increasingly declare what they can do as App Intents, for Siri,
Shortcuts and Apple Intelligence. The proposal was a resolution tier above
`exact`: where an app declares an intent for what the agent wants, invoke it
instead of tapping through the screens that lead there. It is the most
"agentic" lever available, since it acts on meaning rather than on pixels.

The governing rule set the bar before anything was built: adopt it only if an
eval task shows taps are the bottleneck. The spike asked two narrower
questions as well. Can the intents be found from outside the app, and can one
be invoked?

## What was found

**Finding them works, on a simulator only.** An app that declares intents
ships `Metadata.appintents/extract.actionsdata` in its bundle: JSON naming
each intent, its parameters, whether it opens the app, and the Siri phrases of
its App Shortcuts. The iOS 27.0 simulator runtime carries 76 intents across 13
apps, and Siri phrases for 5 of them in 4 apps. A phone's system app bundles
cannot be read.

- **Settings declares none.** It is the app nearly every eval task runs in.
- The system-wide intents are launch an app, close an app, and show the home
  screen, which `launch_app` and `press_button` already do.

**Invoking them does not work.** There is one route from outside the app
that needs no user setup: WebDriverAgent exposes XCTest's `XCUISiriService`
at `POST /session/{id}/wda/siri/activate`, which types a request to Siri.

| where | phrase, from the app's own metadata | what happened |
|---|---|---|
| simulator, iOS 27.0 | "Create a new reminder in Reminders" | blocked 35.4s, reported success, changed nothing |
| iPhone 17 Pro Max, iOS 26.6.1 | the same | 0.6s; Siri's own reminder dialog asked what to remind about; the app never opened |
| the same phone | "Open Favorites in Photos" | 0.4s; Siri read out the iPhone User Guide's tap steps for finding Favorites |

Typed Siri sends a phrase to its own domains and to general knowledge before
an app's App Shortcuts, and the call reports success whichever it chose. A
tier that cannot tell whether it acted is worse than none, because the
verifier would have to observe anyway, and the taps it was meant to replace
would follow.

The other routes need someone first. `shortcuts://run-shortcut` runs only a
shortcut the user already made. Spotlight lists App Shortcuts, but reaching
one there is a search and a tap, which is the tap route through a different
screen.

**The rule fails on its own terms.** Actions run at 1.14x the oracle, and the
excess comes from perception faults, not from routes that are long. The one
task built to need outside knowledge, `set_quiet_hours`, ran at the oracle's
floor (ADR 0012). No eval task runs in an app that declares an intent.

## Decision

No App Intents tier, and no Siri tool. Nothing was added to the code.

## Consequences

- Actions stay taps, typing and gestures, resolved against the tree. Where an
  app supports a deep link, `open_url` already skips the route.
- The bundle metadata reader was a scratch script and was not kept. It would
  matter only alongside an invocation route that works.
- **What would reopen it:** a way to invoke a named intent from outside the
  app that reports which intent ran, without routing through Siri's own
  domains; and an eval task, in an app that declares the intent, where the
  tap route is long enough to be the measured bottleneck. Either alone is not
  enough.
