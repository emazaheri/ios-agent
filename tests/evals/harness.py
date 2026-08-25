"""Golden-flow evaluation harness.

Unit tests answer "does the code work". These answer the question that decides
whether this is a product: how much does a real task cost? A flow that
succeeds in 40 seconds and 6k tokens is usable; the same flow at 4 minutes and
90k tokens is a demo.

Four numbers per flow, tracked per commit: tokens, wall time, action count, and
the distribution of resolution tiers. The last one is the leading indicator; a
flow drifting from `exact` toward `text-fuzzy` is about to become flaky.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ios_mcp.session import IosSession

#: Rough characters-per-token, matching the digest's own estimate.
_CHARS_PER_TOKEN = 4


@dataclass
class EvalResult:
    name: str
    passed: bool
    tokens: int
    seconds: float
    actions: int
    #: Every charged tool call, observations included. Actions alone understate
    #: the bill, because a flow that observes three times and taps once pays
    #: for four payloads.
    steps: int = 0
    tiers: dict[str, int] = field(default_factory=dict)
    failure: str | None = None
    recovered: int = 0

    @property
    def tokens_per_step(self) -> float:
        return self.tokens / self.steps if self.steps else 0.0

    def to_dict(self) -> dict[str, Any]:
        out = {
            "flow": self.name,
            "passed": self.passed,
            "tokens": self.tokens,
            "seconds": round(self.seconds, 1),
            "actions": self.actions,
            "steps": self.steps,
            "tokens_per_step": round(self.tokens_per_step, 1),
            "resolution_tiers": self.tiers,
        }
        if self.recovered:
            out["runner_recoveries"] = self.recovered
        if self.failure:
            out["failure"] = self.failure
        return out

    def render(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        line = (
            f"[{status}] {self.name:32} "
            f"{self.tokens:>6} tok  {self.seconds:>5.1f}s  "
            f"{self.actions:>2} act {self.steps:>2} steps  "
            f"{self.tokens_per_step:>6.0f} tok/step"
        )
        if self.failure:
            line += f"\n         {self.failure}"
        return line


class TokenMeter:
    """Counts what the agent would actually have been charged for.

    Wraps the session so every payload an action or observation returns is
    measured, which is the only honest way to compare flows.
    """

    def __init__(self, session: IosSession) -> None:
        self.session = session
        self.tokens = 0
        self.actions = 0
        self.steps = 0

    def charge(self, payload: Any) -> None:
        self.tokens += len(json.dumps(payload, default=str)) // _CHARS_PER_TOKEN
        self.steps += 1

    async def observe(self, **kwargs: Any) -> Any:
        digest = await self.session.observe(**kwargs)
        self.charge(digest.to_dict())
        return digest

    async def act(self, coro: Awaitable[Any]) -> Any:
        result = await coro
        self.actions += 1
        self.charge(result.to_dict())
        return result


async def run_flow(
    name: str,
    session: IosSession,
    flow: Callable[[IosSession, TokenMeter], Awaitable[bool]],
) -> EvalResult:
    """Run one flow, measuring it. A raised error is a failure, not a crash."""
    meter = TokenMeter(session)
    started = time.monotonic()
    passed = False
    failure: str | None = None
    try:
        passed = await flow(session, meter)
        if not passed:
            failure = "the flow completed but its success assertion did not hold"
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"

    tiers: dict[str, int] = {}
    for entry in session.audit.entries:
        if entry.resolved_via:
            tiers[entry.resolved_via] = tiers.get(entry.resolved_via, 0) + 1

    return EvalResult(
        name=name,
        passed=passed,
        tokens=meter.tokens,
        seconds=time.monotonic() - started,
        actions=meter.actions,
        steps=meter.steps,
        tiers=tiers,
        failure=failure,
        recovered=session.wda.recovered_count,
    )


def write_report(results: list[EvalResult], path: Path) -> Path:
    """Persist the run so the numbers can be diffed against a previous commit."""
    payload = {
        "generated_at": time.time(),
        "totals": {
            "flows": len(results),
            "passed": sum(1 for r in results if r.passed),
            "tokens": sum(r.tokens for r in results),
            "seconds": round(sum(r.seconds for r in results), 1),
            "actions": sum(r.actions for r in results),
            "steps": sum(r.steps for r in results),
            "tokens_per_step": round(
                sum(r.tokens for r in results) / max(sum(r.steps for r in results), 1), 1
            ),
        },
        "flows": [r.to_dict() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path
