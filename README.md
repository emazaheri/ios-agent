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

## The agent

`agent/ios_agent/` is a goal-directed agent built on LangGraph that takes a
sentence ("Turn on Bold Text") and drives the phone to it. It is a **peer of
the MCP server, not a layer above it**: both consume the same `IosSession`, and
both pass through the same policy gate. The provider is configuration, so
OpenAI, Anthropic, Gemini or a local Ollama model is two environment variables
rather than a code change.

```bash
uv sync --extra openai
IOS_AGENT_PROVIDER=openai IOS_AGENT_MODEL=gpt-5.6-sol \
  uv run pytest tests/evals/agent -m model -s
```

### What it cost to find out which ideas were worth keeping

The interesting part is not the agent, it is that the measurement was built
first and then used to reject most of what was planned. Four "deep agent"
pillars were specified up front. **One survived contact with a number.**

| pillar | outcome | evidence |
|---|---|---|
| Verification | **kept** | actions 85 → 53 (−38%), cost $1.21 → $0.74 (−39%), success 21/21 |
| Planning | rejected | agent already at a hand-written oracle's floor on 8 of 10 tasks; total headroom 1 action in 20 |
| Subagents | rejected | 5,864 prompt tokens/run against a 1M window; no screenshots taken; resolution is already server-side |
| Memory | rejected | hedged, it measured *worse* than no memory; asserted, the agent stopped checking the device |

Each rejection is an ADR in [docs/adr/](docs/adr/) with the numbers behind it.

Two results worth stating plainly, because both contradicted the plan:

- The skeleton loop was predicted to look before every move. It spent **exactly
  one observation per run**, the oracle's floor, because every action already
  folds the resulting screen into its response. The lever the whole phase was
  designed around was at its limit before anything was built.
- Memory's two framings are the two ends of one dial with no good setting.
  Hedge it enough to be safe and it motivates an investigation rather than
  removing one; assert it enough to save the work and one run in three finished
  **without touching the device at all**, reporting a failure it never observed.

### Verified on real iOS, including a physical iPhone

Tier 1 runs against a scripted in-process device, so its numbers are a claim
about a fake. The same goal, `Turn on Bold Text in Settings`, across all three
tiers:

| | actions | observations | digest |
|---|---|---|---|
| scripted fake | 3 | 1 | — |
| iOS 26.5 simulator | 3 | 1 | 167 raw nodes → 14 elements, 261 tokens |
| **iPhone, iOS 26.6, Wi-Fi** | **3** | **1** | 140 raw nodes → 15 elements, 243 tokens |

Identical on all three, and on the phone it took 48.6s where the simulator took
seconds. The switch was confirmed by navigating there and reading `value="1"`
independently of what the agent claimed, then restored.

Most importantly, **a real no-op still reports `screen_changed=False` on the
phone**. Fingerprints round positions to 4px so animation jitter does not
register; if a physical device had moved its fingerprint between settled
snapshots, the one pillar this phase kept would have been silently dead on
hardware while every simulator and fake test stayed green.

Tier 3 is opt-in twice over, by the `device` marker and `IOS_MCP_ALLOW_DEVICE=1`,
because hardware being present is not consent to change settings on it.

### Giving it a task

`ios-agent` is a terminal front end over the same library, in its own
distribution (`tui/`, [ADR 0008](docs/adr/0008-the-front-end-is-a-third-distribution.md)).

```bash
uv run ios-agent                               # open it, decide later
uv run ios-agent doctor                        # is this machine set up
uv run ios-agent devices                       # what is reachable
uv run ios-agent "turn on bold text"           # a simulator
uv run ios-agent --pick "turn on bold text"    # choose the device from a list
uv run ios-agent --device "iPhone" \
    --app com.apple.Preferences "turn wi-fi off"         # your phone
uv run ios-agent manual                        # drive it by hand, no model
```

It streams the model's text as it arrives, shows the digest the model is
reading beside it, and keeps the numbers on screen while they climb: actions,
observations, device tokens, cost. Escape halts the run at the next step and
still reports what it did; a second Escape aborts and says the screen on
display can no longer be trusted. `--no-tui` prints the same events as plain
lines for a pipe, and `--inline` runs in a short region under the prompt.

The front end is held to one rule, asserted rather than argued: **watching a
run may not change what it costs.** `tests/tui/test_cost.py` runs the same task
wrapped and unwrapped and compares every counter by equality.

In code the whole API is still one call:

```python
outcome = await run_goal(session, "turn on bold text")
```

`--device` matters: without it the pool prefers a simulator, because acting on
a real phone should take intent rather than being whatever was nearest.

### Approval pauses the run

Anything destructive stops the graph and asks, rather than deciding for the
person whose phone it is:

```python
async def ask(request):  # request names the action and why it tripped
    return input(f"{request['reason']}  approve? ") == "y"


outcome = await run_goal(session, "clear my messages", approve=ask)
```

Without an `approve` callback the run is unattended and everything destructive
is refused, because an unanswerable question is not consent. Approval is scoped
to one action: approving Send never approves Delete.

See [agent/README.md](agent/README.md) for configuration.

## Development

```bash
uv run pytest tests/unit          # 368 tests, no device needed
uv run pytest tests/tui           # the terminal front end, no device, no model
uv run pytest tests/integration   # 13 tests, real simulator
uv run pytest tests/evals -s      # 11 golden flows, with cost per flow
uv run pytest tests/evals/agent   # agent evals, scripted device, no model
```

The eval suite is the quality gate: it reports tokens, wall time, action count
and resolution-tier distribution per flow. A drift from `exact` toward
`text-fuzzy` is the leading indicator that a flow is about to become flaky.

Agent tasks additionally declare an **action floor**, the number of actions a
hand-written oracle needs, asserted against that oracle so it cannot drift into
an aspiration. It is what every agent number is reported against.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the layering, and
[docs/real-device-setup.md](docs/real-device-setup.md) for physical iPhones.

## Why the automation runs on a host, not on the phone

An iOS app cannot automate other apps on the device it runs on. The sandbox
blocks cross-process access, and the Accessibility API is unavailable to
sandboxed apps even with user consent. XCUIAutomation only executes inside an
XCTest runner started by `testmanagerd`, which is driven from a host. Any iOS
app in this project's future is a client of this server, never the engine.
