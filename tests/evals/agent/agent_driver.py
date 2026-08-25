"""Run the agent over the task set, measured exactly like the oracle.

The point of the `Driver` shape is that the oracle and the agent are scored by
the same code reading the same counters, so the only difference in the report
is the agent's. Nothing here judges success: `run_task` reads the device model,
because what the agent claims and what the phone did are different questions.

Needs credentials. Without them the whole tier skips rather than pretending.
"""

from __future__ import annotations

import os
import shutil

import pytest
from ios_agent import SessionBackend, run_goal
from measure import Meter
from tasks import Task

from ios_mcp.session import IosSession


def credentials_available() -> bool:
    """An unset API key does not mean there are no credentials.

    The SDK also resolves an auth token or an `ant auth login` profile, so
    checking only the environment variable would skip a tier that could
    actually run.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if shutil.which("ant") is None:
        return False
    return os.path.isdir(os.path.expanduser("~/.config/anthropic/credentials"))


requires_credentials = pytest.mark.skipif(
    not credentials_available(),
    reason="no Anthropic credentials; set ANTHROPIC_API_KEY or run `ant auth login`",
)


async def drive(task: Task, session: IosSession, meter: Meter) -> None:
    """One goal, start to finish, with the cost copied onto the meter."""
    backend = SessionBackend(session)
    outcome = await run_goal(session, task.goal, backend=backend)

    # The backend counts at the point the call is made, which is the only
    # place that can distinguish an explicit observation from a screen that
    # arrived folded into an action's result.
    meter.observations = backend.stats.observations
    meter.actions = backend.stats.actions
    meter.device_tokens = backend.stats.device_tokens
    meter.charge_model(outcome.prompt_tokens, outcome.completion_tokens)
    meter.last_screen = backend.last_screen
