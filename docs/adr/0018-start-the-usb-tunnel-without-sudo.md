# 18. Start the USB tunnel without sudo; Wi-Fi stays on xcodebuild

Accepted, 2026-10-03.

## Context

A physical device's runner is launched two ways. Cabled, go-ios's `runwda`
asks testmanagerd for it over a go-ios tunnel, which the setup guide said
needed `sudo ios tunnel start`. On Wi-Fi, where go-ios cannot see the phone at
all, it is `xcodebuild test-without-building`, which needs Xcode at runtime.

The proposal was to launch through testmanagerd everywhere: no xcodebuild,
no sudo, one route for USB and Wi-Fi. The candidate was pymobiledevice3's
`developer dvt xcuitest --rsd`, over its `--native` tunnel, which piggybacks
the tunnel macOS already keeps to a paired phone and needs no root. The
simulator was left out from the start: it needs Xcode regardless, so the only
gain there would be startup time.

The gate, set before any code: 10 of 10 launches, no slower than the route
replaced, no sudo and no xcodebuild, and unaffected by `devicectl` discovery
running alongside.

## What was measured

An iPhone 17 Pro Max on iOS 26.6.1, launch until `/status` answers, 10
launches per row:

| route | where | success | launch |
|---|---|---|---|
| xcodebuild (classic) | Wi-Fi | 10/10 | 4.35 to 4.39s warm, 22.69s cold |
| pymobiledevice3, `--native` | Wi-Fi | 10/10 | 1.69 to 1.89s, plus a 1.85s tunnel per session |
| pymobiledevice3, `--native` | USB | 10/10 | 1.08 to 1.66s |
| go-ios `runwda`, userspace tunnel, fixed 3s wait | USB | 10/10 | 4.45 to 4.60s |
| go-ios `runwda`, userspace tunnel started on demand, no fixed wait | USB | 10/10 | **3.09 to 3.30s** (first 4.38s) |

The native route passed the gate as written, and was built as the default.
On hardware it then failed: after the phone was unplugged from USB, the native
tunnel refused six attempts in a row over about two minutes, while xcodebuild
launched over Wi-Fi in the same window. Probed port by port, every attempt was
a TCP reset at connect, 12 of 12, on the one RSD port macOS's `remoted` held.

## Why the native tunnel cannot be the default

pymobiledevice3 documents it (its network-stacks guide, and issue #1994, which
made `--native` opt-in in 11.20.0). The device treats every RSD connection
from one host address as a single peer and replaces the old connection with
the new one. On the native tunnel, pymobiledevice3 and `remoted` share that
address, so each evicts the other; commands fail intermittently, and after a
few rounds `remoted` stops redialing and Xcode loses the phone until it is
replugged. No handshake field changes this. The 10 of 10 were a stretch in
which ours happened to win.

The no-root alternative pymobiledevice3 recommends, its userspace tunnel, runs
over USB only, and its address exists only inside the pymobiledevice3 process,
so WebDriverAgent's HTTP port cannot be reached through it. Its kernel tunnel
reaches Wi-Fi but needs root, which is the thing being removed.

The gate missed this because it measured launches within one healthy stretch
and never a change of transport. A route over someone else's tunnel needs a
recovery criterion as well as a success count.

## Decision

- **USB:** when a cabled iOS 17+ device has no tunnel, the adapter starts
  `ios tunnel start --userspace`, which needs no sudo, and stops it at
  teardown. `goios.auto_start_tunnel` now defaults to on; `goios.tunnel_mode =
  "kernel"` keeps the old `sudo` route for anyone who wants it.
- **The fixed three-second wait is gone.** `_start_runner` slept three seconds
  to see whether go-ios exited, which was most of a 4.5s launch. The wait now
  polls for readiness and fails as soon as the runner exits, with its output.
- **Wi-Fi stays on xcodebuild.** No route without xcodebuild and without sudo
  is reliable there.
- No pymobiledevice3 dependency. go-ios already provided the no-root route.

Three defects found on the way are fixed in the same change:

- Discovery marked every cabled iOS 17+ phone as not ready, tunnel or no
  tunnel. It is now blocked only when no tunnel is up and none will be started.
- go-ios's runner output went to a pipe nothing read after the first three
  seconds, and go-ios logs for as long as the runner lives. It goes to a file.
- The runner bundle id defaulted to `com.facebook.*`, which no free account can
  sign. The Wi-Fi route never reads it, so a Wi-Fi-only setup failed 10 of 10
  launches the first time the phone was cabled. Left at the default, it is now
  read from the runner the build produced.

## Consequences

- The device integration tests pass over USB on the self-started tunnel, 3 of
  3, with no go-ios process left behind.
- A device snapshot, measured at about 3.7s in August, read Settings root in
  0.4 to 0.6s on the same phone during this work, through both launchers. ADR
  0016 compared against 3.7s; against 0.5s its rejection only gets stronger.
- **What would reopen the Wi-Fi half:** a no-root tunnel to a phone on Wi-Fi
  that does not share `remoted`'s host address, or a pymobiledevice3 release
  that carries its userspace tunnel over the network.
