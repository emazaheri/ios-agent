"""End-to-end tests against a real iOS Simulator and real WebDriverAgent.

These are the tests that would catch anything the fakes get wrong about how
WDA actually behaves. They are slow and are skipped when no runtime exists.
"""

from __future__ import annotations

import pytest
from simulator_support import requires_simulator

from ios_mcp.devices.discovery import list_simulators
from ios_mcp.devices.doctor import run_doctor
from ios_mcp.errors import IosAutomationError
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


async def _reachable_panes(session: IosSession) -> list:
    """Every Settings pane one tap from the root, and the root itself.

    Enough surface to find a compound control if the build has one, and
    bounded so the test cannot wander.
    """
    digests = [await session.observe()]
    roots = [n.label for n in digests[0].nodes if n.role in ("button", "cell") and n.label][:8]
    for label in roots:
        try:
            result = await session.tap(target=label)
        except IosAutomationError:
            continue
        digests.append(result.digest or await session.observe())
        try:
            await session.tap(target="Back")
        except IosAutomationError:
            await session.open_url("App-prefs:root")
    return digests


async def test_a_real_compound_control_keeps_its_value(session: IosSession) -> None:
    """A slider or a wheel, read off a real screen rather than a fixture.

    The bug this guards is a merge: iOS wraps a wheel in a `Picker` whose rect
    is the union of its columns, and the container carries an id where the
    wheel carries only a value, so the wrong one used to survive. A value that
    comes back empty here is that bug returning.

    Skips rather than fails when the build has neither. A simulator's Settings
    is a reduced build: the one this was written against (iOS 27, iPhone 18
    Pro) has no Date & Time pane and no Clock app at all, so there is no stock
    wheel to reach. That is a fact about the simulator, not about the code, and
    a test that lied about it would be worse than one that says so.
    """
    controls = [
        n
        for digest in await _reachable_panes(session)
        for n in digest.nodes
        if n.role in ("slider", "picker")
    ]
    if not controls:
        pytest.skip("no slider or picker wheel on this Settings build")

    for control in controls:
        assert control.value, f"{control.role} came back with no value: {control.render()}"


async def test_a_real_slider_can_be_set(session: IosSession) -> None:
    """Dragging the thumb, which is the half no fake can model.

    Restored in a `finally`, the way the device tier restores its one switch:
    a test that leaves a setting moved has changed the machine it ran on.
    """
    slider = next(
        (
            n
            for digest in await _reachable_panes(session)
            for n in digest.nodes
            if n.role == "slider"
        ),
        None,
    )
    if slider is None:
        pytest.skip("no slider on this Settings build")
    before = slider.value

    try:
        result = await session.set_value("80%", target=slider.identifier or slider.label or "")
        assert result.ok
        after = next(
            n for n in (result.digest or await session.observe()).nodes if n.role == "slider"
        )
        assert after.value != before, f"the slider never moved off {before!r}"
    finally:
        if before:
            await session.set_value(before, target=slider.identifier or slider.label or "")
