# ios-mcp

An MCP server that lets an AI agent drive an iPhone or an iOS Simulator.

Built on Apple's XCUIAutomation via WebDriverAgent. The server runs on a host
machine and drives a tethered or simulated device. It is designed for agents
rather than test suites: screens arrive as a compact digest instead of raw
accessibility XML, actions return the screen they produced, and anything
irreversible needs approval first.

```
> open Settings and turn on Bold Text

ios_open_session      iPhone 17, iOS 26.5
ios_tap               "Accessibility"          text-exact   3.0s
ios_tap               "Display & Text Size"    text-exact   3.0s
ios_set_value on      "Bold Text"              text-exact   3.1s
                      ~ e2 switch "Bold Text" =1 (was 0)
```

## Requirements

| For | You need |
|---|---|
| Simulator | macOS, Xcode 16.3+, an iOS runtime, Python 3.12+ |
| Physical iPhone | the above plus [go-ios](https://github.com/danielpaulus/go-ios), Developer Mode, and a signing identity |

Xcode ships without a simulator runtime. If `xcrun simctl list runtimes` is
empty, run `xcodebuild -downloadPlatform iOS` (around 8 GB).

## Setup

```bash
uv sync
./scripts/prepare_wda.sh simulator   # builds WebDriverAgent, once
uv run ios-mcp doctor                # says exactly what is still missing
```

`ios-mcp doctor` is the first thing to run whenever anything misbehaves. It
checks the toolchain, the tunnel, and WebDriverAgent's signing expiry, and
returns a remedy for each failure rather than leaving it to surface later as a
connection error.

## Connecting an agent

Add to `.mcp.json` (already present in this repo for Claude Code):

```json
{
  "mcpServers": {
    "ios": { "command": "uv", "args": ["run", "--directory", ".", "ios-mcp", "serve"] }
  }
}
```

Then ask for what you want in plain language. The server ships an `ios_operator`
prompt that teaches the observe/act/verify loop, so clients do not have to
reinvent it.

For a remote client, `ios-mcp serve --transport http --port 8765`.

## What the agent sees

Raw WebDriverAgent page source for a 200-row list runs to roughly 37,000
tokens. Re-reading that after every tap exhausts a context window in a handful
of steps. `ios_observe` returns this instead:

```
screen: com.apple.Preferences / "Display & Text Size"  fp=3872280e
e1   button       "Accessibility" id=BackButton @(38,84)
e2   switch       "Bold Text" =0 id=ENHANCE_TEXT_LEGIBILITY @(336,161)
e3   button       "Larger Text, Off" id=LARGER_TEXT @(190,216)
```

Measured across eleven golden flows on a real simulator: 50 to 422 tokens per
tool call.

The agent passes `e2` back to an action. It never writes XPath and never
guesses coordinates. If a ref goes stale because the screen moved, the server
re-finds the same element by identity rather than failing.

## Safety

Automating someone's real phone is not test automation. On by default:

- Anything matching Send, Pay, Buy, Delete, Confirm or Sign Out needs approval
  before it happens, via MCP elicitation or an `action_requires_approval` error
  that an external human-in-the-loop layer can answer.
- `ios_type_secret` reads a value from the host keychain and sends it straight
  to the device. It never enters a prompt, a tool result, or the audit trail.
- Card numbers and email addresses are stripped from everything leaving the
  server.
- Repeated failures or a detected loop halt the session.

See [SAFETY.md](SAFETY.md). Defaults are in `ios-mcp.toml`, a `.env`, or
`IOS_MCP_*` environment variables. Copy `.env.example` to `.env` for the full
list of knobs across both packages; a real environment variable always beats
the file.

## Development

```bash
uv run pytest tests/unit          # 243 tests, no device needed
uv run pytest tests/integration   # real simulator
uv run pytest tests/evals -s      # golden flows, with cost per flow
```

The eval suite is the quality gate: it reports tokens, wall time, action count
and resolution-tier distribution per flow. A drift from `exact` toward
`text-fuzzy` is the leading indicator that a flow is about to become flaky.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the layering, and
[docs/real-device-setup.md](docs/real-device-setup.md) for physical iPhones.

## Why the automation runs on a host, not on the phone

An iOS app cannot automate other apps on the device it runs on. The sandbox
blocks cross-process access, and the Accessibility API is unavailable to
sandboxed apps even with user consent. XCUIAutomation only executes inside an
XCTest runner started by `testmanagerd`, which is driven from a host. Any iOS
app in this project's future is a client of this server, never the engine.
