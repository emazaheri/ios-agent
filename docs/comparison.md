# How it compares

Several open-source projects let an AI agent use an iOS device. They answer
different questions, and the right one depends on what you need. This page
says where ios-agent is ahead, where it is level, and where another tool will
suit you better.

Written in October 2026 from each project's README and public issue tracker,
not from running them. Projects move quickly; check their own pages before
deciding.

## The short version

| If you need | Look at |
|---|---|
| An agent that uses iOS UI and can tell when a step did not work, on a simulator or a real iPhone, with an approval gate | **ios-agent** |
| The same tools across iOS and Android | [mobile-mcp](https://github.com/mobile-next/mobile-mcp), [agent-device](https://github.com/callstack/agent-device) |
| Profiling and debugging a React Native app | [Argent](https://github.com/software-mansion/argent) |
| Building, testing and running an Xcode project | [MobileBuildMCP](https://github.com/getsentry/MobileBuildMCP), formerly XcodeBuildMCP |
| Many agents on many devices at once, or a device cloud | [agent-device](https://github.com/callstack/agent-device) |

## Side by side

| | ios-agent | mobile-mcp | agent-device | Argent | MobileBuildMCP |
|---|---|---|---|---|---|
| Main job | Agent-driven iOS UI | Cross-platform device control | Coding agents verifying their change | Debug and profile apps | Build, test, run |
| How it reaches iOS | WebDriverAgent over XCTest | WebDriverAgent | XCTest, plus an accessibility bridge on the simulator | Closed binaries | AXe |
| Screen as seen by the model | Compact digest with refs | Accessibility tree, screenshot fallback | Accessibility snapshots with refs and diffs | Accessibility tree, screenshots, React tree | Mainly build output |
| Physical iPhone | USB and Wi-Fi | USB | Yes | Not stated for iOS | Yes, with signing |
| Android and other platforms | No | Yes | Yes, and many more | Yes | No |
| Approval before risky actions | Yes | Blocks unsafe URL schemes | Not described | Not described | Not described |
| Published agent evals | Yes | No | No | Customer anecdotes | No |
| Bundled agent and terminal app | Yes | No | No | No | No |
| Licence | MIT | Apache 2.0 | MIT | Apache 2.0, with proprietary binaries | MIT |

Every other project here is far more widely adopted than ios-agent, and all
but MobileBuildMCP reach more platforms. ios-agent is deliberately narrow:
iOS only, and deep in the layer an agent talks to.

## The problems users report, and what ios-agent does

These are the failure classes that recur across the four projects' public
issue trackers, roughly in order of how often they appear.

### An action reports success when nothing happened

The loudest theme in every tracker. Taps that return `tapped: true` and never
land ([Argent #547](https://github.com/software-mansion/argent/issues/547),
[#1176](https://github.com/software-mansion/argent/issues/1176)), a tap that
reports success without activating the control
([MobileBuildMCP #544](https://github.com/getsentry/MobileBuildMCP/issues/544)),
and a failed query indistinguishable from a true negative
([agent-device #2273](https://github.com/callstack/agent-device/issues/2273)).

**ios-agent:** every action returns the screen it produced and whether it
changed. A real no-op reports `screen_changed=False` on a physical iPhone, and
the bundled agent's verification step acts on it.

### Typed text does not land

Eleven characters requested and seven typed
([agent-device #2080](https://github.com/callstack/agent-device/issues/2080)),
and, on Android, leading characters dropped while the full string is reported
([Argent #562](https://github.com/software-mansion/argent/issues/562)).

**ios-agent:** the field is read back after typing. Text that did not land
fails the action and says what the field shows. Clearing a field uses the
element's own clear rather than a typed shortcut.

### Breakage when Xcode changes

Tools that reach into private simulator frameworks broke when Xcode 27
replaced Simulator.app with Device Hub
([Argent #406](https://github.com/software-mansion/argent/issues/406),
[#465](https://github.com/software-mansion/argent/issues/465),
[MobileBuildMCP #453](https://github.com/getsentry/MobileBuildMCP/issues/453),
[#535](https://github.com/getsentry/MobileBuildMCP/issues/535)).

**ios-agent:** public XCTest only. Two faster private routes were measured and
turned down ([ADR 0016](adr/0016-no-accessibility-inspector-tree-source.md),
[ADR 0017](adr/0017-no-simulator-native-tree-source.md)). WebDriverAgent has
its own startup failures; `doctor` diagnoses them.

### Physical iPhones

Device tunnels blocked on a new macOS
([mobile-mcp #323](https://github.com/mobile-next/mobile-mcp/issues/323)),
devices silently omitted ([#372](https://github.com/mobile-next/mobile-mcp/issues/372)),
and requests for UI automation on physical devices and for running on them
over Wi-Fi
([MobileBuildMCP #519](https://github.com/getsentry/MobileBuildMCP/issues/519),
[#54](https://github.com/getsentry/MobileBuildMCP/issues/54)).

**ios-agent:** USB and Wi-Fi, verified on an iPhone 17 Pro Max at the same
action count as the simulator. See [Physical iPhone](real-device-setup.md).

### Coordinates guessed from screenshots

"About 90% of the coordinates the LLM tries, is invalid"
([mobile-mcp #29](https://github.com/mobile-next/mobile-mcp/issues/29)),
oversized screenshots rejected by the model API
([#140](https://github.com/mobile-next/mobile-mcp/issues/140)), and sideways
landscape screenshots ([Argent #609](https://github.com/software-mansion/argent/issues/609)).

**ios-agent:** the model names elements by ref and never writes coordinates.
Resolution runs on the host and re-finds a moved element by identity.
Screenshots are capped at 1568 pixels on the long edge.

### Context cost and large screens

Thousands of always-on rule tokens
([Argent #518](https://github.com/software-mansion/argent/issues/518)), oversized
output ([MobileBuildMCP #177](https://github.com/getsentry/MobileBuildMCP/issues/177)),
and timeouts on long lists
([mobile-mcp #288](https://github.com/mobile-next/mobile-mcp/issues/288),
[agent-device #2552](https://github.com/callstack/agent-device/issues/2552)).

**ios-agent:** 50 to 474 tokens per step across thirteen golden flows. A
300-row Contacts list comes back in one observation of 438 tokens in 1.35s.

### Leftover processes

Hundreds of `simctl` processes at full CPU
([Argent #210](https://github.com/software-mansion/argent/issues/210)) and a
4 GB leak once the parent dies
([MobileBuildMCP #273](https://github.com/getsentry/MobileBuildMCP/issues/273)).

**ios-agent:** a reaper process stops the device runner when the process that
started it exits, a SIGKILL included. `ios-mcp reset` clears anything else.

### Where ios-agent is no better

- **System sheets.** Permission alerts are covered by a golden flow and an
  agent task. The StoreKit sign-in sheet
  ([Argent #207](https://github.com/software-mansion/argent/issues/207)) is not.
- **Flutter and WebViews** have no accessibility tree for anyone to read.
  ios-agent says so in the digest rather than returning an empty screen
  ([ADR 0007](adr/0007-report-unreachable-content-rather-than-reaching-it.md)).
  That is honesty, not a solution.
- **Instructions planted in screen content.** The approval gate stops risky
  actions; it does not detect a screen trying to steer the agent
  ([ADR 0013](adr/0013-no-defence-against-instructions-planted-in-screen-content.md)).
- **Breadth.** No Android, no profiling, no builds, no device cloud.
