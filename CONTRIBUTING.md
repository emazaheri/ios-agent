# Contributing

Thanks for looking. This is a small project with a few opinions that are load
bearing, so it is worth ten minutes on this page before a first change.

## Getting set up

```bash
uv sync
./scripts/prepare_wda.sh simulator   # builds WebDriverAgent, once
uv run ios-agent doctor              # says exactly what is still missing
```

`doctor` names a remedy for every failure. If it is happy and something still
misbehaves, that is a bug worth reporting on its own.

## The loop

```bash
uv run pytest tests/unit tests/tui -q                        # ~40s, no device
uv run ruff check --fix . && uv run ruff format .
uv run mypy ios_mcp agent/ios_agent tui/ios_tui
```

Those three are what CI runs. They need neither a device nor a model: verified
by running them with `xcrun`, `xcodebuild`, `simctl`, `ios` and `open` all
replaced by a script that exits 127.

Between runs against a device, or after one that crashed:

```bash
uv run ios-mcp reset        # lists leftover WebDriverAgent processes; -y stops them
```

A runner nobody is holding keeps the device and the next run waits out
`wda.startup_timeout_s` before failing. `reset` claims a process only when its
`-xctestrun`, its `--bundleid` or its forwarded port ties it to
WebDriverAgent, so your own `xcodebuild test-without-building` is safe from it.

## Five things that are not style preferences

**No fake has ever caught a perception or lifecycle bug.** Every one came from
a real run against real hardware, and the list of them is in
[docs/realities](docs/realities/). If a change touches the digest,
resolution, or the device lifecycle, run it against a simulator before
believing it. `uv run pytest tests/integration`.

**The evals are the quality gate, not a pass/fail suite.** They report tokens
per step, wall time, action count and resolution-tier distribution. A drift
from `exact` toward `text-fuzzy` is the leading indicator that a flow is about
to become flaky. Agent tasks declare an **action floor**, the number a
hand-written oracle needs, asserted by equality so it cannot quietly become an
aspiration. Every run of the free series is recorded in
`tests/evals/history.jsonl`, and CI fails when one of those numbers moves
without the new line being committed with it. If the change is deliberate, the
failure prints the `scripts/eval_trend.py append` command that accepts it.

**Layers 1 to 4 must not import MCP, and nothing may import upward.**
`tests/unit/test_layering.py` enforces this statically. It is what lets an agent
framework import `IosSession` directly instead of paying a protocol round-trip,
and what keeps `ios-mcp` usable by an agent that is not the one shipped beside
it.

**A front end may not change what a run costs.** `tests/tui/test_cost.py` runs
one task wrapped and unwrapped and compares every counter by equality.

**Adopt a thing because a measurement demands it.** Three of four planned agent
features were rejected on their own numbers, each recorded as an ADR in
[docs/adr](docs/adr/) with what would reopen it. A proposal that names what it
would improve, and by how much, is one that can be settled.

## Looking at the terminal app

```bash
uv run python scripts/tui_screenshot.py
```

A passing test suite says nothing about what a TUI looks like. That script
renders every shape to `.artifacts/tui/` in about a second, and has caught eight
display bugs no assertion did. If you change the front end, look at it.

For behaviour a screenshot cannot show, tmux gives a real PTY:

```bash
tmux new-session -d -s ios -x 120 -y 34 -c "$PWD"
tmux send-keys -t ios 'uv run ios-agent manual' Enter
tmux capture-pane -t ios -p          # what is on screen
tmux send-keys -t ios C-q            # quit, releasing the device
```

## Tests

Helpers live beside the tests that use them rather than in the package:
`tests/fake_wda.py`, `tests/fake_device.py`, `tests/trees.py`,
`tests/evals/agent/screens.py`. `pythonpath` in `pyproject.toml` makes them
importable.

Write the test so it fails against the bug. Several tests in this repository
were written, passed, and were then found to pass against the very thing they
were written for, because they asserted on the wrong box or on stored text
rather than on what a person can see. Reintroducing the bug for a minute is the
only thing that tells the two apart.

## Commits

Say what was wrong, not what you typed. The diff already shows the second.

## Scope

Four things are deliberately out, and each has a reason rather than a backlog
entry.

**A consumer macOS app.** Shipping WebDriverAgent to users is capped at about
a hundred devices by provisioning, and `get-task-allow`, the entitlement that
makes WDA work at all, is the one App Store distribution forbids. Not a polish
problem.

**Automating your signing flow.** Feasible with an App Store Connect API key
and a dedicated keychain, and it is product plumbing rather than anything to do
with driving a phone.

**A cloud device farm.** A different business, and a capital-intensive one.

**Android.** A different accessibility stack end to end.

If a change only makes sense for one of those, it does not belong here.

## Releases

One version for all three distributions, one tag, one release. They were
numbered independently once and drifted within a single release: `ios-mcp` sat
at 0.1.1 with the other two on 0.1.0, under a repository-level tag that
therefore named none of them. Only `ios-mcp` is published, so separate numbers
bought nothing.

The version appears in five places. `tests/unit/test_version.py` checks four
of them against each other, and the release workflow checks them against the
tag, which is the one fact a test cannot see. `server.json` is the sharp edge:
it states the version twice, for the server entry and for the PyPI package it
points at, and a registry entry naming a version PyPI does not have is what
cost 0.1.1 in the first place.

```bash
# 1. gates, exactly what CI runs
uv run pytest tests/unit tests/tui -q
uv run ruff check . && uv run ruff format --check .
uv run mypy ios_mcp agent/ios_agent tui/ios_tui
uv run python scripts/eval_trend.py check .artifacts/evals/agent.json --suite agent-oracle

# 2. bump all three pyprojects and both server.json fields, then
uv sync                       # refreshes uv.lock; commit it with the bump

# 3. one commit, one annotated tag
git commit -am "Release 0.2.0"
git tag -a v0.2.0 -m "v0.2.0"
git push origin main --follow-tags
```

Pushing the tag runs `.github/workflows/release.yml`, which re-runs the gates,
refuses a tag that disagrees with `pyproject.toml`, and publishes `ios-mcp` to
PyPI through Trusted Publishing. There is no API token: the workflow mints a
short-lived OIDC credential, which is the difference between a secret that can
leak and one that does not exist. It needs a `pypi` environment on the
repository and a trusted publisher configured on PyPI for this workflow.

`ios-agent` and `ios-tui` carry `Private :: Do Not Upload`, so PyPI refuses
them even if a broad `uv publish` is run from the root.

Bump the minor while the project is 0.x whenever public API, agent behaviour
or a user-visible default changes; the patch is for fixes that change none of
those. Release notes come from the commit messages and the ADRs, which is why
both are written the way they are; there is no separate changelog to fall out
of date.

## Licence

By contributing you agree your work is licensed under the [MIT License](LICENSE).
