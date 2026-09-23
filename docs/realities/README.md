# iOS realities the fakes do not model

Every one of these was found by running against hardware, not by
reasoning. None of it was found by a fake, which is why a change to the
digest, resolution or the device lifecycle is not believed until it has
been run for real.

They are split by mechanic rather than kept as one list because almost
none of it is needed to answer a given question, and a file that is read
in full every time is a file that gets skimmed.

| file | what is in it |
|---|---|
| [simulator-lifecycle](simulator-lifecycle.md) | runtimes, devices, `simctl boot`, and the window Xcode 27 renamed |
| [device-and-wda](device-and-wda.md) | launching the runner, USB against Wi-Fi, deep links, and what a snapshot costs |
| [perception-geometry](perception-geometry.md) | rects, `isVisible`, identity against value, and roles that are a bet on the framework |
| [third-party-apps](third-party-apps.md) | the habits that broke perception the first time it left Settings |
| [compound-controls](compound-controls.md) | pickers, wheels, steppers, and the wrappers around them |
| [screenshots-and-annotation](screenshots-and-annotation.md) | points against pixels, on the one output a caller trusts by eye |
| [unreachable-content](unreachable-content.md) | Flutter canvases and WebViews, where there is no tree at any price |

Two things that are *not* here. A note about one particular app, written
for the agent to read while driving it, belongs in
`agent/ios_agent/skills/apps/`. A note that exists because the digest
drops something belongs in a fix to `ios_mcp/perception/`: this directory
is the record of what it costs to encode one app's habits somewhere they
cannot be tested.
