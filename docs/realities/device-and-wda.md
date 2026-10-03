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
