"""Action layer: act, stabilize, re-observe, and report the change."""

from __future__ import annotations

import pytest
from fake_device import make_session
from trees import form_screen, list_screen, node, settings_screen

from ios_mcp.errors import (
    ElementNotFound,
    ElementNotInteractable,
    InvalidArgument,
    NotSupported,
)


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
    table = next(n for n in digest.nodes if n.role == "table")
    scoped = await session.read_text(ref=table.ref)
    assert "Bluetooth" in scoped

    wifi = next(n for n in digest.nodes if n.label == "Wi-Fi")
    narrow = await session.read_text(ref=wifi.ref)
    assert "Wi-Fi" in narrow
    assert "Bluetooth" not in narrow


def _wheel_screen(value: str) -> dict:
    """One picker wheel, alone on the screen so nearest-match is unambiguous."""
    return node(
        "Application",
        label="Clock",
        h=852,
        children=[
            node("PickerWheel", value=value, x=140, y=520, w=110, h=216),
        ],
    )


#: Centre of the wheel in `_wheel_screen`, which is what a nudge taps beside.
_WHEEL_CENTRE_Y = 520 + 216 / 2


def _turning_wheel(options: list[str], start: int = 0, *, wraps: bool = False):
    """A wheel that moves one row per tap on a neighbouring row.

    A tap, not a drag, because that is what a real `UIPickerView` responds to
    predictably: see `_PICKER_ROW_PX`. `wraps` models the other half of what
    hardware showed, an hour wheel that never reaches an end and so can never
    be detected by waiting for the value to stop changing.
    """
    state = {"index": start}
    fake: dict = {}

    def on_gesture(path: str, body: dict | None) -> None:
        if not path.endswith("/wda/tap") or not body or "y" not in body:
            return
        step = 1 if body["y"] < _WHEEL_CENTRE_Y else -1
        moved = state["index"] + step
        state["index"] = moved % len(options) if wraps else max(0, min(len(options) - 1, moved))
        fake["session"].source_tree = _wheel_screen(options[state["index"]])

    return state, fake, on_gesture


async def test_a_picker_wheel_turns_until_it_reads_the_wanted_option() -> None:
    """The option asked for is not in the tree until the wheel shows it.

    A `PickerWheel` reports its selection and nothing else, so there is no list
    to look the answer up in and no way to compute the distance to it. Turning
    it and reading it back is the only route, which is why this is a loop and
    why the loop has to be bounded.
    """
    options = ["6", "7", "8", "9"]
    state, holder, on_gesture = _turning_wheel(options)
    session, fake, _ = make_session(_wheel_screen("6"), on_gesture=on_gesture)
    holder["session"] = fake
    digest = await session.observe()
    wheel = next(n for n in digest.nodes if n.role == "picker")

    result = await session.set_value("9", ref=wheel.ref)

    assert result.ok
    assert options[state["index"]] == "9"


async def test_a_picker_already_showing_the_option_is_left_alone() -> None:
    """Same contract as the switch: asking for what is already set does nothing."""
    _, holder, on_gesture = _turning_wheel(["6", "7"])
    session, fake, _ = make_session(_wheel_screen("6"), on_gesture=on_gesture)
    holder["session"] = fake
    digest = await session.observe()
    wheel = next(n for n in digest.nodes if n.role == "picker")

    await session.set_value("6", ref=wheel.ref)

    assert fake.gestures == []


async def test_a_picker_without_the_option_reports_what_it_saw() -> None:
    """The values seen are the only record of what the wheel contained.

    Failing with just "not found" would leave the agent with no way to learn
    the spelling of the options, since they are never all on screen at once.
    """
    options = ["Monday", "Tuesday", "Wednesday"]
    _, holder, on_gesture = _turning_wheel(options)
    session, fake, _ = make_session(_wheel_screen("Monday"), on_gesture=on_gesture)
    holder["session"] = fake
    digest = await session.observe()
    wheel = next(n for n in digest.nodes if n.role == "picker")

    with pytest.raises(ElementNotFound) as exc_info:
        await session.set_value("Friday", ref=wheel.ref)

    assert set(exc_info.value.details["seen"]) == set(options)


async def test_a_wheel_that_never_turns_stops_instead_of_spinning() -> None:
    """A dead control must cost a bounded number of gestures, not a budget."""
    session, fake, _ = make_session(_wheel_screen("6"))
    digest = await session.observe()
    wheel = next(n for n in digest.nodes if n.role == "picker")

    with pytest.raises(ElementNotFound):
        await session.set_value("9", ref=wheel.ref)

    assert len(fake.gestures) == 2, "one nudge per direction, then give up"


async def test_a_slider_is_dragged_from_its_thumb_to_the_requested_fraction() -> None:
    """Starting at the thumb rather than mid-track is what makes the ends work."""
    tree = node(
        "Application",
        label="Sounds",
        h=852,
        children=[node("Slider", name="volume", value="40%", x=60, y=300, w=270, h=32)],
    )
    session, fake, _ = make_session(tree)
    digest = await session.observe()
    slider = next(n for n in digest.nodes if n.role == "slider")

    await session.set_value("80%", ref=slider.ref)

    path, body = fake.gestures[-1]
    assert path.endswith("/wda/dragfromtoforduration")
    assert body is not None
    assert body["fromX"] == pytest.approx(60 + 0.4 * 270)
    assert body["toX"] == pytest.approx(60 + 0.8 * 270)


async def test_a_slider_position_has_to_be_a_position() -> None:
    tree = node(
        "Application",
        label="Sounds",
        h=852,
        children=[node("Slider", name="volume", value="40%", x=60, y=300, w=270, h=32)],
    )
    session, _, _ = make_session(tree)
    await session.observe()

    with pytest.raises(InvalidArgument):
        await session.set_value("loud", target="volume")


async def test_a_drawn_stepper_points_at_the_buttons_instead_of_guessing() -> None:
    """Composed steppers dissolve into their buttons, so this is the other case.

    An app that draws its own stepper leaves a container with no parts in the
    tree. There is nothing to nudge and nothing to read back, so saying so
    beats a gesture that cannot be verified.
    """
    tree = node(
        "Application",
        label="Reminders",
        h=852,
        children=[node("Stepper", name="interval_stepper", x=289, y=196, w=94, h=32)],
    )
    session, _, _ = make_session(tree)
    await session.observe()

    with pytest.raises(ElementNotInteractable) as exc_info:
        await session.set_value("3", target="interval_stepper")

    assert "Increment" in (exc_info.value.hint or "")


async def test_a_wrapping_wheel_stops_when_it_comes_round_again() -> None:
    """An hour wheel has no end, so "it stopped changing" never happens.

    On a real Clock alarm the wheel wraps from 12 back to 1 forever. The first
    version of this loop waited for the value to stop moving and would have
    spun until its cap on every wheel it could not satisfy. Stopping on a value
    already seen is what makes a wrapping wheel terminate, and it costs one
    step per distinct option rather than a fixed budget.
    """
    options = ["1", "2", "3", "4"]
    _, holder, on_gesture = _turning_wheel(options, wraps=True)
    session, fake, _ = make_session(_wheel_screen("1"), on_gesture=on_gesture)
    holder["session"] = fake
    digest = await session.observe()
    wheel = next(n for n in digest.nodes if n.role == "picker")

    with pytest.raises(ElementNotFound) as exc_info:
        await session.set_value("99", ref=wheel.ref)

    assert set(exc_info.value.details["seen"]) == set(options)
    assert len(fake.gestures) < 12, f"spun {len(fake.gestures)} times round a four-option wheel"
