"""The MCP surface, exercised through a real client."""

from __future__ import annotations

import json

import pytest
from fake_device import make_session
from fastmcp import Client
from fastmcp.exceptions import ToolError
from trees import form_screen, settings_screen

from ios_mcp.config import Settings
from ios_mcp.server.app import build_server


@pytest.fixture
def server_with_session(monkeypatch):
    """A server whose ios_open_session attaches to a fake device."""
    mcp = build_server(Settings())
    ctx = mcp.ios_context

    state: dict = {}

    async def fake_open(device=None, *, app=None, fresh=False):
        session, fake, adapter = make_session(settings_screen())
        state["session"] = session
        state["fake"] = fake
        state["adapter"] = adapter
        ctx.session = session
        return session

    monkeypatch.setattr(ctx, "open", fake_open)
    return mcp, ctx, state


def payload(result):
    return result.structured_content or result.data


async def test_the_tool_surface_stays_small_enough_to_choose_from() -> None:
    """Too many confusable tools measurably degrades tool selection."""
    async with Client(build_server(Settings())) as client:
        tools = await client.list_tools()
    assert len(tools) <= 32, f"{len(tools)} tools is getting hard to choose between"


async def test_every_tool_documents_itself() -> None:
    async with Client(build_server(Settings())) as client:
        for tool in await client.list_tools():
            assert tool.description, f"{tool.name} has no description"
            assert tool.annotations is not None, f"{tool.name} has no annotations"


async def test_mutating_tools_are_not_marked_read_only() -> None:
    """A client that trusts readOnlyHint would run these without asking."""
    mutating = {
        "ios_tap",
        "ios_type",
        "ios_scroll",
        "ios_swipe",
        "ios_drag",
        "ios_set_value",
        "ios_handle_alert",
        "ios_launch_app",
        "ios_terminate_app",
        "ios_install_app",
        "ios_type_secret",
    }
    async with Client(build_server(Settings())) as client:
        for tool in await client.list_tools():
            if tool.name in mutating:
                assert not tool.annotations.readOnlyHint, f"{tool.name} claims to be read-only"


async def test_tools_that_can_do_damage_are_flagged_destructive() -> None:
    async with Client(build_server(Settings())) as client:
        by_name = {t.name: t for t in await client.list_tools()}
    for name in ("ios_tap", "ios_type", "ios_set_value", "ios_handle_alert"):
        assert by_name[name].annotations.destructiveHint, f"{name} should be destructive"


async def test_doctor_works_before_any_device_exists() -> None:
    async with Client(build_server(Settings())) as client:
        result = await client.call_tool("ios_doctor", {})
    data = payload(result)
    assert "summary" in data
    assert isinstance(data["checks"], list)


async def test_acting_without_a_session_says_what_to_do() -> None:
    async with Client(build_server(Settings())) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("ios_tap", {"target": "anything"})
    error = json.loads(str(exc_info.value))
    assert error["error"] == "device_unavailable"
    assert "ios_open_session" in error["hint"]


async def test_open_session_returns_the_first_screen(server_with_session) -> None:
    mcp, _, _ = server_with_session
    async with Client(mcp) as client:
        result = await client.call_tool("ios_open_session", {})
    data = payload(result)
    assert data["ok"] is True
    assert "screen" in data
    # The rendered form is what the model reads, and it carries the refs.
    assert data["screen"]["text"], "the first screen must come back with the session"
    assert data["screen"]["element_count"] > 0


async def test_observe_then_tap_by_ref(server_with_session) -> None:
    mcp, _, state = server_with_session
    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        observed = payload(await client.call_tool("ios_observe", {"include_elements": True}))
        ref = next(e["ref"] for e in observed["elements"] if e.get("label") == "Wi-Fi")
        result = payload(await client.call_tool("ios_tap", {"ref": ref}))

    assert result["ok"] is True
    assert result["target"]["resolved_via"] == "exact"
    assert state["fake"].taps()


