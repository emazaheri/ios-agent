## What this changes, and why

<!-- The problem first. A diff shows what changed; it cannot say what was wrong. -->

## How it was verified

<!--
Tick what you ran. The offline suites are the floor, not the proof: no fake in
this repository has ever caught a perception-geometry or device-lifecycle bug.
Every one came from a real run.
-->

- [ ] `uv run pytest tests/unit tests/tui -q`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy ios_mcp agent/ios_agent tui/ios_tui`
- [ ] `uv run pytest tests/integration` — needed for anything touching perception or the device lifecycle
- [ ] `uv run pytest tests/evals -s` — needed for anything that could change what a run costs
- [ ] Ran it against a real simulator or phone

<!-- If a number moved, put the before and after here. -->

## Anything a reviewer should push back on

<!--
Uncertainties, shortcuts, things you were unsure of. This is the most useful
section in the template; leaving it empty is a claim.
-->
