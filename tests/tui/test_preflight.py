"""Finding out there is no model before spending a minute on a device.

The check this covers is the cheapest one in the system and it used to run
last, behind the most expensive setup: a missing key surfaced after a cold
simulator had booted and WebDriverAgent had started.

Two providers behave differently and that is why the probe asks two questions.
OpenAI raises when the model is constructed, so building it is a real check.
Anthropic constructs happily and raises on the first call, minutes later, so
the environment is inspected as well.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from ios_agent import AgentSettings, probe_provider
from ios_tui.app import IosAgentApp
from ios_tui.events import DeviceReady
from ios_tui.runner import GoalRunner
from ios_tui.widgets import StatusBar
from screens import DeviceModel, build_session
from tui_harness import ScriptedModel, settings


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither a key in the developer's environment nor one in `.env`.

    The `chdir` is the load-bearing half. `tests/conftest.py` switches the
    `env_file` off for `Settings` and `AgentSettings`, but `probe_provider`
    calls `export_provider_credentials`, which reads `.env` from the working
    directory with `dotenv_values` and so goes around that entirely. Without
    a clean directory these tests pass on a machine with a key and fail on one
    without, which is the wrong way round.
    """
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_a_provider_that_checks_eagerly_is_reported_as_unusable() -> None:
    """OpenAI raises at construction, so this is a fact rather than a guess."""
    probe = probe_provider(AgentSettings(provider="openai", model="gpt-5.6-sol"))

    assert probe.status == "fail"
    assert probe.usable is False
    assert "OPENAI_API_KEY" in (probe.remedy or "")


def test_a_provider_that_checks_late_is_a_warning_not_a_refusal() -> None:
    """Anthropic builds without a credential and fails on the first call.

    A missing variable is not proof of a missing credential: the SDK also takes
    an auth token or an `ant auth login` profile. So this warns and lets the
    run proceed, because refusing here would lock out everyone using one.
    """
    probe = probe_provider(AgentSettings(provider="anthropic", model="claude-opus-5"))

    assert probe.status == "warn"
    assert probe.usable is True, "a warning must not stop a run"
    assert "ANTHROPIC_API_KEY" in probe.detail


def test_a_credential_in_the_environment_satisfies_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    probe = probe_provider(AgentSettings(provider="anthropic", model="claude-opus-5"))

    assert probe.status == "ok"
    assert probe.detail.startswith("anthropic:claude-opus-5")


def test_a_missing_integration_package_names_the_extra_to_install() -> None:
    """The other way a provider is unreachable, and the one already handled."""
    probe = probe_provider(AgentSettings(provider="ollama", model="llama3"))

    assert probe.status == "fail"
    assert "uv sync --extra ollama" in (probe.remedy or "")


def test_a_provider_with_no_variable_to_check_is_not_warned_about() -> None:
    """Bedrock and Vertex use their cloud's credential chain.

    Warning about a variable that was never going to be set is noise, and a
    warning seen on every start is a warning nobody reads.
    """
    from ios_agent.config import _CREDENTIAL_VARS

    assert _CREDENTIAL_VARS["bedrock_converse"] == ()
    assert _CREDENTIAL_VARS["ollama"] == ()


# -- the app ---------------------------------------------------------------


class _Runner(GoalRunner):
    """Records whether a device was ever asked for."""

    def __init__(self, sink: object, agent: AgentSettings) -> None:
        super().__init__(sink, settings(), agent)  # type: ignore[arg-type]
        self.started = 0

    async def start(self) -> object:
        self.started += 1
        session, _, _ = build_session(DeviceModel(), settings())
        self.session = session
        self.sink.emit(
            DeviceReady(
                lease={"device": {"name": "iPhone 17", "os_version": "26.5", "kind": "simulator"}}
            )
        )
        return session

    async def close(self) -> None:
        return None


def _app(provider: str, model: str, *, manual: bool = False) -> IosAgentApp:
    agent = AgentSettings(provider=provider, model=model)
    return IosAgentApp(lambda sink: _Runner(sink, agent), manual=manual)


async def _settle(app: IosAgentApp, until: object, timeout: float = 10.0) -> None:
    assert callable(until)
    async with asyncio.timeout(timeout):
        while not until():
            await asyncio.sleep(0.02)


async def test_an_unusable_model_stops_before_a_device_is_acquired() -> None:
    """The whole point: fail in a second, not after a simulator has booted."""
    app = _app("openai", "gpt-5.6-sol")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "failed")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 0, "a device was acquired for a run that could not happen"

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "no model" in written
        assert "no device was acquired" in written


