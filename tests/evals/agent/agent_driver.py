"""Run the agent over the task set, measured exactly like the oracle.

The point of the `Driver` shape is that the oracle and the agent are scored by
the same code reading the same counters, so the only difference in the report
is the agent's. Nothing here judges success: `run_task` reads the device model,
because what the agent claims and what the phone did are different questions.

Needs a model. Which one is configuration, so the gate below has to be too: a
run against a local Ollama model needs no key at all, and hardcoding a check
for `ANTHROPIC_API_KEY` would skip a tier that could happily run.
"""

from __future__ import annotations

import importlib.util
import os
import shutil

import pytest
from ios_agent import AgentSettings, SessionBackend, run_goal
from ios_agent.config import KNOWN_EXTRAS, export_provider_credentials
from measure import Meter
from tasks import Task

from ios_mcp.session import IosSession

#: Where each provider looks for a key. An empty tuple means it needs none,
#: which is the whole appeal of running one locally. A provider absent from
#: this map is not assumed to be unusable: only its package is checked.
CREDENTIAL_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "azure_openai": ("AZURE_OPENAI_API_KEY",),
    "google_genai": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "mistralai": ("MISTRAL_API_KEY",),
    "ollama": (),
}


def _package_installed(provider: str) -> bool:
    package = KNOWN_EXTRAS.get(provider)
    if package is None:
        return True  # unknown provider; let init_chat_model have its say
    return importlib.util.find_spec(package.replace("-", "_")) is not None


def _has_credentials(provider: str) -> bool:
    names = CREDENTIAL_ENV.get(provider)
    if names is None:
        return True  # nothing known to check, so do not block the run
    if not names:
        return True  # runs locally, no key involved
    if any(os.environ.get(name) for name in names):
        return True
    if provider == "anthropic":
        # An unset key does not mean no credentials: the SDK also resolves an
        # `ant auth login` profile, and checking only the variable would skip
        # a tier that would have run.
        return shutil.which("ant") is not None and os.path.isdir(
            os.path.expanduser("~/.config/anthropic/credentials")
        )
    return False


def why_unavailable() -> str | None:
    """The reason this tier cannot run, or None if it can.

    Exports first, for the same reason the loop does: a key sitting in `.env`
    is real, and reporting "no credentials" because nothing had copied it into
    the environment yet would skip a tier that was ready to run.
    """
    export_provider_credentials()
    cfg = AgentSettings()
    if not _package_installed(cfg.provider):
        return f"{cfg.describe()}: {cfg.missing_package_hint()}"
    if not _has_credentials(cfg.provider):
        names = " or ".join(CREDENTIAL_ENV.get(cfg.provider, ()))
        return f"{cfg.describe()}: no credentials; set {names or 'the provider credential'}"
    return None


_unavailable = why_unavailable()
requires_a_model = pytest.mark.skipif(_unavailable is not None, reason=_unavailable or "")


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
    meter.refusals = backend.stats.refusals
    meter.charge_model(outcome.prompt_tokens, outcome.completion_tokens)
    meter.last_screen = backend.last_screen
