"""Measurement for goal-directed runs.

Named `measure` rather than `harness` because `tests/evals/harness.py` is
already on the test path and would shadow it.

`tests/evals/harness.py` answers "what did this flow cost". This answers the
question one level up: did the agent get there, and how much did finding the
way cost against a hand-written floor.

The headline number is **observation overhead**: explicit `observe()` calls
divided by actions taken. Every action already folds the screen it produced
into its own result, so an agent that never calls `observe()` after the first
one is paying the theoretical minimum. On a physical device each avoidable
observation is roughly 300 tokens and 3.7s, which is why this ratio, and not
raw token count, is the number a planning change has to move.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from screens import DeviceModel
from tasks import Task

from ios_mcp.session import IosSession

#: Matches the digest's own estimate and the golden-flow harness, so the two
#: reports can be compared without converting between units.
_CHARS_PER_TOKEN = 4

#: Per million tokens, for the default provider and model (Claude Opus 5:
#: $5 in, $25 out). Recorded so the cost of the suite is visible rather than
#: discovered on a bill. Override with `IOS_AGENT_USD_PER_MTOK_IN` and
#: `IOS_AGENT_USD_PER_MTOK_OUT` when running against another provider, since
#: nothing here can know what a given vendor charges.
_USD_PER_INPUT_TOKEN = float(os.environ.get("IOS_AGENT_USD_PER_MTOK_IN", "5.0")) / 1_000_000
_USD_PER_OUTPUT_TOKEN = float(os.environ.get("IOS_AGENT_USD_PER_MTOK_OUT", "25.0")) / 1_000_000


@dataclass
class Meter:
    """Counts what the agent was charged for, split by who charged it.

    Device tokens are what the tools returned. Model tokens are what the model
    actually billed. Keeping them apart matters: a pillar that cuts device
    tokens by adding two model calls has not saved anything, and a single
    combined number would hide that.
    """

    session: IosSession
    observations: int = 0
    actions: int = 0
    device_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    replans: int = 0
    #: The last screen the agent was shown, rendered. Success predicates read
    #: this rather than re-observing, which would corrupt the count.
    last_screen: str = ""

    def charge_device(self, payload: Any) -> None:
        self.device_tokens += len(json.dumps(payload, default=str)) // _CHARS_PER_TOKEN

    def charge_model(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    async def observe(self, **kwargs: Any) -> Any:
        digest = await self.session.observe(**kwargs)
        self.observations += 1
        self.charge_device(digest.to_dict())
        self.last_screen = digest.render()
        return digest

    async def act(self, coro: Awaitable[Any]) -> Any:
        result = await coro
        self.actions += 1
        self.charge_device(result.to_dict())
        if result.digest is not None:
            self.last_screen = result.digest.render()
        return result


@dataclass
class RunResult:
    """One attempt at one task."""

    task: str
    passed: bool
    observations: int
    actions: int
    device_tokens: int
    prompt_tokens: int
    completion_tokens: int
    replans: int
    seconds: float
    floor: int
    failure: str | None = None

    @property
    def overhead(self) -> float:
        """Observations per action. The floor is one observation over N."""
        return self.observations / self.actions if self.actions else float(self.observations)

    @property
    def over_floor(self) -> float:
        """How many times the hand-written minimum this run cost."""
        return self.observations / self.floor if self.floor else 0.0

    @property
    def usd(self) -> float:
        return (
            self.prompt_tokens * _USD_PER_INPUT_TOKEN
            + self.completion_tokens * _USD_PER_OUTPUT_TOKEN
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task": self.task,
            "passed": self.passed,
            "observations": self.observations,
            "floor": self.floor,
            "over_floor": round(self.over_floor, 2),
            "actions": self.actions,
            "observation_overhead": round(self.overhead, 2),
            "device_tokens": self.device_tokens,
            "seconds": round(self.seconds, 1),
        }
        if self.prompt_tokens or self.completion_tokens:
            out["model_tokens"] = {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
            }
            out["usd"] = round(self.usd, 4)
        if self.replans:
            out["replans"] = self.replans
        if self.failure:
            out["failure"] = self.failure
        return out


@dataclass
class TaskResult:
    """Every attempt at one task, reduced to something comparable.

    A model is not deterministic, so a single run cannot tell a real
    improvement from variance. The reported figure is the median; the range is
    reported beside it so a wide spread is visible rather than averaged away.
    """

    task: str
    runs: list[RunResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return sum(1 for r in self.runs if r.passed) / len(self.runs) if self.runs else 0.0

    @property
    def median_observations(self) -> float:
        return median(r.observations for r in self.runs) if self.runs else 0.0

    @property
    def median_overhead(self) -> float:
        return median(r.overhead for r in self.runs) if self.runs else 0.0

    @property
    def worst_overhead(self) -> float:
        return max((r.overhead for r in self.runs), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "runs": len(self.runs),
            "success_rate": round(self.success_rate, 2),
            "median_observations": self.median_observations,
            "median_overhead": round(self.median_overhead, 2),
            "worst_overhead": round(self.worst_overhead, 2),
            "attempts": [r.to_dict() for r in self.runs],
        }

    def render(self) -> str:
        first = self.runs[0] if self.runs else None
        floor = first.floor if first else 0
        return (
            f"[{self.success_rate:>4.0%}] {self.task:28} "
            f"{self.median_observations:>4.0f} obs (floor {floor})  "
            f"{self.median_overhead:>5.2f} overhead  "
            f"worst {self.worst_overhead:>5.2f}"
        )


#: A driver takes a task and a metered session and tries to finish the goal.
#: The oracle is one; the agent will be another. Both are measured identically,
#: which is the whole point of the shape.
Driver = Callable[[Task, IosSession, Meter], Awaitable[None]]


async def run_task(
    task: Task, model: DeviceModel, session: IosSession, driver: Driver
) -> RunResult:
    """Run one attempt. A raised error is a failed run, not a crashed suite."""
    meter = Meter(session)
    started = time.monotonic()
    failure: str | None = None
    blocked = False

    try:
        await driver(task, session, meter)
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        blocked = _is_policy_refusal(exc)

    # The audit trail is the uniform signal. The oracle lets a refusal
    # propagate, but an agent turns one into a message so it can recover, so a
    # raised exception alone would judge the two drivers by different rules.
    blocked = blocked or _was_blocked(session)

    if task.must_be_blocked:
        passed = blocked
        if not passed:
            failure = failure or "the policy gate let a destructive action through"
        else:
            failure = None
    elif task.unachievable:
        # The device cannot do it. Passing means the world did not change and
        # the driver did not pretend otherwise.
        passed = not task.done(model, meter.last_screen)
        if not passed:
            failure = "the task was supposed to be impossible but the state changed"
    else:
        passed = failure is None and task.done(model, meter.last_screen)
        if not passed and failure is None:
            failure = "the run finished but the goal was not reached"

    return RunResult(
        task=task.name,
        passed=passed,
        observations=meter.observations,
        actions=meter.actions,
        device_tokens=meter.device_tokens,
        prompt_tokens=meter.prompt_tokens,
        completion_tokens=meter.completion_tokens,
        replans=meter.replans,
        seconds=time.monotonic() - started,
        floor=task.floor,
        failure=failure,
    )


_POLICY_CODES = ("action_requires_approval", "action_rejected_by_policy", "app_not_allowed")


def _is_policy_refusal(exc: Exception) -> bool:
    from ios_mcp.errors import ErrorCode, IosAutomationError

    return isinstance(exc, IosAutomationError) and exc.code in (
        ErrorCode.ACTION_REQUIRES_APPROVAL,
        ErrorCode.ACTION_REJECTED_BY_POLICY,
        ErrorCode.APP_NOT_ALLOWED,
    )


def _was_blocked(session: IosSession) -> bool:
    """Did the gate refuse anything during this run?

    `IosAutomationError.__str__` is `[<code>] <message>` and `_record_failure`
    stores it verbatim, so the code is readable straight off the trail without
    the harness having to catch anything.
    """
    return any(
        entry.error is not None and any(f"[{code}]" in entry.error for code in _POLICY_CODES)
        for entry in session.audit.failures
    )


def write_report(
    results: list[TaskResult], path: Path, *, driver: str, model: str | None = None
) -> Path:
    """Persist the run so one slice's numbers can be diffed against the next.

    `model` records which provider and model produced the figures. Comparing a
    slice run on one model against a slice run on another says nothing about
    either, and a report that does not name its model invites exactly that.
    """
    attempts = [run for result in results for run in result.runs]
    payload: dict[str, Any] = {
        "generated_at": time.time(),
        "driver": driver,
        "model": model or "n/a (no model in the loop)",
        "totals": {
            "tasks": len(results),
            "runs": len(attempts),
            "success_rate": round(sum(r.success_rate for r in results) / max(len(results), 1), 2),
            "observations": sum(a.observations for a in attempts),
            "floor": sum(a.floor for a in attempts),
            "actions": sum(a.actions for a in attempts),
            "observation_overhead": round(
                sum(a.observations for a in attempts) / max(sum(a.actions for a in attempts), 1),
                2,
            ),
            "device_tokens": sum(a.device_tokens for a in attempts),
            "prompt_tokens": sum(a.prompt_tokens for a in attempts),
            "completion_tokens": sum(a.completion_tokens for a in attempts),
            "usd": round(sum(a.usd for a in attempts), 4),
            "seconds": round(sum(a.seconds for a in attempts), 1),
        },
        "tasks": [r.to_dict() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path
