# Driving a physical iPhone

Simulators need only Xcode. A real device needs signing, Developer Mode, and,
on iOS 17+, a tunnel. Run `uv run ios-mcp doctor` at any point; it checks each
of these and returns a remedy.

## 1. Install go-ios

```bash
npm install -g go-ios
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

Sign in to Xcode first (Settings > Accounts). Then open the WebDriverAgent
project once and pick your team under Signing & Capabilities on both the
`WebDriverAgentLib` and `WebDriverAgentRunner` targets:

```bash
open vendor/wda/WebDriverAgent/WebDriverAgent.xcodeproj
```

That GUI step is not optional. Signing in to Accounts alone creates no
certificate; Apple issues one only when a project first asks for a team, and
`xcodebuild` cannot do it because it does not share Xcode's authenticated
session (it fails with `No Account for Team`).

**Free Apple IDs need their own bundle id.** Apple refuses
`com.facebook.WebDriverAgentRunner` because someone else already registered
it, reporting it as *"cannot be registered to your development team"*. Change
the Runner target's Bundle Identifier to something unique such as
`com.yourname.WebDriverAgentRunner`.

**Authorize codesign once**, or every build stops on a keychain prompt per
framework, eight times over:

```bash
security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
  ~/Library/Keychains/login.keychain-db
```

Then build and install:

```bash
./scripts/prepare_wda.sh device        # finds your team and device
ios install --path vendor/wda/WebDriverAgentRunner-Runner.app
```

Finally, **trust the developer on the phone**: Settings > General > VPN &
Device Management > your Apple ID > Trust. iOS refuses to launch a
free-account app until you do, and the host only sees an opaque
`deviceprocesscontrolservice` error.

Tell the server which runner to launch:

```bash
export IOS_MCP_WDA__BUNDLE_ID=com.yourname.WebDriverAgentRunner.xctrunner
```

### Things that look like bugs but are not

- The build must target the device by id, not `generic/platform=iOS`. With a
  generic destination Apple never registers the phone, issues no profile, and
  the build fails claiming *"your team has no devices"*.
- Do not strip the embedded `XC*.framework` copies. Removing anything from a
  signed bundle invalidates its signature, and installation then fails with a
  bare `ApplicationVerificationFailed`.
- **Signing expires after 7 days** on a free Apple ID. Re-run
  `prepare_wda.sh device` and reinstall. `ios_doctor` reports the expiry date.
  A paid Developer Program membership gets a year.

## 5. Run

```bash
uv run ios-mcp doctor      # should report "Ready to automate: real device"
uv run ios-mcp devices
```

The device is never the default target. `ios_open_session` prefers a simulator
even when a phone is connected, so acting on real hardware is always something
the caller asked for by name or UDID.

## Over Wi-Fi, without a cable

Once a device has been paired over USB, it can be driven with no cable at all.
The server picks the route itself:

| | USB | Wi-Fi |
|---|---|---|
| Discovery | go-ios | CoreDevice (`devicectl`) |
| Runner launch | `ios runwda` | `xcodebuild test-without-building` |
| Reaching WebDriverAgent | port forward | straight to the phone's own address |
| Needs a RemoteXPC tunnel | yes | no |
| Needs Xcode | no | yes |

Nothing to configure: if go-ios can see the device, USB is used; otherwise the
network route is taken automatically. `ios-mcp devices` says which, and
`ios-mcp doctor` reports e.g. `1 device(s): 1 over the network`.

Two things make this work. go-ios talks to usbmuxd and cannot see an unplugged
device at all, so discovery has to go through CoreDevice, which also browses
Bonjour. And WebDriverAgent listens on the device itself, so once it is running
any host on the same network can reach it — the port forward exists only to
carry traffic over USB, and there is no USB here.

The runner announces the address it bound (`ServerURLHere->http://10.0.0.195:8100`),
which the adapter reads from the xcodebuild log. That is more reliable than
guessing the device's IP, since it is the interface WDA actually chose.

Wi-Fi is not slower in practice: a snapshot measured 2.6s over the network
against 3.7s over USB, because the cost is the accessibility traversal on the
device, not the transport.

### Pointing at a runner you manage yourself

```bash
export IOS_MCP_WDA__BASE_URL=http://10.0.0.195:8100
```

The server then connects to that WebDriverAgent instead of launching one, and
never tears it down. Useful for a device farm, a phone on another network, or a
runner you started by hand for debugging.

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
| `device_locked` | The phone slept. The session wakes it automatically, but cannot type a passcode. Set Auto-Lock to Never for long runs. |
| `signing_invalid`, or WDA stops working after a week | Free profile expired. Re-run `prepare_wda.sh device` and reinstall. |
| `device_not_ready` | Phone untrusted, Developer Mode off, or the runner not trusted under VPN & Device Management. |
| Launch fails with `deviceprocesscontrolservice` code 2 | The developer certificate is not trusted on the phone. |
| `ApplicationVerificationFailed` on install | The bundle was modified after signing. |

### Speed

A snapshot costs roughly 3.7s on a physical iPhone against well under a second
on a simulator, so each action lands around 8-12s. That is the accessibility
round trip, not traversal depth: raw tree size stops growing past
`snapshot.max_depth = 30` while wall time stays flat to depth 60.

One case is worth knowing about because it looks like a hang. The first
snapshot after an app is backgrounded blocks for 61 seconds: XCTest keeps
waiting on the app that went away. No WebDriverAgent setting avoids it, so
`home()` activates SpringBoard immediately afterwards, which drops the same
snapshot to about 5s. If you background an app by some other route and the
next call stalls, that is why.
