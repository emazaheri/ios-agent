# Check your own app

The job this is most often wanted for: you, or your coding agent, changed an
iOS app, and you want something to open it on a simulator and confirm the
change works, the way a person would, without writing a UI test first.

Every step below was run against a small SwiftUI app on an iOS 27 simulator:
a Name field, and a Save button that is disabled until the field has text.

## 1. Build for the simulator

Any simulator build of your app works. From an Xcode project:

```bash
xcodebuild -scheme YourApp -sdk iphonesimulator \
  -configuration Debug -derivedDataPath build build
# the app is at build/Build/Products/Debug-iphonesimulator/YourApp.app
```

A simulator build needs no signing.

## 2. Ask from your MCP client

With the server [connected](../README.md#connecting-your-own-agent-over-mcp), ask
in plain language, naming the `.app` path:

> Install build/Build/Products/Debug-iphonesimulator/YourApp.app on the
> simulator, open it fresh, type "Ada" in the name field, and confirm Save
> becomes enabled and that saving shows "Saved Ada".

The client will reach for these tools, in roughly this order:

| Step | Tool | What came back |
|---|---|---|
| Install | `ios_install_app` with `path` | `{"ok": true, "installed": ...}` |
| Open fresh | `ios_launch_app` with `fresh=true` | The first screen: `textfield =Name` and `button "Save" disabled` |
| Type | `ios_type` with `target="Name"` | `ok: true`, and the field read back as typed |
| Save | `ios_tap` with `target="Save"` | `screen_changed: true`, with `+ text "Saved Ada"` in the change |
| Confirm | `ios_wait_for` with `text="Saved Ada"` | `ok: true`, `'Saved Ada' appeared` |

Every action returns the screen it produced, so most checks need no separate
look. `disabled` in the first screen is the "before" half of the check, and the
added `Saved Ada` line is the "after".

A field with no label is matched by its placeholder (`=Name` above, resolved as
`value-exact`), so you can name it the way it reads on screen.

## 3. Or from the terminal

The bundled agent does the same from one line, once the app is installed:

```bash
xcrun simctl install booted build/Build/Products/Debug-iphonesimulator/YourApp.app
xcrun simctl terminate booted com.example.yourapp    # start from a clean launch
uv run ios-agent --app com.example.yourapp \
  "type Ada in the name field, save, and tell me whether it says Saved Ada"
```

`--app` brings the app to the front, but an app that is still running keeps
the state it had. Without the `terminate`, a second run found "Saved Ada"
already on screen from the first, and correctly reported success without
touching anything. That is right for a question and wrong for a check.

On the sample app the run took 2 actions, 1 observation and 15 seconds. It
ends with what the agent claims and, separately, what the device recorded for
each action. With `--no-tui`, for a script or CI, the exit code is non-zero
when the device contradicts the claim.

## Going straight to a screen

A deep link is usually the cheapest route to a screen deep in the app:
`ios_open_url` with your app's URL. The first time a custom scheme is opened,
iOS asks first:

```
alert  "Open in “YourApp”?"   buttons: Cancel, Open
```

and the app is not reachable until it is answered. Tap the button by name:
`ios_tap` with `target="Open"`. After that, the link opens straight through for
as long as the app stays installed.

## When the check fails

That is the point of running it, so it helps to know what failure looks like:

- **A tap did nothing**: `screen_changed: false` and an empty change. The
  control may be disabled, covered, or not wired up.
- **Typed text did not land**: `ok: false` with `text_mismatch`, and a `typed`
  entry saying what the field actually holds.
- **Text never appeared**: `ios_wait_for` returns `ok: false` with a note
  saying it did not appear within the timeout. It reports this as data rather
  than an error, so a check can branch on it.
- **The screen is nearly empty**: the view may be drawn rather than built from
  UIKit or SwiftUI controls, as in Flutter or a WebView. See
  [Troubleshooting](troubleshooting.md#what-the-agent-sees).

To make your app easier to check, give controls accessibility identifiers and
labels. The agent reads the same tree VoiceOver does.
