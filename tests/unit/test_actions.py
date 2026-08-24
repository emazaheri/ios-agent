"""Action layer: act, stabilize, re-observe, and report the change."""

from __future__ import annotations

import pytest
from fake_device import make_session
from trees import form_screen, list_screen, node, settings_screen

from ios_mcp.errors import ElementNotInteractable, InvalidArgument, NotSupported


async def test_tap_hits_the_centre_of_the_resolved_element() -> None:
    session, fake, _ = make_session(settings_screen())
    digest = await session.observe()
    switch = next(n for n in digest.nodes if n.role == "switch")

    result = await session.tap(ref=switch.ref)

    assert result.ok
    assert result.target is not None
    assert result.target.resolved_via == "exact"
    x, y = fake.taps()[-1]
    assert switch.rect.contains(x, y)


async def test_tap_by_description_needs_no_prior_observation() -> None:
    session, _, _ = make_session(settings_screen())
    result = await session.tap(target="Wi-Fi")
    assert result.ok
    assert result.target is not None
    assert result.target.label == "Wi-Fi"


async def test_an_action_returns_the_resulting_screen() -> None:
    """This is what halves the round-trips of an observe/act/observe loop."""

    def toggle(path: str, body: dict | None) -> None:
        if path.endswith("/wda/tap"):
            fake.source_tree = settings_screen(airplane_on=True)

    session, fake, _ = make_session(settings_screen(airplane_on=False), on_gesture=toggle)
    await session.observe()

    result = await session.tap(target="Airplane Mode", role="switch")

    assert result.screen_changed is True
    payload = result.to_dict()
    assert "change" in payload or "screen" in payload


async def test_a_change_on_a_similar_screen_is_reported_as_a_delta() -> None:
    def toggle(path: str, body: dict | None) -> None:
        if path.endswith("/wda/tap"):
            fake.source_tree = settings_screen(airplane_on=True)

    session, fake, _ = make_session(settings_screen(airplane_on=False), on_gesture=toggle)
    await session.observe()

    result = await session.tap(target="Airplane Mode", role="switch")

    assert result.delta is not None, "same screen, so a delta is enough"
    assert result.digest is None, "a full digest would be wasted tokens here"
    assert len(result.delta.changed) == 1
    before, after = result.delta.changed[0]
    assert before.value == "0" and after.value == "1"


async def test_navigating_to_a_new_screen_returns_a_full_digest() -> None:
    def navigate(path: str, body: dict | None) -> None:
        if path.endswith("/wda/tap"):
            fake.source_tree = form_screen()

    session, fake, _ = make_session(settings_screen(), on_gesture=navigate)
    await session.observe()

    result = await session.tap(target="Wi-Fi")

    assert result.screen_changed is True
    assert result.digest is not None, "a different screen needs a full digest"
    assert any(n.label == "Send" for n in result.digest.nodes)


async def test_an_action_that_changes_nothing_says_so() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()
    result = await session.tap(target="Wi-Fi")
    assert result.screen_changed is False
    assert result.delta is not None and result.delta.empty
    assert "no visible change" in result.delta.render()


async def test_tapping_a_disabled_element_is_refused_with_a_reason() -> None:
    session, _, _ = make_session(form_screen())
    digest = await session.observe()
    disabled = next(n for n in digest.nodes if not n.enabled)

    with pytest.raises(ElementNotInteractable) as exc_info:
        await session.tap(ref=disabled.ref)
    assert "disabled" in exc_info.value.message


async def test_typing_focuses_the_field_first() -> None:
    session, fake, _ = make_session(form_screen())
    await session.observe()

    await session.type_text("hello@example.com", target="To:")

    assert fake.taps(), "the field must be focused before typing"
    assert "hello@example.com" in fake.typed()


async def test_typing_with_submit_sends_a_newline() -> None:
    session, fake, _ = make_session(form_screen())
    await session.observe()
    await session.type_text("query", target="Subject:", submit=True)
    assert fake.typed().endswith("\n")


async def test_setting_a_switch_already_in_the_wanted_state_does_nothing() -> None:
    """Tapping a switch that is already on would turn it off."""
    session, fake, _ = make_session(settings_screen(airplane_on=True))
    await session.observe()
    before = len(fake.taps())

    await session.set_value("on", target="Airplane Mode")

    assert len(fake.taps()) == before, "an already-on switch must not be toggled"


async def test_setting_a_switch_that_needs_changing_taps_it() -> None:
    session, fake, _ = make_session(settings_screen(airplane_on=False))
    await session.observe()
    before = len(fake.taps())
    await session.set_value("on", target="Airplane Mode")
    assert len(fake.taps()) == before + 1


