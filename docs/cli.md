# Command line

Two commands. `ios-agent` is the terminal app and the bundled agent;
`ios-mcp` is the MCP server and its maintenance tools. `doctor`, `devices` and
`reset` exist in both and do the same thing.

Every subcommand and flag below is checked against the parsers by a test, so
this page cannot fall behind the code.

## Options for both commands

| Option | What it does |
|---|---|
| `--config PATH` | Read settings from a TOML file. Environment variables and `.env` still win; see [Configuration](../.env.example). |
| `--log-level LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. `ios-agent` defaults to `WARNING` so device traffic does not drown the run; `ios-mcp` uses the configured level, `INFO` by default, on stderr. |

## ios-agent

```bash
ios-agent                               # open the app, give it a goal there
ios-agent "turn on bold text"           # same as: ios-agent run "turn on bold text"
ios-agent --pick "turn wi-fi off"       # choose the device from a list first
ios-agent manual                        # drive by hand, no model, no API key
```

A goal with no subcommand means `run`, wherever the flags sit. No arguments at
all opens the app with the input focused.

### `run`

Give the agent a goal.

| Option | What it does |
|---|---|
| `goal` | What you want done, in plain English. Optional: without it the app opens and waits. |
| `--device NAME` | UDID or part of a device name. Omitted, the pool chooses and prefers a simulator. |
| `--app BUNDLE_ID` | Open this app first, for example `com.apple.Preferences`. Omitted, the agent starts from the current screen. |
| `--approve` | Ask before destructive actions instead of refusing them. Without it a run is unattended, and anything the gate would ask about is refused. |
| `--max-steps N` | Turns, and actions, before the agent gives up. Default 24 each. |
| `-p`, `--pick` | Choose the device from a list. A physical phone is never pre-selected. |
| `--no-tui` | Plain lines on stdout, for a pipe or a log. |
| `--inline` | Run in a short live region under the prompt rather than full screen. |
| `--no-stream` | Wait for each model turn rather than showing it as it arrives. |
| `-v`, `--verbose` | Show device startup lines. |

### `quickstart`

Check the toolchain, offer the repairs worth offering, build WebDriverAgent if
it is missing, then open manual mode. Needs no API key.

| Option | What it does |
|---|---|
| `-y`, `--yes` | Say yes to creating a simulator and building WebDriverAgent. |

### `manual`

Drive the device by hand with the agent's verbs and no model in the loop.

| Option | What it does |
|---|---|
| `--device NAME` | UDID or part of a device name. |
| `--app BUNDLE_ID` | Open this app first. |
| `--inline` | Run under the prompt. |
| `-p`, `--pick` | Choose the device from a list. |
| `-v`, `--verbose` | Show device startup lines. |

### `devices`, `doctor`, `reset`

See [the shared commands](#shared-commands) below.

## ios-mcp

```bash
ios-mcp                                          # same as: ios-mcp serve
ios-mcp serve --transport http --port 8765       # for a client that speaks HTTP
```

### `serve`

Run the MCP server. This is the default when no subcommand is given, which is
how an MCP client and the registry entry start it.

| Option | What it does |
|---|---|
| `--transport stdio\|http` | Default `stdio`. |
| `--host HOST` | HTTP only. Default `127.0.0.1`. |
| `--port PORT` | HTTP only. Default `8765`. |
| `--allow-remote` | Let HTTP bind an address other than loopback. The server has no authentication, so anyone who can reach the port can drive the device. See the [threat model](threat-model.md). |

### `prepare-wda`

Build WebDriverAgent, once, where the server looks for it. Clones
appium/WebDriverAgent at a pinned tag and builds it in about 20 seconds for
the simulator. A device build signs it with your team and reads `TEAM_ID`,
`UDID` and `WDA_BUNDLE_ID` from the environment.

| Option | What it does |
|---|---|
| `target` | `simulator` (the default) or `device`. |

The build goes into the WDA home: `IOS_MCP_WDA__HOME` when set, else a
clone's `vendor/wda` when run from one, else
`~/Library/Application Support/ios-mcp/wda`. Inside a clone,
`./scripts/prepare_wda.sh` does the same.

### `devices`, `doctor`, `reset`

See [the shared commands](#shared-commands) below.

## Shared commands

### `doctor`

Check the toolchain, simulator runtimes, the device tunnel, WebDriverAgent's
signing expiry and, from `ios-agent`, the model. Each failure comes with a
remedy. Run it first whenever anything misbehaves.

| Option | What it does |
|---|---|
| `--json` | Machine-readable output. |

### `devices`

List simulators and attached iPhones, and whether each is ready.

| Option | What it does |
|---|---|
| `--json` | Machine-readable output. |

### `reset`

List WebDriverAgent processes a crashed run left behind, and stop them with
`--yes`. A leftover runner holds the device and makes the next run time out.
It claims a process only when its test bundle, bundle id or forwarded port ties
it to WebDriverAgent, so your own `xcodebuild` runs are not touched. It exits
non-zero while anything is still running, so `ios-mcp reset -y && <run>` does
what it reads as.

| Option | What it does |
|---|---|
| `--device UDID` | Only processes driving this device. |
| `-y`, `--yes` | Stop what was found, rather than listing it. |
| `--json` | Machine-readable output. |
