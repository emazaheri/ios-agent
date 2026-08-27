# ios-tui

A terminal front end for `ios-agent`. Installs the `ios-agent` command.

```bash
ios-agent doctor                        # is this machine set up
ios-agent devices                       # what is reachable
ios-agent "turn on bold text"           # a simulator
ios-agent --device "iPhone" --approve \
    --app com.apple.Preferences "turn wi-fi off"
```

## Why this is its own distribution

`ios-agent` is held to a seven-module public surface of `ios-mcp`, asserted in
`tests/unit/test_layering.py`. A front end needs two modules outside it: device
discovery, to offer a picker, and the doctor, to explain a machine where
nothing will run. Widening that surface to buy a front end a device list would
loosen an invariant for a reason unrelated to why it exists.

So the front end sits beside the agent rather than inside it, and the
dependencies point one way: `ios-tui` -> `ios-agent` -> `ios-mcp`. See
`docs/adr/0008-the-front-end-is-a-third-distribution.md`.

## The split inside the package

Half of this package never imports Textual:

| module | |
|---|---|
| `events.py` | what happened, as frozen values |
| `bus.py` | `EventSink`, a queue, a list for tests |
| `stream.py` | `EventBackend`, and the streaming model factory |
| `progress.py` | device startup, bridged from `logging` |
| `runner.py` | pool, session, `run_goal`, stop |
| `printer.py` | the plain-text consumer, `--no-tui` |

`app.py`, `widgets.py` and `approval.py` are the Textual half. The split is
what makes the interesting part testable without a terminal, and
`tests/unit/test_layering.py` enforces it rather than trusting it.

## The rule this package is held to

**A front end may not change what a run costs.** `EventBackend` wraps a
`Backend` and emits around it, adding no await of its own and never touching
the device. `tests/tui/test_cost.py` runs the same scripted task wrapped and
unwrapped and asserts the counters equal, in the spirit of the agent eval
oracles.

## Looking at it

```bash
uv run python scripts/tui_screenshot.py       # .artifacts/tui/*.png
```

Renders each shape against the scripted device: no simulator, no model, no API
key, about a second. It exists because a green test suite says nothing about
what a terminal app looks like, and the first run of it found two bugs every
test was passing through: an empty transcript pane, and a header that
recoloured the screen below it.
