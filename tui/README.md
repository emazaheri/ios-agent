# ios-tui

A terminal front end for `ios-agent`. Installs the `ios-agent` command.

```bash
ios-agent doctor                        # is this machine set up
ios-agent devices                       # what is reachable
ios-agent "turn on bold text"           # a simulator
ios-agent --pick "turn on bold text"    # choose the device from a list
ios-agent --device "iPhone" --approve \
    --app com.apple.Preferences "turn wi-fi off"
```

Inside the app, `/device` (or `ctrl+o`) opens the same list at any point and
switches to whatever you choose, releasing the current device first. `--pick`
asks the same question before the first device is acquired.

Typed commands all take a leading slash: `/device`, `/log`, `/save`, `/quit`.
A goal is a sentence about a phone and a command is an instruction to the front
end, so the slash tells them apart without reserving English words; someone
whose goal genuinely is "device settings" can still ask for it.

The cursor starts
wherever `DevicePool.resolve(None)` points, which is the same device an
unattended run would take, so a physical phone is never pre-selected: reaching
one always costs a keystroke. Devices that cannot be driven are listed too,
dimmed and unselectable, with their blockers spelled out, because hiding an
unusable device hides the reason it is unusable.

The picker also appears on its own when `--device` matches nothing or matches
several things. That is a question rather than a dead end, and the answer is
on the list.

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

## Debugging it in a real terminal

The screenshots above render the app through Textual's own pipeline, which is
enough for layout but is not a terminal: no TTY, no terminal emulator, no
keyboard. For the real thing, run it inside tmux, which allocates a PTY of its
own and can be driven and read without a person at the keyboard.

```bash
tmux new-session -d -s ios -x 120 -y 34 -c "$PWD"
tmux send-keys -t ios 'uv run ios-agent manual --app com.apple.Preferences' Enter
sleep 45                                   # a cold simulator takes a while

tmux send-keys -t ios 'tap Accessibility' Enter
tmux capture-pane -t ios -p                # what is on screen, as text
tmux capture-pane -t ios -p -e             # the same, with colour escapes

tmux send-keys -t ios C-l                  # a key binding
tmux send-keys -t ios C-q                  # quit, releasing the device
tmux kill-session -t ios
```

This is the only way to check the things `run_test` cannot: that key bindings
reach the app, that colour is actually emitted, and that the layout survives a
real terminal at a real size.
