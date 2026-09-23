# Simulator realities

Everything about getting a simulator to exist, boot, and show a window.

- **"No simulator" is two different problems.** Xcode ships without a runtime,
  and `xcodebuild -downloadPlatform iOS` is about 8 GB and several minutes. A
  runtime with no device created is the other half and is nothing: `simctl
  create` takes 0.2s offline and `simctl delete` undoes it. Worth detecting
  separately, because only one of them is reasonable to offer to fix. Pick the
  device type from the runtime's own `supportedDeviceTypes`; the flat `simctl
  list devicetypes` is ordered newest-first and includes types the runtime
  refuses, so taking its last entry reaches an iPhone 6s and fails with
  `Incompatible device`.
- **`simctl boot` starts the runtime, not the window.** A simulator booted this
  way runs headlessly and no window appears, so a person watching an agent
  drive a phone cannot see the phone. Opening it is idempotent, costs about
  70 ms, and does not steal focus.
  `IOS_MCP_SIMULATOR__SHOW_WINDOW=false` for CI.
- **Xcode 27 renamed the app, and `open -a Simulator` now fails outright.**
  `Simulator.app` no longer exists at any path; it is Device Hub,
  `com.apple.dt.Devices`, moved from `Contents/Developer/Applications` to
  `Contents/Applications`, with `SimulatorKit.framework` moved to
  `Contents/SharedFrameworks`. Flutter, Expo, React Native and Claude Code all
  filed the same bug. `_show_window` tries the bundle id first and the old
  name second, and warns rather than debugs when neither opens: it logged at
  debug for five days on a machine whose owner expected to watch the runs, and
  nothing said so. `doctor` has a `simulator-window` check for that reason.
