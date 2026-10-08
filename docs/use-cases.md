# Use cases

What you can do with this today, as the repository stands, and what you
cannot yet. Each use case names where to start and the evidence that it
works, so a claim here is one the tests, the evals or a run on hardware
already back.

## At a glance

| Use case | Simulator | Physical iPhone | Needs a model |
|---|---|---|---|
| [Check a change to your own app](#check-a-change-to-your-own-app) | Yes | Yes | Yes, or none from Python |
| [Give your coding agent hands on a device](#give-your-coding-agent-hands-on-a-device) | Yes | Yes | Your client's |
| [Walk a flow and report what looks broken on it](#walk-a-flow-and-report-what-looks-broken-on-it) | Yes | Yes | Yes |
| [Check a screen under different device conditions](#check-a-screen-under-different-device-conditions) | Yes | Orientation only | Optional |
| [Read an answer out of an app with no API](#read-an-answer-out-of-an-app-with-no-api) | Yes | Yes | Yes |
| [Change a setting by saying what you want](#change-a-setting-by-saying-what-you-want) | Yes | Yes | Yes |
| [Write scripted checks in Python](#write-scripted-checks-in-python) | Yes | Yes | No |
| [See a screen the way an agent sees it](#see-a-screen-the-way-an-agent-sees-it) | Yes | Yes | No |
| [Build and measure your own iOS agent](#build-and-measure-your-own-ios-agent) | Yes | Yes | Yes |

## Check a change to your own app

You changed an iOS app and want something to open it, use the feature the
way a person would, and say whether it worked, without writing a UI test
first.

```bash
xcrun simctl install booted build/Build/Products/Debug-iphonesimulator/YourApp.app
xcrun simctl terminate booted com.example.yourapp
uv run ios-agent --app com.example.yourapp \
  "type Ada in the name field, save, and tell me whether it says Saved Ada"
```

Every action returns the screen it produced and says whether the screen
changed, so "I tapped Save" and "Save did something" are different answers.
A typed value is read back from the field, and text that did not land comes
back as a failure rather than a success.

- **Start with:** [Check your own app](check-your-app.md), run step by step
  against a small SwiftUI app on an iOS 27 simulator.
- **Evidence:** 39 of 39 runs succeeded across the agent tasks, at 1.14 times
  the actions a hand-written oracle needs. See
  [measured results](../README.md#measured-on-real-hardware).
- **Limits:** a simulator build needs no signing; a phone needs a signed
  runner (see [Use a physical iPhone](real-device-setup.md)). An app that is
  still running keeps its state, so terminate it or launch with `fresh=true`
  for a clean start.

## Give your coding agent hands on a device

Claude Code, Cursor or any MCP client gets 31 tools and 5 resources for an
iOS simulator or iPhone: open a session, install a build, launch, tap, type,
scroll, wait for text, read logs, export the trace. A coding agent can then
check its own change in the running app before it reports the work as done.

```bash
uv run ios-mcp serve
```

- **Start with:** [Connecting your own agent over MCP](../README.md#connecting-your-own-agent-over-mcp),
  then the [tool reference](tool-reference.md).
- **Evidence:** the tools are thin wrappers over the library the 13 golden
  flows run on a simulator, including declining a real Maps permission
  alert, with tokens per step held under a ceiling of 900. Over MCP each call
  costs about 1.6 ms more and no extra tokens.
- **Limits:** one session drives one device. Several sessions on several
  devices in parallel is not something this is built or measured for.

## Walk a flow and report what looks broken on it

Give the agent a route through your app and ask it to report anything that
seems wrong along the way: a button that takes the tap and does nothing, a
screen that contradicts an earlier one, a count that does not match what is
switched on.

```bash
uv run ios-agent --app com.example.yourapp \
  "sign up as Ada with ada@example.com, finish onboarding, and report \
anything that seems broken, one line per problem"
```

- **Evidence:** on a six-screen onboarding app with seven planted bugs, every
  bug that announces itself on the route the goal forced was found in six
  runs out of six, with one false report across all six, at one observation
  per run. See [ADR 0021](adr/0021-exploring-an-app-for-planted-bugs.md).
- **Limits, and they matter:** this is checking a route you name, not
  exploring an app. Overall recall of self-evident bugs was 60%, below the
  80% bar set beforehand, because the agent does not wander down branches the
  goal did not ask for. A bug that is wrong only against a spec is found only
  when the goal states the spec (3 of 3 with it, 0 of 3 without). Purely
  visual bugs, such as white text on white, are invisible to it. Name the
  branches you care about, and state the rules that define correct.

## Check a screen under different device conditions

Before or during a flow, put the simulator into the state you want to test:
dark appearance, landscape, a simulated location, a permission already
granted or denied, or a frozen status bar for clean screenshots. Then check
the screen reads and behaves the way it should.

| Condition | Tool | Where |
|---|---|---|
| Light or dark appearance | `ios_set_device_state` | Simulator |
| Portrait or landscape | `ios_set_device_state` | Simulator and iPhone |
| Simulated location | `ios_set_device_state` | Simulator |
| Status bar frozen | `ios_set_device_state` | Simulator |
| Camera, photos, location and other permissions | `ios_set_permission` | Simulator |
| A system alert answered as a person would | `ios_handle_alert`, or the bundled agent reading it as a screen | Both |

- **Start with:** the [Environment](tool-reference.md#environment) section of
  the tool reference.
- **Evidence:** the agent task `answer_a_permission_alert` puts a location
  alert in front of an app on first launch; the agent declined it 3 runs out
  of 3 at the oracle's action count.

## Read an answer out of an app with no API

Some information lives only on a screen: an answer on a profile card, a
status inside an app with no export, a value buried two levels into
Settings. The agent can navigate there and return it as text.

```bash
uv run ios-agent "what does Settings say my iPhone's model name is?"
```

- **Evidence:** the agent task `read_a_card_answer` reads an answer out of a
  card screen built with the habits that broke perception on a real
  third-party app: a labelled wrapper, a field split across label and value,
  a control with an id and no label.
- **Limits:** Flutter canvases and WebViews expose no accessibility tree. The
  digest says so and points at a screenshot rather than returning an empty
  screen. See [ADR 0007](adr/0007-report-unreachable-content-rather-than-reaching-it.md).

## Change a setting by saying what you want

"Turn on bold text", "turn off Wi-Fi", "set quiet hours", on a simulator or
your own phone. Switches are set to a state rather than toggled, so asking
for on when a switch is already on touches nothing.

```bash
uv run ios-agent "turn on bold text"
uv run ios-agent --pick "turn off Wi-Fi"    # choose a phone or simulator
```

- **Start with:** the [quickstart](quickstart.md).
- **Evidence:** the same goal ran at 3 actions and 1 observation on the
  scripted fake, on an iOS 26.5 simulator, and on an iPhone 17 Pro Max on
  iOS 26.6 over Wi-Fi.
- **Limits:** the policy gate asks before anything that deletes, pays, sends,
  or reaches another person, such as a like or a follow. See
  [SAFETY.md](../SAFETY.md) and [ADR 0014](adr/0014-ask-before-reaching-another-person.md).
  Answering yes is your decision, on your device.

## Write scripted checks in Python

When you know the steps and want the same result every run, skip the model.
`IosSession` is the library under both the server and the agent, and
everything it does an agent can do, a script can do: tap by name, set a
switch to a state, wait for text, read the screen.

```python
await session.launch_app("com.example.yourapp", fresh=True)
await session.type_text("Ada", target="Name")
await session.tap(target="Save")
result = await session.wait_for("Saved Ada")
assert result.ok
```

Elements are found by what they say on screen, through six resolution tiers
on the server, so a script names "Save" rather than holding coordinates or an
XPath.

- **Start with:** [Build on the library](library.md).
- **Evidence:** the simulator integration tests and the 13 golden flows are
  written this way.
- **Limits:** there is no recorder that turns an agent's run into a script,
  and no JUnit report or test runner of its own; use pytest or whatever you
  already run.

## See a screen the way an agent sees it

Drive a device by hand, with no API key, and read each screen as the model
would: a few hundred tokens of refs, roles and labels. Useful for
understanding why an agent did what it did, or for checking that the
controls in your app say what they are.

```bash
uv run ios-agent manual
```

`ios_screenshot` with `annotate_refs=true` draws every ref onto a screenshot,
for screens where the visible text and the label differ.

- **Start with:** [The terminal app](../README.md#the-terminal-app) and
  [What the model sees](../README.md#what-the-model-sees).

## Build and measure your own iOS agent

Use the library, the MCP server and the eval harness as the base for your
own agent, and measure it the same way this one was: success, observations
and actions against a hand-written oracle, turns, tokens per step, and cost.

- **Start with:** [Build on the library](library.md), then
  [how the agent was built](how-it-was-built.md) and the
  [decision records](adr/), which show what was kept and what the
  measurements turned down.
- **Evidence:** 15 goal-directed agent tasks, three of them injecting failures
  seen on real hardware, each with an oracle whose floors are asserted by
  equality so they cannot drift.

## Not yet

Things people reasonably want from a tool like this that the repository does
not do today:

- **Exploratory bug hunting with no route given.** Measured and rejected at
  60% recall in [ADR 0021](adr/0021-exploring-an-app-for-planted-bugs.md).
- **Visual checks.** Layout, colour and contrast bugs are not in the
  accessibility tree, and the bundled agent cannot see images.
- **Many devices in parallel**, device farms, or test sharding.
- **A regression suite format**, with record and replay or CI reports.
- **Android**, or any platform other than iOS.
- **Flutter canvases and WebViews**, which have no tree to read.
- **Building your app.** Bring a `.app` or `.ipa`; Xcode builds it.

For the tools that cover some of these, see [How it compares](comparison.md).
