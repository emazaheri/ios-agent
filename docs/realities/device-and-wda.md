# Devices, WebDriverAgent, and what a snapshot costs

The device path, the runner that drives it, and the timings that shape every setting above it.

- **Simulators cannot launch WDA via `simctl`.** An `.xctrunner` aborts without
  an XCTestConfiguration, so it needs `xcodebuild test-without-building
  -xctestrun`. USB devices are the opposite: `ios runwda` drives testmanagerd
  directly, no Xcode needed.
- **go-ios is blind without a cable.** It speaks to usbmuxd, so `ios list`
  returns empty for a device on Wi-Fi. Discovery merges CoreDevice
  (`xcrun devicectl`), which also browses Bonjour.
- **WDA listens on the device itself.** Over Wi-Fi there is no tunnel and no
  port forward — connect straight to the address it announces in its log
  (`ServerURLHere->http://...`).
- **The first snapshot after backgrounding an app blocks for 61 seconds.**
  XCTest keeps waiting on the app that went away. `home()` activates
  SpringBoard immediately after, which drops it to about 5s.
- **A device snapshot was measured at ~3.7s** in August 2026, against under a
  second on a simulator, so `stabilize.max_wait_s` must exceed
  `stable_samples` snapshots or a real device times out on every action. In
  October the same phone read Settings root in 0.4 to 0.6s, over USB and
  Wi-Fi alike. The ceiling stays; the 3.7s is no longer the typical case.
  Later in October it was 0.7 to 1.4s over USB and 1.6s over Wi-Fi, so the
  cost moves from day to day and no setting should be fitted to one reading.
- **A screenshot costs a tenth of a tree read.** 0.10s on a simulator, 0.15s
  over USB and 0.17s over Wi-Fi, against 1.0, 1.3 and 1.6s. That is what lets
  `stabilize.signal = "frames"` watch the screen settle and read the tree once.
  See ADR 0019.
- **"Identical frames" is not "settled".** After typing into Settings search
  the screen holds still for 0.42s, then animates its results in; after that,
  the caret blinks through the same two or three frames forever. Only frames
  never seen before in the settle count as movement.
- **The USB tunnel needs no sudo.** `ios tunnel start --userspace` carried 10
  of 10 runner launches, and the adapter now starts it on demand. Launch to
  ready is 3.1 to 3.3s including the tunnel, down from 4.5s when go-ios was
  given a fixed three seconds to fail. See ADR 0018.
- **pymobiledevice3's `--native` tunnel is not usable for a session.** It
  rides macOS's own tunnel, and the device treats every RSD connection from
  one host address as one peer, so it and `remoted` evict each other. It
  launched 10 of 10 over Wi-Fi in one stretch, then refused six attempts in a
  row after a replug. Its no-root alternative is USB-only. Wi-Fi stays on
  xcodebuild.
- **A profile reissued after it expired asks for Trust again.** Xcode deletes
  an expired profile, so `prepare_wda.sh` finds no team to read (pass
  `TEAM_ID`), and the phone then refused the runner until the developer was
  trusted again under VPN & Device Management.
- **iOS 26 retired `prefs:` for `App-prefs:`.** Sub-pane URLs like
  `App-prefs:root=WIFI` return success and do nothing, and `App-prefs:root`
  does not reset Settings out of a sub-pane, so tests terminate the app.
- **`pageSourceExcludedAttributes` does nothing on `format=json`.** Appium
  documents it as the fix for expensive attribute computation. Measured: 750 ms
  with, 743 ms without, `isVisible` present either way. It is no longer sent,
  and reinstating it on the XML endpoint would strip `isVisible`, which the
  digest depends on.
- **The Accessibility Inspector service is not a cheaper tree.** It needs no
  signed WDA, but it reads by walking inspector focus one element at a time,
  at 60 to 80 ms each, with no frames, and it scrolls the screen to reach rows
  below the fold. Settings root took 3.3 to 4.4s against WDA's 3.7s. Its
  `perform_press` only works on debug builds. See ADR 0016.
- **An empty text field reports its placeholder as its value.** The Settings
  search field reads `value="Search"` until something is typed, so a
  read-back that took the value at its word would see text in an empty field.
- **Ctrl-A is not select-all on an iOS keyboard.** Sent through `/wda/keys` on a simulator,
  `\ue009a` arrived as two characters: "Airplane" became "Airplane\ue009aWi"
  instead of "Wi". `POST /element/{id}/clear` empties it, 0.34s on a
  simulator. `GET /element/active` names the focused field in 0.06s.
- **Reading a field back costs 0.4 to 0.5s on a simulator and 0.7 to 0.9s on
  a phone over Wi-Fi**, four calls on a 6s and a 12s type, measured on
  Settings search.
- **A 0.4s hold turned every scroll in Contacts into a long press.**
  WebDriverAgent's drag presses for its `duration`, then moves. Settings rows
  ignore a long press, so the golden flows scrolled fine for months; on a
  300-row Contacts list the list never moved and `scroll(until=...)` reported
  that it had ended. Holds of 0.15s and less scrolled every time, and scrolls
  now hold for 0.1s.
- **A 300-row list is cheap to read and slow to cross.** Contacts with 300
  seeded people: one observe is 166 raw nodes, 31 digest nodes and 438 tokens
  in 1.35s, because only the rows on screen exist in the tree. Scrolling to row
  250 took 25 scrolls and 192.6s on a simulator (173.5s settling on frames),
  each scroll a 3.2s drag call plus a settle of two tree reads. Search is the
  route to a known row; scrolling is the route to an unknown one.
- **A permission alert is SpringBoard's screen, and a tap clears it.** With
  Maps' location permission reset (`simctl privacy <udid> reset location`),
  launching Maps on an iOS 27.0 simulator returned the alert in the action
  result: "Allow “Maps” to use your location?", buttons Allow Once,
  Allow While Using App, Don’t Allow. The digest switches to
  `com.apple.springboard` with an `alert` node and the three buttons. A
  plain tap on Don’t Allow clears it, so an agent with no alert verb is
  not stuck; the apostrophe is the typographic one.
- **An exiting server took nothing with it.** Over stdio, an MCP client quitting
  is just stdin closing, and nothing called the pool's shutdown; a SIGKILL runs
  no Python at all. Either way, on a simulator, `xcodebuild` was reparented to
  launchd and the runner kept serving inside the simulator until `ios-mcp
  reset`. macOS has no parent-death signal, so the fix is a reaper outside the
  process: it reads a pipe whose write end dies with the interpreter, then stops
  the guarded process groups. Both cases now leave nothing, within 0.1s. On
  an iPhone 17 Pro Max over Wi-Fi, killing `xcodebuild` also stops the runner
  on the phone: its `/status` stopped answering in both cases. The USB route
  (`ios runwda`, the forward, the tunnel) uses the same code and is unrun.
