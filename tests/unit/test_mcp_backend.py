"""The agent driving the device over MCP instead of in-process.

This is the claim ADR 0006 left outstanding: that the server works for a
consumer with no special access. So the test uses a real `fastmcp.Client`
against a real `build_server()`, speaking the protocol, rather than reaching
into the server object. An in-process shortcut would exercise a path no client
has and prove nothing.

`ios_agent` may not import `ios_mcp.server` -- `test_layering.py` forbids it --
but a *test* may, because the test is standing in for the caller who wires the
two together. That asymmetry is the point rather than a loophole.

The comparison that matters is at the bottom: the same actions through both
backends must produce the same device tokens, or the two sets of numbers this
project reports are not comparable and the whole exercise is decorative.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from ios_agent.backend import SessionBackend
from ios_agent.mcp_backend import McpBackend
from screens import DeviceModel, Injection, build_session

from ios_mcp.config import Settings
from ios_mcp.devices.pool import Lease
from ios_mcp.errors import ElementNotFound, ErrorCode, IosAutomationError
from ios_mcp.server.app import build_server


def _fast_settings(*, confirm_destructive: bool = False) -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    cfg.policy.loop_detection_window = 50
    cfg.policy.confirm_destructive = confirm_destructive
    return cfg


@pytest.fixture
async def served(request: pytest.FixtureRequest):
    """A real server over a real client, backed by the scripted device.

    The server normally builds its own session from a `DevicePool`. Here the
    session is constructed first, against the fake device, and handed to the
    server's context so both backends drive the same screens and any difference
    in the numbers is the transport rather than the device.
    """
    marker = request.node.get_closest_marker("device_state")
    kwargs: dict[str, Any] = marker.kwargs if marker else {}
    cfg = _fast_settings(confirm_destructive=kwargs.pop("confirm_destructive", False))

    model = DeviceModel(**kwargs)
    session, fake, _ = build_session(model, cfg)

    server = build_server(cfg)
    server.ios_context.session = session  # type: ignore[attr-defined]
    server.ios_context.lease = Lease(  # type: ignore[attr-defined]
        device=session.lease.device,
        adapter=session.lease.adapter,
        session=session.lease.session,
    )

    async with Client(server) as client:
        yield client, model, session, fake


async def test_it_observes_the_screen_over_the_protocol(served: Any) -> None:
    client, _model, _session, _fake = served
    backend = McpBackend(client)

    screen = await backend.observe()

    assert "Airplane Mode" in screen
    assert backend.stats.observations == 1
    assert backend.stats.device_tokens > 0


async def test_an_action_returns_the_screen_it_produced(served: Any) -> None:
    """The lever the whole design rests on has to survive serialisation.

    If a tap over MCP came back without its resulting screen, the agent would
    have to spend an observation to learn what happened, and the
    observation-overhead argument would hold for one backend and not the other.
    """
    client, _model, _session, _fake = served
    backend = McpBackend(client)
    await backend.observe()

    reply = await backend.tap("Accessibility", idem_key="k1")

    assert "Display & Text Size" in reply
    assert backend.stats.actions == 1


async def test_it_drives_a_real_task_to_completion(served: Any) -> None:
    client, model, _session, _fake = served
    backend = McpBackend(client)

    await backend.observe()
    await backend.tap("Accessibility", idem_key="k1")
    await backend.tap("Display & Text Size", idem_key="k2")
    await backend.set_value("on", "Bold Text", idem_key="k3")

    assert model.switches["bold_text"] is True
    assert backend.stats.observations == 1, "the folded-in screens were not enough"
    assert backend.stats.actions == 3


async def test_a_typed_error_survives_the_round_trip(served: Any) -> None:
    """A JSON blob is not an error message.

    The server marshals `IosAutomationError` into a `ToolError` carrying its
    code, hint and candidate elements. Reconstructing the typed exception is
    what lets the tool layer treat a bad label the same way on both backends:
    as one recoverable turn rather than a lost run.
    """
    client, _model, _session, _fake = served
    backend = McpBackend(client)
    await backend.observe()

    with pytest.raises(IosAutomationError) as caught:
        await backend.tap("Nonexistent Row", idem_key="k1")

    assert caught.value.code is ErrorCode.ELEMENT_NOT_FOUND
    assert isinstance(caught.value, IosAutomationError)


@pytest.mark.device_state(injections=frozenset({Injection.DEAD_SWITCH}))
async def test_verification_works_the_same_over_mcp(served: Any) -> None:
    """The one pillar this phase kept must not be transport-specific.

    It judges from `screen_changed` and the delta, which the server serialises
    unchanged. If it read them differently here, a comparison between the
    backends would be measuring the verifier rather than the protocol.
    """
    client, model, _session, _fake = served
    backend = McpBackend(client)
    await backend.observe()

    replies = [await backend.set_value("on", "Airplane Mode", idem_key=f"k{i}") for i in range(5)]

    assert model.switches["airplane"] is False
    assert backend.stats.refusals > 0, "the escalation never fired over MCP"
    assert "Not run." in replies[-1]


@pytest.mark.device_state(screen="reset", confirm_destructive=True)
async def test_a_destructive_action_is_refused_over_mcp(served: Any) -> None:
    """Going through MCP does not bypass the gate either.

    The server has no elicitation handler in this configuration, so the call
    raises `action_requires_approval` carrying a signature. That is the
    documented human-in-the-loop path for a client that cannot ask, and it is
    what the agent's `interrupt()` hangs off.
    """
    client, _model, _session, fake = served
    backend = McpBackend(client)
    await backend.observe()

    with pytest.raises(IosAutomationError) as caught:
        await backend.tap("Erase All Content and Settings", idem_key="k1")

    assert caught.value.code is ErrorCode.ACTION_REQUIRES_APPROVAL
    assert caught.value.details.get("signature"), "no signature to approve with"
    assert fake.taps() == [], "a destructive tap reached the device before approval"


async def test_both_backends_report_comparable_numbers(served: Any) -> None:
    """The reason this backend exists: two paths, one unit of measurement.

    Device tokens are counted from the payload, and the server serialises the
    same dict `ActionResult.to_dict()` produces. If these diverged, every
    figure in the eval reports would depend on which transport produced it.
    """
    client, _model, session, _fake = served
    over_mcp = McpBackend(client)
    direct = SessionBackend(session)

    await over_mcp.observe()
    await over_mcp.tap("Accessibility", idem_key="m1")

    await direct.observe()
    await direct.tap("Back", idem_key="d1")

    assert over_mcp.stats.observations == direct.stats.observations == 1
    assert over_mcp.stats.actions == direct.stats.actions == 1
    # Same order of magnitude, from the same payload shape. Not equal, because
    # the two screens differ; equality here would mean the test was measuring
    # nothing.
    assert 0.5 < over_mcp.stats.device_tokens / direct.stats.device_tokens < 2.0


def test_the_backend_reaches_the_server_only_as_a_client() -> None:
    """The constraint that shaped this design, checked on imports.

    An in-process import of the server would have been simpler and would have
    proved nothing about whether anyone else can drive it. Read from the import
    graph rather than by scanning the text: a first version grepped the source
    and failed on its own docstring, which mentions `ios_mcp.server` precisely
    to explain why it is not imported.

    `test_layering.py` enforces this across the whole package; this asserts it
    for the one module where the temptation is real.
    """
    import ast
    from pathlib import Path

    module = Path(__file__).resolve().parents[2] / "agent" / "ios_agent" / "mcp_backend.py"
    tree = ast.parse(module.read_text())

    imported: set[str] = set()
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            imported.update(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module:
            imported.add(statement.module)

    assert not any(m.startswith("ios_mcp.server") for m in imported), imported
    # It does still depend on the error taxonomy, which is public surface.
    assert any(m.startswith("ios_mcp.errors") for m in imported)


def test_an_unrecognised_error_is_left_alone() -> None:
    """Only errors this project serialised get reconstructed."""
    from ios_agent.mcp_backend import _typed

    plain = RuntimeError("something else entirely")

    assert _typed(plain) is plain


def test_a_resolution_failure_keeps_its_candidates() -> None:
    """The hint is the recoverable part, so it has to survive the round trip."""
    from ios_agent.mcp_backend import _typed

    original = ElementNotFound(
        "no such element", hint="Try one of these", details={"closest": ["Wi-Fi"]}
    )
    rebuilt = _typed(RuntimeError(__import__("json").dumps(original.to_dict())))

    assert isinstance(rebuilt, IosAutomationError)
    assert rebuilt.code is ErrorCode.ELEMENT_NOT_FOUND
    assert rebuilt.hint == "Try one of these"
    assert rebuilt.details["closest"] == ["Wi-Fi"]


async def test_the_protocol_costs_no_extra_tokens(served: Any) -> None:
    """The measurement ADR 0006 predicted, now taken rather than assumed.

    Device tokens are identical because the server serialises exactly the dict
    `ActionResult.to_dict()` produces. The cost of MCP is latency alone, and
    measured on this machine it is about 1.6 ms per call against a real device
    snapshot of roughly 3,700 ms, which is 0.04%.

    Asserted as identical rather than approximate: if a payload ever gains or
    loses a field in transit, the two sets of numbers this project reports stop
    being comparable, and that should fail loudly.
    """
    client, _model, session, _fake = served
    over_mcp = McpBackend(client)
    direct = SessionBackend(session)

    await over_mcp.observe()
    mcp_observe = over_mcp.stats.device_tokens
    await direct.observe()
    direct_observe = direct.stats.device_tokens

    assert mcp_observe == direct_observe, (
        f"the same screen cost {mcp_observe} tokens over MCP and "
        f"{direct_observe} directly; the payloads have diverged"
    )