async def test_a_warning_does_not_stop_the_run() -> None:
    app = _app("anthropic", "claude-opus-5")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 1, "a warning stopped a run it should only have flagged"

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "ANTHROPIC_API_KEY" in written


async def test_the_model_is_named_before_the_first_goal() -> None:
    """You cannot see which model you are about to spend money on, otherwise.

    The status bar used to fill this in from `GoalStarted`, so it was blank
    until after the decision it describes had been taken.
    """
    app = _app("anthropic", "claude-opus-5")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        await pilot.pause()

        assert app.query_one(StatusBar).model.startswith("anthropic:claude-opus-5")


async def test_manual_mode_never_asks_for_a_model() -> None:
    """It drives the device by hand and is meant to work with no key at all.

    A preflight here would refuse to start the one mode that has no use for a
    provider, on a machine where none is configured.
    """
    app = _app("openai", "gpt-5.6-sol", manual=True)
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")
        await pilot.pause()

        runner = app.runner
        assert isinstance(runner, _Runner)
        assert runner.started == 1, "manual mode was blocked by a model it does not use"
        assert app.query_one(StatusBar).model == ""


def test_the_scripted_model_in_tests_is_not_probed() -> None:
    """A run with an injected model never touches a provider.

    The probe reads `runner.agent`, which describes the configured provider, so
    an injected model would still be preflighted against a provider it does not
    use. Recorded because the failure would be a test suite that needs an API
    key.
    """
    runner = GoalRunner(object(), settings(), model=ScriptedModel([]))  # type: ignore[arg-type]
    assert runner._model is not None


# -- after a goal fails ----------------------------------------------------


async def test_a_failed_goal_does_not_report_the_device_as_broken() -> None:
    """A goal that blew up did not take the phone with it.

    The session is still attached and still drivable, so a header reading
    "failed" over a working device says the tool is broken when one request
    was. `Failed.where` exists to tell the two apart and was being ignored.
    """
    from ios_tui.events import Failed

    app = _app("anthropic", "claude-opus-5")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app._apply(Failed(where="run", message="the model refused"))
        await pilot.pause()

        assert app.query_one(StatusBar).state == "ready"
        assert app.runner is not None and app.runner.session is not None


async def test_a_failure_to_acquire_is_still_reported_as_failed() -> None:
    """The other half of the distinction: no device means genuinely stuck."""
    from ios_tui.events import Failed

    app = _app("anthropic", "claude-opus-5")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app._apply(Failed(where="acquire", message="no WebDriverAgent"))
        await pilot.pause()

        assert app.query_one(StatusBar).state == "failed"


async def test_a_failed_goal_repeats_the_remedy_the_probe_gave() -> None:
    """A wall of the provider's own text with no next step is a dead end.

    The remedy comes from the startup probe rather than from reading the
    error, so it is offered only when the model was already flagged and is
    never guessed at from the provider's wording.
    """
    from ios_tui.events import Failed

    app = _app("anthropic", "claude-opus-5")  # no credential, so the probe warns
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app._apply(Failed(where="run", message="Anthropic authentication failed: ..."))
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "set ANTHROPIC_API_KEY" in written


async def test_the_remedy_is_offered_once_and_not_after_every_failure() -> None:
    """It is advice, not a running commentary.

    Counted as whole lines rather than as a substring: the startup warning
    ends with the same sentence, and it is a different line saying a different
    thing at a different time. What must not repeat is the standalone remedy,
    once per failure.
    """
    from ios_tui.events import Failed

    app = _app("anthropic", "claude-opus-5")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        for _ in range(3):
            app._apply(Failed(where="run", message="Anthropic authentication failed: ..."))
        await pilot.pause()

        standalone = [
            line.text.strip()
            for line in app.transcript.lines
            if line.text.strip().startswith("set ANTHROPIC_API_KEY")
        ]
        assert len(standalone) == 1, f"the remedy repeated: {standalone}"


async def test_nothing_is_suggested_when_the_model_was_never_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A working provider that fails for some other reason gets no advice
    about credentials, because guessing would send someone after the wrong
    thing."""
    from ios_tui.events import Failed

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")

    app = _app("anthropic", "claude-opus-5")
    async with app.run_test(size=(110, 26)) as pilot:
        await _settle(app, lambda: app.query_one(StatusBar).state == "ready")

        app._apply(Failed(where="run", message="the device stopped responding"))
        await pilot.pause()

        written = "\n".join(line.text for line in app.transcript.lines)
        assert "ANTHROPIC_API_KEY" not in written
