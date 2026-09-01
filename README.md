# ios-agent

**Drive an iPhone or an iOS Simulator with an AI agent.** A terminal app, an
MCP server, and the library beneath both.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#requirements)
[![Tests](https://img.shields.io/badge/tests-537%20offline-brightgreen.svg)](#development)

![ios-agent driving Settings on an iPhone simulator](docs/images/ios-agent.png)

Built on Apple's XCUIAutomation through WebDriverAgent. It runs on a Mac and
drives a simulator or a tethered phone. It is designed for **agents rather than
test suites**: screens arrive as a compact digest instead of raw accessibility
XML, actions hand back the screen they produced, and anything irreversible asks
first.

```bash
uv sync && ./scripts/prepare_wda.sh simulator
uv run ios-agent "turn on bold text"
```

## Contents

- [Why it is built this way](#why-it-is-built-this-way)
- [Requirements](#requirements) · [Setup](#setup)
- [The terminal app](#the-terminal-app)
- [Connecting your own agent over MCP](#connecting-your-own-agent-over-mcp)
- [What the model sees](#what-the-model-sees)
- [Safety](#safety)
- [Measured on real hardware](#measured-on-real-hardware)
- [Development](#development) · [Contributing](#contributing)

## Why it is built this way

Raw WebDriverAgent page source for a 200-row list runs to roughly **37,000
tokens**. Re-reading that after every tap exhausts a context window in a handful
of steps. Four decisions follow from that, and they are the whole design:

| | |
|---|---|
| **Perception is budget-aware** | 251 raw nodes to 12 elements on a real third-party screen; 50–445 tokens per step |
| **Actions return the screen they produced** | halves round-trips, and returns a *delta* when the screen is similar |
| **Resolution runs on the host** | six tiers, so a retry costs zero model tokens where a round-trip costs a whole turn |
| **The gate asks before acting, not after** | so the answer still means something |

Everything else in the repository is downstream of those.

## Requirements

| For | You need |
|---|---|
| **Simulator** | macOS, Xcode 16.3+, an iOS runtime, Python 3.12+ |
| **Physical iPhone** | the above plus [go-ios](https://github.com/danielpaulus/go-ios), Developer Mode, and a signing identity. Follow [docs/real-device-setup.md](docs/real-device-setup.md), which is a longer road than the simulator and has a few steps that look like bugs but are not |

Xcode ships **without** a simulator runtime. If `xcrun simctl list runtimes` is
empty, `xcodebuild -downloadPlatform iOS` fetches one (around 8 GB). If a
runtime is installed but no simulator has been created, `ios-agent` offers to
create one for you: that part takes about a second.

## Setup

```bash
uv sync
./scripts/prepare_wda.sh simulator   # builds WebDriverAgent, once
uv run ios-agent doctor              # says exactly what is still missing
```

`doctor` is the first thing to run whenever anything misbehaves. It checks the
toolchain, the simulator runtimes, the tunnel, WebDriverAgent's signing expiry
and the model, and returns a **remedy for each failure** rather than letting it
surface later as a connection error.

The app runs those same checks before it touches a device, so a machine that is
not set up is told so in about a second rather than after a simulator has
booted.

## The terminal app

```bash
uv run ios-agent                             # open it, decide later
uv run ios-agent "turn on bold text"         # give it a goal
uv run ios-agent --pick "turn wi-fi off"     # choose the device from a list
uv run ios-agent manual                      # drive it by hand, no model needed
uv run ios-agent devices                     # what is reachable
```

It streams the model's reasoning as it arrives, shows the digest the model is
reading beside it, and keeps the numbers on screen while they climb: actions,
observations, device tokens, cost.

| | |
|---|---|
| `/` | command menu, filtered as you type |
| `/device` · `ctrl+o` | switch phone or simulator mid-session |
| `esc` | stop at the next step, with a complete report; again to abort |
| `ctrl+r` · `ctrl+s` | re-read the screen · save the audit trail |
| `/copy` · `ctrl+y` | copy the transcript, or a selection, to the clipboard |
| `--inline` | run in a short region under the prompt |
| `--no-tui` | plain lines, for a pipe |

**`manual` mode needs no API key.** It drives the same eight verbs by hand,
which is the fastest way to debug perception on an app nobody has pointed this
at before.

The front end is held to one rule, asserted rather than argued: **watching a
run may not change what it costs.** `tests/tui/test_cost.py` runs the same task
wrapped and unwrapped and compares every counter by equality.

### Choosing a model

The provider is configuration, not a dependency. The loop builds through
LangChain's `init_chat_model`, so switching is two environment variables and an
extra:

```bash
uv sync --extra openai
IOS_AGENT_PROVIDER=openai IOS_AGENT_MODEL=gpt-5.6-sol uv run ios-agent "..."
```

Anthropic, OpenAI, Gemini, Bedrock, Groq, Mistral and a local Ollama model are
all supported. See [agent/README.md](agent/README.md).

## Connecting your own agent over MCP

30 tools and 4 resources, over stdio or HTTP. Add to `.mcp.json` (already
present here for Claude Code):

```json
{
  "mcpServers": {
    "ios": { "command": "uv", "args": ["run", "--directory", ".", "ios-mcp", "serve"] }
  }
}
```

Then ask for what you want in plain language. The server ships an `ios_operator`
prompt that teaches the observe/act/verify loop, so clients do not have to
reinvent it. For a remote client, `ios-mcp serve --transport http --port 8765`.

Or skip the protocol and import the library:

```python
outcome = await run_goal(session, "turn on bold text")
```

## What the model sees

```
screen: com.apple.Preferences / "Display & Text Size"  fp=3872280e
e1   button       "Accessibility" id=BackButton @(38,84)
e2   switch       "Bold Text" =0 id=ENHANCE_TEXT_LEGIBILITY @(336,161)
e3   button       "Larger Text, Off" id=LARGER_TEXT @(190,216)
```

Measured across eleven golden flows on a real simulator: **50 to 422 tokens per
tool call.**

The agent passes `e2` back to an action. It never writes XPath and never
guesses coordinates. If a ref goes stale because the screen moved, the host
re-finds the same element by identity rather than failing.

## Safety

Automating someone's real phone is not test automation. On by default:

- Anything matching **Send, Pay, Buy, Delete, Confirm or Sign Out** needs
  approval *before* it happens, via MCP elicitation or an
  `action_requires_approval` error an external human-in-the-loop layer can
  answer. Approval is scoped to one action: approving Send never approves
  Delete.
- Without an approver the run is unattended and everything destructive is
  **refused**, because an unanswerable question is not consent.
- `ios_type_secret` reads a value from the host keychain and sends it straight
  to the device. It never enters a prompt, a tool result, or the audit trail.
- Card numbers and email addresses are stripped from everything leaving the
  server.
- Repeated failures or a detected loop halt the session.
- The device picker never pre-selects a physical phone. Reaching one always
  costs a keystroke.

See [SAFETY.md](SAFETY.md). Every default is settable through an `IOS_MCP_*`
environment variable, a `.env`, or an optional `ios-mcp.toml`, in that order of
precedence. Copy `.env.example` to `.env` for the full list.

## Measured on real hardware

The eval harness was built before the agent, which is the only reason any
of these numbers exist. Latest measurement, 13 tasks × 3 runs on
`gpt-5.6-sol`:

| | |
|---|---|
| success | 39/39 |
| observations | **39, against an oracle floor of 39** |
| refusals, unusable runs | 0, 0 |
| cost | $2.13 over 10m28s |

Every task sits at the observation floor, including two in an app Apple did
not write, because every action already folds the screen it produced into
its response.

### Verified on real iOS, including a physical iPhone

Tier 1 runs against a scripted in-process device, so its numbers are a claim
about a fake. The same goal, `turn on Bold Text`, across all three tiers:

| | actions | observations | digest |
|---|---|---|---|
| scripted fake | 3 | 1 | — |
| iOS 26.5 simulator | 3 | 1 | 167 raw nodes → 14 elements, 261 tokens |
| **iPhone, iOS 26.6, Wi-Fi** | **3** | **1** | 140 raw nodes → 15 elements, 243 tokens |

Identical on all three, and on the phone it took 48.6s where the simulator took
seconds. The switch was confirmed by navigating there and reading `value="1"`
independently of what the agent claimed, then restored.

Most importantly, **a real no-op still reports `screen_changed=False` on the
phone.** If a physical device had moved its fingerprint between settled
snapshots, the verification step would have been silently dead on hardware
while every simulator and fake test stayed green.

Hardware is opt-in twice over, by the `device` marker and
`IOS_MCP_ALLOW_DEVICE=1`, because hardware being present is not consent to
change settings on it.

## Development

```bash
uv run pytest tests/unit          # 422 tests, no device, no model
uv run pytest tests/tui           # 175 tests, the terminal front end
uv run pytest tests/integration   # 13 tests, real simulator
uv run pytest tests/evals -s      # golden flows, with cost per flow
uv run ruff check . && uv run mypy ios_mcp agent/ios_agent tui/ios_tui
```

The eval suite is the quality gate: it reports tokens, wall time, action count
and resolution-tier distribution per flow. A drift from `exact` toward
`text-fuzzy` is the leading indicator that a flow is about to become flaky.
Agent tasks additionally declare an **action floor**, the number of actions a
hand-written oracle needs, asserted against that oracle so it cannot drift into
an aspiration. Failures are attributed too: a report says which of them were
the device, perception, the model or the policy gate, rather than only that
something failed.

Those numbers are kept over time in `tests/evals/history.jsonl`, one committed
line per measured run. CI runs the one series that costs nothing (the oracle
against a scripted device) and fails if any of it moves without the new line
being committed alongside. Hand-run slices go in the same file:

```bash
python scripts/eval_trend.py show --suite agent-oracle
python scripts/eval_trend.py append .artifacts/evals/agent-s5.json --suite agent-model
```

The guard is exact rather than banded, because every number it checks is a
count on a fixed route with no model, no network and no clock in it. See
[docs/adr/0009](docs/adr/0009-the-eval-trend-is-committed.md).

```bash
uv run python scripts/tui_screenshot.py   # render the front end to .artifacts
```

A passing test suite says nothing about what a terminal app looks like. That
script has caught eight display bugs no assertion did.

Three distributions in one uv workspace, and the dependencies only point one
way. See [ARCHITECTURE.md](ARCHITECTURE.md):

```
ios-tui    terminal front end     depends on ios-agent, ios-mcp
ios-agent  goal-directed agent    depends on ios-mcp
ios-mcp    library + MCP server   depends on neither
```

## Why the automation runs on a host, not on the phone

An iOS app cannot automate other apps on the device it runs on. The sandbox
blocks cross-process access, and the Accessibility API is unavailable to
sandboxed apps even with user consent. XCUIAutomation only executes inside an
XCTest runner started by `testmanagerd`, which is driven from a host. Any iOS
app in this project's future is a client of this server, never the engine.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers
the setup, the loop, and the five conventions that are load bearing rather than
stylistic. CI runs ruff, mypy and the 537 offline tests on Linux and macOS.

## License

MIT. See [LICENSE](LICENSE).
