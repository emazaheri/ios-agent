# ios-mcp

An MCP server that lets any AI agent drive an iPhone or an iOS Simulator.

Built on Apple's XCUIAutomation via WebDriverAgent. The server runs on a host
machine (macOS for simulators, macOS or Linux for physical devices) and drives a
tethered or Wi-Fi-connected device.

## Status

Under construction. See `docs/` and the architecture plan for the roadmap.

## Quick start

```bash
uv sync
uv run ios-mcp doctor      # tells you exactly what is missing
uv run ios-mcp devices     # list simulators and attached iPhones
uv run ios-mcp serve       # run the MCP server over stdio
```

## Why the automation runs on a host, not on the phone

An iOS app cannot automate other apps on the device it runs on. The sandbox
blocks cross-process access, and the Accessibility API is unavailable to
sandboxed apps even with user consent. XCUIAutomation only executes inside an
XCTest runner process started by `testmanagerd`, which is driven from a host
machine. Any future iOS app in this project is a client of this server, never
the automation engine.
