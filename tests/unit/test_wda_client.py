"""Transport-level behaviour of the WDA client."""

from __future__ import annotations

import httpx
import pytest
from fake_wda import FakeWda

from ios_mcp.errors import (
    ElementNotFound,
    ElementNotInteractable,
    ElementStale,
    RunnerCrashed,
    SessionLost,
    WdaError,
)
from ios_mcp.wda.client import WdaClient


async def test_unwraps_the_value_envelope(wda_client: WdaClient) -> None:
    status = await wda_client.status()
    assert status.ready is True
    assert status.ios_version == "18.2"


async def test_screenshot_is_base64_decoded(wda_client: WdaClient, fake_wda: FakeWda) -> None:
    await wda_client.post("/session")
    png = await wda_client.screenshot_png("S1")
    assert png == fake_wda.screenshot_bytes


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("no such element", ElementNotFound),
        ("stale element reference", ElementStale),
        ("invalid element state", ElementNotInteractable),
        ("element not interactable", ElementNotInteractable),
        ("invalid session id", SessionLost),
        ("some unmapped wda error", WdaError),
    ],
)
async def test_wda_error_vocabulary_maps_to_typed_errors(
    wda_client: WdaClient, fake_wda: FakeWda, kind: str, expected: type[Exception]
) -> None:
    fake_wda.fail_next("/source", kind)
    with pytest.raises(expected) as exc_info:
        await wda_client.get("/session/S1/source")
    assert exc_info.value.details["wda_error"] == kind


async def test_element_not_found_carries_an_actionable_hint(
    wda_client: WdaClient, fake_wda: FakeWda
) -> None:
    fake_wda.fail_next("/source", "no such element")
    with pytest.raises(ElementNotFound) as exc_info:
        await wda_client.get("/session/S1/source")
    assert "ios_observe" in (exc_info.value.hint or "")


async def test_session_errors_are_marked_recoverable(
    wda_client: WdaClient, fake_wda: FakeWda
) -> None:
    fake_wda.fail_next("/source", "invalid session id")
    with pytest.raises(SessionLost) as exc_info:
        await wda_client.get("/session/S1/source")
    assert exc_info.value.recoverable is True


async def test_element_not_found_is_not_recoverable(
    wda_client: WdaClient, fake_wda: FakeWda
) -> None:
    """A missing element is the agent's problem to solve, not the plumbing's."""
    fake_wda.fail_next("/source", "no such element")
    with pytest.raises(ElementNotFound) as exc_info:
        await wda_client.get("/session/S1/source")
    assert exc_info.value.recoverable is False


async def test_connection_refused_becomes_runner_crashed(
    wda_client: WdaClient, fake_wda: FakeWda
) -> None:
    fake_wda.crash()
    with pytest.raises(RunnerCrashed) as exc_info:
        await wda_client.get("/status")
    assert exc_info.value.recoverable is True


async def test_is_alive_reports_false_when_the_runner_is_down(
    wda_client: WdaClient, fake_wda: FakeWda
) -> None:
    assert await wda_client.is_alive() is True
    fake_wda.crash()
    assert await wda_client.is_alive() is False


async def test_read_timeout_suggests_lowering_snapshot_depth(wda_client: WdaClient) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    wda_client._http = httpx.AsyncClient(
        base_url="http://127.0.0.1:8100", transport=httpx.MockTransport(timeout)
    )
    with pytest.raises(RunnerCrashed) as exc_info:
        await wda_client.get("/session/S1/source")
    assert "max_depth" in (exc_info.value.hint or "")


async def test_non_json_error_body_is_reported_not_swallowed(wda_client: WdaClient) -> None:
    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    wda_client._http = httpx.AsyncClient(
        base_url="http://127.0.0.1:8100", transport=httpx.MockTransport(html)
    )
    with pytest.raises(WdaError, match="502"):
        await wda_client.get("/status2")
