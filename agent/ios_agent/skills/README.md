# App skills

One file per app, named for its bundle id, under `apps/`. The agent loads the
matching file when it opens that app, once per run, and the text is appended to
the `open_app` result the model already reads.

## What belongs here

How this app is shaped, and how to reach a screen in it. Its own vocabulary,
where that differs from Apple's. Where something lives when the route is not
guessable from the first screen.

## What does not

**Outcomes.** Not "this switch does nothing", not "there is no way to do X".
ADR 0003 measured that framing: told assertively that a control was dead, the
agent stopped touching the device and reported a failure it had not observed.
One run in three finished with zero actions. The screen is the evidence, and a
note that overrides it is the one thing this stack has spent the most effort
avoiding.

**Workarounds for perception bugs.** If a control is unreachable because the
digest drops it, the fix is in `ios_mcp/perception/`, not in a file here.
`docs/realities/third-party-apps.md` is what happens when an app's habits get
encoded somewhere they cannot be tested.

**Length.** 1,500 characters, enforced. The run pays for every one of them.

## Shape

    # <App name> (<bundle id>)

    Written against <build or date>.

    <the notes>

`tests/unit/test_skills.py` checks all of the above against every file here.