async def test_a_destructive_tap_is_refused_with_a_signature(monkeypatch) -> None:
    mcp = build_server(Settings())
    ctx = mcp.ios_context

    async def fake_open(device=None, *, app=None, fresh=False):
        session, _, _ = make_session(form_screen())
        # No approval handler: the error carries the decision back to the caller.
        session.on_approval = None
        ctx.session = session
        return session

    monkeypatch.setattr(ctx, "open", fake_open)

    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("ios_tap", {"target": "Send"})

    error = json.loads(str(exc_info.value))
    assert error["error"] == "action_requires_approval"
    assert "signature" in error["details"]


async def test_passing_the_signature_back_lets_it_through(monkeypatch) -> None:
    mcp = build_server(Settings())
    ctx = mcp.ios_context
    holder = {}

    async def fake_open(device=None, *, app=None, fresh=False):
        session, fake, _ = make_session(form_screen())
        session.on_approval = None
        holder["fake"] = fake
        ctx.session = session
        return session

    monkeypatch.setattr(ctx, "open", fake_open)

    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("ios_tap", {"target": "Send"})
        signature = json.loads(str(exc_info.value))["details"]["signature"]

        result = payload(
            await client.call_tool("ios_tap", {"target": "Send", "approve": signature})
        )

    assert result["ok"] is True
    assert len(holder["fake"].taps()) == 1


async def test_a_missing_element_returns_a_structured_error(server_with_session) -> None:
    mcp, _, _ = server_with_session
    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("ios_tap", {"target": "Nothing Like This"})

    error = json.loads(str(exc_info.value))
    assert error["error"] == "element_not_found"
    assert "closest" in error["details"], "the agent needs candidates, not just a refusal"


async def test_wait_for_reports_failure_as_data_not_an_error(server_with_session) -> None:
    """A timeout is something to reason about, not an exception to recover from."""
    mcp, _, _ = server_with_session
    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        result = payload(await client.call_tool("ios_wait_for", {"text": "Nope", "timeout_s": 0.2}))
    assert result["ok"] is False


async def test_observe_omits_structured_elements_by_default() -> None:
    """They duplicate the rendered text at roughly twice the tokens, and both
    would be pushed into the model's context."""
    mcp = build_server(Settings())

    async def fake_open(device=None, *, app=None, fresh=False):
        session, _, _ = make_session(settings_screen())
        mcp.ios_context.session = session
        return session

    mcp.ios_context.open = fake_open
    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        default = payload(await client.call_tool("ios_observe", {}))
        verbose = payload(await client.call_tool("ios_observe", {"include_elements": True}))

    assert "elements" not in default
    assert "elements" in verbose
    assert len(json.dumps(verbose)) > len(json.dumps(default)) * 1.5


async def test_the_operator_prompt_ships_with_the_server() -> None:
    async with Client(build_server(Settings())) as client:
        result = await client.get_prompt("ios_operator")
    text = result.messages[0].content.text
    assert "observe" in text.lower()
    assert "ios_type_secret" in text


async def test_capabilities_tell_the_agent_what_this_device_supports(server_with_session) -> None:
    mcp, _, _ = server_with_session
    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        result = await client.read_resource("ios://capabilities")
    data = json.loads(result[0].text)
    assert data["device_kind"] == "simulator"
    assert data["supported"]["ios_set_permission"] is True


async def test_session_status_before_and_after_opening(server_with_session) -> None:
    mcp, _, _ = server_with_session
    async with Client(mcp) as client:
        before = payload(await client.call_tool("ios_session_status", {}))
        assert before["open"] is False

        await client.call_tool("ios_open_session", {})
        after = payload(await client.call_tool("ios_session_status", {}))

    assert after["open"] is True
    assert after["halted"] is False


async def test_the_trace_records_what_the_session_did(server_with_session) -> None:
    mcp, _, _ = server_with_session
    async with Client(mcp) as client:
        await client.call_tool("ios_open_session", {})
        await client.call_tool("ios_tap", {"target": "Wi-Fi"})
        trace = payload(await client.call_tool("ios_export_trace", {}))

    assert trace["summary"]["steps"] == 1
    assert trace["steps"][0]["action"] == "tap"


def test_the_server_reports_its_own_version_not_the_frameworks() -> None:
    """Without a version FastMCP reports its own, so a client asking what it
    was talking to was told "4.0.0", which is the framework."""
    from importlib import metadata

    server = build_server(Settings())
    assert server.version == metadata.version("ios-mcp")
    assert not server.version.startswith("4."), "that is FastMCP's version"