async def test_set_value_refuses_a_role_that_has_no_value() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()
    with pytest.raises(ElementNotInteractable) as exc_info:
        await session.set_value("x", target="Wi-Fi")
    assert "ios_tap" in (exc_info.value.hint or "")


async def test_scroll_gestures_inside_the_scrollable_area() -> None:
    session, fake, _ = make_session(list_screen(rows=100))
    await session.observe()

    await session.scroll("down")

    drags = [b for p, b in fake.gestures if p.endswith("dragfromtoforduration")]
    assert len(drags) == 1
    drag = drags[0]
    # Scrolling down drags the content upward.
    assert drag["fromY"] > drag["toY"]
    # The gesture stays inset from the screen edges so it does not trigger
    # system gestures instead of scrolling.
    assert 0 < drag["toY"] < 852


async def test_scroll_until_stops_as_soon_as_the_text_appears() -> None:
    state = {"scrolls": 0}

    def scroll(path: str, body: dict | None) -> None:
        if not path.endswith("dragfromtoforduration"):
            return
        state["scrolls"] += 1
        # A real scroll moves the content, and on the third one the row we are
        # looking for comes into view.
        rows = [
            node("Cell", label=f"Row {i + state['scrolls'] * 5}", name=f"row{i}", y=100 + i * 44)
            for i in range(10)
        ]
        if state["scrolls"] >= 3:
            rows.append(node("Cell", label="Target Row", name="target", y=560))
        fake.source_tree = node("Application", h=852, children=rows)

    session, fake, _ = make_session(list_screen(rows=100), on_gesture=scroll)
    await session.observe()

    result = await session.scroll("down", until="Target Row", max_scrolls=10)

    assert state["scrolls"] == 3, "must stop the moment the text appears, not keep scrolling"
    assert "found" in (result.note or "")


async def test_scroll_until_gives_up_when_the_list_stops_moving() -> None:
    """A list that has reached its end must not spin for max_scrolls."""
    session, fake, _ = make_session(list_screen(rows=3))
    await session.observe()

    result = await session.scroll("down", until="Nonexistent", max_scrolls=20)

    drags = [p for p, _ in fake.gestures if p.endswith("dragfromtoforduration")]
    assert len(drags) < 5, "should detect the unchanged screen and stop"
    assert "not found" in (result.note or "")


async def test_an_unknown_scroll_direction_is_rejected() -> None:
    session, _, _ = make_session(settings_screen())
    await session.observe()
    with pytest.raises(InvalidArgument):
        await session.scroll("sideways")  # type: ignore[arg-type]


async def test_wait_for_reports_success_when_the_text_appears() -> None:
    session, _, _ = make_session(settings_screen())
    result = await session.wait_for("Bluetooth", timeout_s=0.5)
    assert result.ok is True
    assert "appeared" in (result.note or "")


async def test_wait_for_reports_failure_without_raising() -> None:
    session, _, _ = make_session(settings_screen())
    result = await session.wait_for("Nonexistent", timeout_s=0.05)
    assert result.ok is False
    assert "did not" in (result.note or "")


async def test_an_alert_is_surfaced_on_every_action() -> None:
    session, fake, _ = make_session(settings_screen())
    await session.observe()
    fake.alert_text = '"Maps" Would Like to Use Your Location'

    result = await session.tap(target="Wi-Fi")

    assert result.alert is not None
    payload = result.to_dict()
    assert "ios_handle_alert" in payload["hint"]


async def test_handling_an_alert_clears_it() -> None:
    session, fake, _ = make_session(settings_screen())
    fake.alert_text = "Allow?"
    await session.observe()

    result = await session.handle_alert("accept")

    assert result.alert is None


async def test_press_button_rejects_an_unknown_name() -> None:
    session, _, _ = make_session(settings_screen())
    with pytest.raises(InvalidArgument) as exc_info:
        await session.press_button("teleport")
    assert "home" in (exc_info.value.hint or "")


async def test_open_url_uses_simctl_on_a_simulator() -> None:
    session, _, adapter = make_session(settings_screen(), kind="simulator")
    await session.open_url("prefs:root=WIFI")
    assert adapter.urls_opened == ["prefs:root=WIFI"]


async def test_setting_permissions_is_refused_on_a_real_device() -> None:
    session, _, _ = make_session(settings_screen(), kind="device")
    with pytest.raises(NotSupported) as exc_info:
        await session.set_permission("com.example.app", "photos", True)
    assert "ios_handle_alert" in (exc_info.value.hint or "")


async def test_read_text_scopes_to_one_element() -> None:
    session, _, _ = make_session(settings_screen())
    digest = await session.observe()
    cell = next(n for n in digest.nodes if n.label == "Airplane Mode" and n.role == "cell")
    text = await session.read_text(ref=cell.ref)
    assert "Airplane Mode" in text
    assert "Bluetooth" not in text
