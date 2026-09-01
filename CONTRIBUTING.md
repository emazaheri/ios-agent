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

## Five things that are not style preferences

**No fake has ever caught a perception or lifecycle bug.** Every one came from
a real run against real hardware, and the list of them is in
[CLAUDE.md](CLAUDE.md#ios-realities-the-fakes-do-not-model). If a change touches
the digest, resolution, or the device lifecycle, run it against a simulator
before believing it. `uv run pytest tests/integration`.

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

Deliberately out, with reasons in [CLAUDE.md](CLAUDE.md#scope): a consumer macOS
app, automating your signing flow, a cloud device farm, Android. If a change
only makes sense for one of those, it does not belong here.

## Licence

By contributing you agree your work is licensed under the [MIT License](LICENSE).
