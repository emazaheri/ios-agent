# Quickstart

From nothing to an agent changing a setting on a simulator, in about ten
minutes, most of it Xcode downloading. You need a Mac with Xcode 16.3 or later
and Python 3.12 or later; see [Installation](../README.md#requirements) for the
details.

## 1. Set up

```bash
git clone https://github.com/emazaheri/ios-agent && cd ios-agent
uv sync
uv run ios-agent quickstart
```

`quickstart` checks the toolchain and offers the repairs worth offering: it
creates a simulator if a runtime is installed but no device is, and builds
WebDriverAgent if it is missing, which takes about 20 seconds once. If Xcode
has no iOS runtime at all, it says so and stops, because that download is
about 8 GB: run `xcodebuild -downloadPlatform iOS` and start again.

It ends in manual mode.

## 2. Look around by hand

Manual mode drives the device with the same verbs the agent uses, with no model
and no API key. It is the quickest way to see what an agent would see. Press
`/` for the command menu; `ctrl+r` re-reads the screen.

The screen arrives as a digest, a few hundred tokens rather than tens of
thousands:

```
screen: com.apple.Preferences / "Display & Text Size"  fp=fd5ec5c2
e2   button       "Accessibility" id=BackButton @(38,84)
e3   switch       "Bold Text" =0 id=ENHANCE_TEXT_LEGIBILITY @(336,161)
e6   button       "Larger Text, Off" id=LARGER_TEXT @(190,216)
```

Each line is one element: a ref (`e3`), its role, its label, its value (`=0` is
off), its id, and where it sits. You and the agent act on refs or labels, never
on coordinates.

## 3. Give it a goal

The agent needs a model. Anthropic is the default:

```bash
uv sync --extra anthropic
export ANTHROPIC_API_KEY=...
```

or any other provider, with two variables; see
[Choose a model](../agent/README.md). Then:

```bash
uv run ios-agent --app com.apple.Preferences "turn on bold text"
```

The app shows the model's reasoning, the screen it is reading, and the running
cost. With `--no-tui` the same run prints plainly. This one is real, on an iOS
27 simulator:

```
  device : iPhone 17 (simulator, iOS 27.0)
  model  : openai:gpt-5.6-sol
  goal   : turn on bold text

    observe      -> 343 device tokens so far
    tap          Accessibility                                 6125ms
    tap          Display & Text Size                           3402ms
    set_value    Bold Text                                     3424ms

  what it cost
    3 actions, 1 observation(s), 1283 device tokens, 28.8s
    model: 9755 in / 155 out

  what it says
    succeeded: True
    Bold Text is turned on.

  what it did
     1. tap          button "Accessibility"                   changed=True
     2. tap          button "Display & Text Size"             changed=True
     3. set_value    switch "Bold Text"                       changed=True
```

What to read in it:

- **One observation.** The agent looked at the screen once. Every action hands
  back the screen it produced, so it never has to look again.
- **`set_value`, not `tap`.** A switch is set to a state, so asking for "on"
  when it is already on does nothing rather than turning it off.
- **Two accounts of the run.** "What it says" is the agent's claim. "What it
  did" is the device's record, one line per action with whether the screen
  changed. When they disagree, a `--no-tui` run exits non-zero.
- **The cost.** Device tokens are what the screens cost the model; the model
  line is the whole conversation.

## Next

- [Check your own app](check-your-app.md): install a build and confirm a change works.
- [Connect an MCP client](../README.md#connecting-your-own-agent-over-mcp): give Claude Code or Cursor the same tools.
- [Use a physical iPhone](real-device-setup.md): over a cable or Wi-Fi.
- [Troubleshooting](troubleshooting.md): when something does not work.
