# Troubleshooting

Start with the doctor. It checks the toolchain, the simulator runtimes, the
device tunnel, WebDriverAgent's build and signing, the devices and the model,
and gives a remedy for each failure:

```bash
uv run ios-agent doctor        # or: uv run ios-mcp doctor
```

```
12 ok. Ready to automate: simulator, real device.

  [PASS] xcode: Xcode 27.0 at /Applications/Xcode.app/Contents/Developer
  [PASS] simulators: 11 simulator(s) on iOS 27.0
  [PASS] wda-bundle: simulator bundle ready, device runner ready
  [PASS] model: openai:gpt-5.6-sol
  ...
```

The terminal app runs the same checks before it touches a device, so a Mac
that is not set up is told so in about a second rather than after a simulator
has booted. If the doctor passes and something still fails, find the symptom
below.

## Setting up

| Symptom | Cause and fix |
|---|---|
| `xcode` fails | Only the Command Line Tools are installed. Install the full Xcode, then `sudo xcode-select -s /Applications/Xcode.app`. |
| `simulators` fails: no runtime | Xcode ships without one. `xcodebuild -downloadPlatform iOS`, about 8 GB. |
| `simulators` fails: a runtime but no device | `ios-agent` offers to create one, which takes under a second; or `xcrun simctl create`. |
| `wda-bundle` fails | WebDriverAgent is not built. `./scripts/prepare_wda.sh simulator`, or `ios-agent quickstart`, which builds it in about 20 seconds. |
| `model` warns | No credential this project can see. Bedrock, Vertex and an `ant auth login` profile resolve their own, so a warning is not always a problem. `manual` mode needs no model at all. See [Choose a model](../agent/README.md). |
| The simulator runs but no window appears | Expected with `IOS_MCP_SIMULATOR__SHOW_WINDOW=false`. On Xcode 27 the window is Device Hub, not Simulator.app; automation does not need it either way. |

## A run that stalls or times out

| Symptom | Cause and fix |
|---|---|
| The first run after a crash times out waiting for WebDriverAgent | A runner is still holding the device. Runners stop with the process that started them, even when it is killed, so this one was started some other way. `uv run ios-mcp reset` lists it and `-y` stops it. |
| One call blocks for about a minute after leaving an app | The first snapshot after an app is backgrounded waits 61 seconds on the app that went away. `home` avoids it; another route to the home screen may not. See [device realities](realities/device-and-wda.md). |
| Every action on a phone takes 8 to 12 seconds | Normal: a tree read costs seconds on a phone against under one on a simulator. `IOS_MCP_STABILIZE__SIGNAL=frames` settles on screenshots instead, 17 to 22% faster on a phone ([ADR 0019](adr/0019-settle-on-frames-as-an-option.md)). |
| `session_halted` | The gate stopped the session: five failed actions in a row, or the screen cycling between the same few states. Read the reason in `ios_session_status`, then `ios_resume`. |

## A physical iPhone

| Symptom | Cause and fix |
|---|---|
| The phone is not listed | Unlock it, tap Trust, and turn on Developer Mode under Privacy & Security. `ios-agent devices` says what is missing for each device. |
| `tunnel_down` | No tunnel, and none could be started. Unlock the phone and check the cable, or run `ios tunnel start --userspace`. |
| `device_locked` | The phone slept. A session wakes it but cannot type a passcode; set Auto-Lock to Never for long runs. |
| Works for a week, then `signing_invalid` | A free Apple ID's profile lasts seven days. Re-run `./scripts/prepare_wda.sh device`. A profile reissued after it expired asks for Trust on the phone again, and may need `TEAM_ID` passed explicitly. |
| `ApplicationVerificationFailed` on install | The runner bundle was changed after it was signed. Rebuild it rather than editing it. |
| Launch fails with `deviceprocesscontrolservice` code 2 | The developer certificate is not trusted on the phone: Settings, General, VPN & Device Management. |

The full device walkthrough is [Use a physical iPhone](real-device-setup.md).

## What the agent sees

| Symptom | Cause and fix |
|---|---|
| A screen comes back nearly empty | Flutter canvases and WebViews have no accessibility tree. The digest carries a note saying so; use `ios_screenshot` for that screen. [ADR 0007](adr/0007-report-unreachable-content-rather-than-reaching-it.md) |
| `element_not_found` for something visible | The digest dropped it to stay within budget, or it has no label. `ios_find` searches the full tree; `ios_screenshot(annotate_refs=true)` shows every ref on the picture. |
| `element_stale` or `element_ambiguous` | The screen moved since the agent looked. The error lists the closest candidates; observe again and act on a current ref. |
| Typed text comes back `ok: false` with `text_mismatch` | The field holds something other than what was typed, which the read-back caught. The `typed` entry says what it holds. |
| `action_requires_approval` | The gate is asking. See [Approvals and secrets](approvals-and-secrets.md). |

Every error carries a `code`, a `hint` and often `details`; the codes are
listed in the [tool reference](tool-reference.md#errors).
