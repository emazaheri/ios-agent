"""The injected failures have to actually fail.

An injection that quietly does nothing would leave every task passing and the
replan tests measuring a happy path. These assert the opposite of the oracle
tests: that without the recovery step the task really is unreachable, so the
three replan tasks are earning their place.
"""

from __future__ import annotations

import pytest
from screens import DeviceModel, build_session
from tasks import BY_NAME
from test_agent_evals import eval_settings

pytestmark = pytest.mark.agent


async def test_a_dead_switch_reports_success_and_never_moves() -> None:
    """The worst failure this system can have, reproduced deliberately.

    `set_value` returns ok. The switch is still off. An agent that trusts the
    return value declares victory on a phone that did not change.
    """
    task = BY_NAME["enable_airplane_mode"]
    model = task.model()
    session, _, _ = build_session(model, eval_settings(task))

    await session.observe()
    result = await session.set_value("on", target="Airplane Mode")

    assert result.ok is True, "the tap is accepted, which is what makes this dangerous"
    assert model.switches["airplane"] is False
    assert result.screen_changed is False


async def test_the_same_switch_moves_without_the_injection() -> None:
    """Control: the tap and the geometry are right, only the injection is wrong."""
    model = DeviceModel()
    session, _, _ = build_session(model, eval_settings(BY_NAME["enable_bold_text"]))

    await session.observe()
    await session.set_value("on", target="Airplane Mode")

    assert model.switches["airplane"] is True


async def test_a_sub_pane_deep_link_succeeds_and_does_nothing() -> None:
    """`App-prefs:root=WIFI` on iOS 26: accepted, ignored, no error to catch."""
    task = BY_NAME["open_wifi_pane"]
    model = task.model()
    session, _, _ = build_session(model, eval_settings(task))

    await session.observe()
    result = await session.open_url("App-prefs:root=WIFI")

    assert result.ok is True
    assert model.urls_opened == ["App-prefs:root=WIFI"]
    assert model.screen == "settings_root", "the URL was honoured in name only"


async def test_settings_opens_on_whichever_pane_it_was_last_showing() -> None:
    """The plan is wrong before the first action, not because of one."""
    task = BY_NAME["reach_accessibility"]
    model = task.model()
    session, _, _ = build_session(model, eval_settings(task))

    digest = await session.observe()

    assert model.screen == "bluetooth"
    assert digest.title == "Bluetooth"
    assert "Accessibility" not in digest.render()


async def test_a_tap_on_a_switch_label_does_not_move_it() -> None:
    """Aiming at the row instead of the toggle is the geometry bug, reproduced.

    `_dedupe_colocated` takes semantics from the labelled row and geometry from
    the switch. If it ever stops doing that, taps land here and switches stop
    moving while still reporting success.
    """
    model = DeviceModel()
    session, _, _ = build_session(model, eval_settings(BY_NAME["enable_bold_text"]))
    await session.observe()

    model.tap(x=40.0, y=110.0)  # squarely on the "Airplane Mode" text

    assert model.switches["airplane"] is False


async def test_only_a_window_of_the_long_list_is_ever_reported() -> None:
    """Row 60 cannot be read without scrolling, which is the task's whole point."""
    task = BY_NAME["find_in_long_list"]
    model = task.model()
    session, _, _ = build_session(model, eval_settings(task))

    digest = await session.observe()

    assert "Contact 000" in digest.render()
    assert "Contact 060" not in digest.render()
