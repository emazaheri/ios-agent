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
- **A device snapshot costs ~3.7s** against under a second on a simulator, so
  `stabilize.max_wait_s` must exceed `stable_samples` snapshots or a real
  device times out on every action.
- **iOS 26 retired `prefs:` for `App-prefs:`.** Sub-pane URLs like
  `App-prefs:root=WIFI` return success and do nothing, and `App-prefs:root`
  does not reset Settings out of a sub-pane, so tests terminate the app.
- **`pageSourceExcludedAttributes` does nothing on `format=json`.** Appium
  documents it as the fix for expensive attribute computation. Measured: 750 ms
  with, 743 ms without, `isVisible` present either way. It is no longer sent,
  and reinstating it on the XML endpoint would strip `isVisible`, which the
  digest depends on.
