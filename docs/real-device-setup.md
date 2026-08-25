# Driving a physical iPhone

Simulators need only Xcode. A real device needs signing, Developer Mode, and,
on iOS 17+, a tunnel. Run `uv run ios-mcp doctor` at any point; it checks each
of these and returns a remedy.

## 1. Install go-ios

```bash
brew install go-ios
ios version
```

`go-ios` handles device communication and launches WebDriverAgent through
`testmanagerd` without needing Xcode, which is also what allows a Linux host.

## 2. Prepare the device

1. Connect over USB and tap **Trust**.
2. Enable **Settings > Privacy & Security > Developer Mode** and reboot.
3. Confirm it is visible: `ios list`.

## 3. Start the tunnel (iOS 17 and later)

iOS 17 moved device communication from TCP to QUIC + RemoteXPC, so a tunnel
must exist before anything can reach the device. It needs root.

```bash
sudo ios tunnel start        # or: ./scripts/start_tunnel.sh
```

Leave it running. The server talks to its control API on `127.0.0.1:28100`
rather than starting one per session. `scripts/start_tunnel.sh` contains a
`launchd` plist for running it at boot.

## 4. Build and sign WebDriverAgent

```bash
security find-identity -v -p codesigning     # find your team id
TEAM_ID=XXXXXXXXXX ./scripts/prepare_wda.sh device
```

Two things this handles that are easy to get wrong:

- On iOS 17+, `testmanagerd` moved to `com.apple.dt.testmanagerd.runner`, and
  the runner must bind the device's own XCTest libraries rather than its
  embedded copies. The script strips `Frameworks/XC*.framework` for exactly
  this reason; without it the runner crashes on launch.
- **Signing expires.** A free Apple ID gets a 7-day profile, so automation
  stops working after a week with a confusing connection error. `ios_doctor`
  reports the expiry date and days remaining. A paid Developer Program
  membership gets a year and is the practical requirement for regular use.

Install it once: `ios install --path vendor/wda/WebDriverAgentRunner-Runner.app`.

## 5. Run

```bash
uv run ios-mcp doctor      # should now report "Ready to automate: real device"
uv run ios-mcp devices
```

The device is never the default target. `ios_open_session` prefers a simulator
even when a phone is connected, so acting on real hardware is always something
the caller asked for by name or UDID.

## What does not work on a physical device

| Capability | Why |
|---|---|
| `ios_set_permission` | `simctl privacy` is Simulator-only. Drive Settings, or answer the permission alert with `ios_handle_alert`. |
| appearance, location, status-bar overrides | Simulator-only. |

`ios://capabilities` reports this per session, so an agent can check rather
than discover it by failing.

## When it breaks

| Symptom | Cause |
|---|---|
| `tunnel_down` | The daemon is not running. `sudo ios tunnel start`. |
| `signing_invalid`, or WDA times out after a week | Profile expired. Re-run `prepare_wda.sh device`. |
| `device_not_ready` | Phone locked, untrusted, or Developer Mode off. |
| WDA crashes immediately on launch | Embedded `XC*.framework` copies not stripped. Re-run `prepare_wda.sh device`. |
