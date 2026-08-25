"""End-to-end tests against a real iOS Simulator and real WebDriverAgent.

These are the tests that would catch anything the fakes get wrong about how
WDA actually behaves. They are slow and are skipped when no runtime exists.
"""

from __future__ import annotations

import pytest
from simulator_support import requires_simulator

from ios_mcp.devices.discovery import list_simulators
from ios_mcp.devices.doctor import run_doctor
from ios_mcp.session import IosSession

pytestmark = [pytest.mark.simulator, requires_simulator]


async def test_the_doctor_reports_the_simulator_as_usable() -> None:
    report = await run_doctor()
    assert report.can_use_simulator, report.render()


async def test_simulators_are_discovered_with_plausible_metadata() -> None:
    devices = await list_simulators()
    assert devices, "at least one simulator should exist"
    for d in devices:
        assert d.kind == "simulator"
        assert d.os_version[0].isdigit(), f"bad version {d.os_version!r}"


async def test_a_session_opens_and_reads_the_settings_app(session: IosSession) -> None:
    """The whole stack: boot, WDA launch, session, snapshot, digest."""
    digest = await session.observe()
    assert digest.nodes, digest.render()
    labels = {n.label for n in digest.nodes}
    assert any(
        expected in labels for expected in ("General", "Wi-Fi", "Airplane Mode", "Settings")
    ), f"unexpected Settings screen: {digest.render()[:600]}"


async def test_the_digest_stays_within_its_token_budget(session: IosSession) -> None:
    """Against real UIKit output, not a synthetic fixture."""
    digest = await session.observe()
    assert digest.estimated_tokens() <= session.settings.digest.token_budget * 1.1


async def test_a_real_screenshot_comes_back_as_png(session: IosSession) -> None:
    png = await session.screenshot()
    assert png.startswith(b"\x89PNG"), "not a PNG"
    assert len(png) > 10_000


async def test_tapping_a_row_navigates(session: IosSession) -> None:
    before = await session.observe()
    result = await session.tap(target="General")
    assert result.screen_changed, "tapping General should navigate"
    after = result.digest or await session.observe()
    assert after.fingerprint != before.fingerprint


async def test_scrolling_moves_the_content(session: IosSession) -> None:
    before = await session.observe()
    result = await session.scroll("down")
    assert result.screen_changed, "the Settings list should scroll"
    _ = before


async def test_scroll_until_finds_an_item_further_down(session: IosSession) -> None:
    result = await session.scroll("down", until="Privacy", max_scrolls=15)
    assert "found" in (result.note or ""), result.note


async def test_a_deep_link_opens_a_specific_settings_pane(session: IosSession) -> None:
    """iOS 26 renamed the Settings scheme from `prefs:` to `App-prefs:`."""
    result = await session.open_url("App-prefs:root=General")
    digest = result.digest or await session.observe()
    assert digest.nodes, "the General pane should render"


async def test_an_invalid_deep_link_fails_loudly(session: IosSession) -> None:
    """A silently ignored bad URL would leave the agent acting on the wrong screen."""
    from ios_mcp.errors import IosAutomationError

    with pytest.raises(IosAutomationError):
        await session.open_url("prefs:root=General")  # the retired iOS 25 scheme


async def test_launching_and_reading_a_second_app(session: IosSession) -> None:
    await session.launch_app("com.apple.mobilesafari", fresh=True)
    digest = await session.observe()
    assert digest.app == "com.apple.mobilesafari", digest.app


async def test_typing_into_a_real_text_field(session: IosSession) -> None:
    """Type into whatever search field Settings offers, which is always present."""
    await session.open_url("App-prefs:root")
    digest = await session.observe()
    field = next((n for n in digest.nodes if n.role in ("searchfield", "textfield")), None)
    if field is None:
        pytest.skip(f"no search field on this Settings build: {digest.render()[:300]}")

    result = await session.type_text("Airplane", ref=field.ref)

    assert result.ok
    after = result.digest or await session.observe()
    assert any("Airplane" in (n.text or "") for n in after.nodes), after.render()[:400]


async def test_the_session_recovers_from_a_killed_runner(session: IosSession) -> None:
    """The property that matters over a long run: outliving a WDA crash."""
    from ios_mcp.devices.shell import run

    await session.observe()
    await run(
        "xcrun",
        "simctl",
        "terminate",
        session.lease.device.udid,
        "com.facebook.WebDriverAgentRunner.xctrunner",
        timeout=30.0,
    )
    digest = await session.observe()
    assert digest.nodes, "the session should have healed and re-observed"
