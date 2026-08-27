"""Session lifecycle, snapshot tuning, and auto-heal."""

from __future__ import annotations

import pytest
from fake_wda import FakeWda

from ios_mcp.config import Settings
from ios_mcp.errors import RunnerCrashed
from ios_mcp.wda.client import WdaClient
from ios_mcp.wda.session import WdaSession


async def test_open_creates_a_session_and_applies_snapshot_settings(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    session_id = await wda_session.open()
    assert session_id == "S1"
    # These are what keep observation affordable; a session without them is a bug.
    assert fake_wda.settings_applied["snapshotMaxDepth"] == 50
    assert fake_wda.settings_applied["snapshotMaxChildren"] == 64
    # And `pageSourceExcludedAttributes` is deliberately not among them. It is
    # the documented fix for expensive attribute computation and it is a no-op
    # on `format=json`: 750 ms with it, 743 ms without. Asserting it was *sent*
    # is what let it look like an optimisation for the whole project's life.
    assert "pageSourceExcludedAttributes" not in fake_wda.settings_applied


async def test_open_does_not_terminate_the_foreground_app_on_teardown(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    """Killing whatever the user had open is hostile on a real phone."""
    await wda_session.open("com.apple.Preferences")
    create = next(b for m, p, b in fake_wda.calls if p == "/session" and m == "POST")
    assert create["capabilities"]["alwaysMatch"]["shouldTerminateApp"] is False
    assert create["capabilities"]["alwaysMatch"]["bundleId"] == "com.apple.Preferences"


async def test_fresh_launch_sets_force_app_launch(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    await wda_session.open("com.apple.Preferences", fresh=True)
    create = next(b for m, p, b in fake_wda.calls if p == "/session" and m == "POST")
    assert create["capabilities"]["alwaysMatch"]["forceAppLaunch"] is True


async def test_source_parses_into_a_snapshot_tree(wda_session: WdaSession) -> None:
    root = await wda_session.source()
    assert root.type == "Application"
    nodes = root.walk()
    labels = {n.label for n in nodes}
    assert "Airplane Mode" in labels
    switch = next(n for n in nodes if n.type == "Switch")
    assert switch.value == "0"
    assert switch.identifier == "airplane_switch"


async def test_booleans_survive_wdas_string_encoding(wda_session: WdaSession) -> None:
    """WDA sends isVisible as "1"/"0" strings, not JSON booleans."""
    root = await wda_session.source()
    hidden = [n for n in root.walk() if not n.visible]
    assert len(hidden) == 1
    assert hidden[0].rect.area == 0


async def test_alert_returns_none_when_no_alert_is_present(wda_session: WdaSession) -> None:
    assert await wda_session.alert() is None


async def test_alert_reports_text_and_buttons(wda_session: WdaSession, fake_wda: FakeWda) -> None:
    fake_wda.alert_text = '"Maps" Would Like to Use Your Location'
    alert = await wda_session.alert()
    assert alert is not None
    assert "Location" in alert.text
    assert alert.buttons == ("Cancel", "OK")


async def test_pasteboard_roundtrips_through_base64(wda_session: WdaSession) -> None:
    await wda_session.set_pasteboard("hello world")
    assert await wda_session.get_pasteboard() == "hello world"


async def test_a_dead_session_is_recreated_transparently(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    await wda_session.open()
    fake_wda.sessions_created = 0
    fake_wda.fail_next("/source", "invalid session id")

    root = await wda_session.source()  # must not raise

    assert root.type == "Application"
    assert fake_wda.sessions_created == 1
    assert wda_session.recovered_count == 1


async def test_a_crashed_runner_is_relaunched_and_the_app_restored(settings: Settings) -> None:
    fake = FakeWda()
    client = WdaClient("http://127.0.0.1:8100", settings.wda)
    client._http = fake.client_factory()

    relaunches: list[int] = []

    async def relaunch() -> str:
        relaunches.append(1)
        fake.restart(new_session_id="S1")
        return "http://127.0.0.1:8100"

    session = WdaSession(client, settings, relaunch=relaunch)
    await session.open("com.apple.Preferences")
    fake.crash()

    root = await session.source()

    assert root.type == "Application"
    assert len(relaunches) == 1
    assert session.recovered_count == 1
    # The app that was in the foreground before the crash is restored.
    assert session.bundle_id == "com.apple.Preferences"


async def test_a_crash_without_a_relaunch_hook_raises_with_a_clear_hint(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    await wda_session.open()
    fake_wda.crash()
    with pytest.raises(RunnerCrashed) as exc_info:
        await wda_session.source()
    assert "device pool" in (exc_info.value.hint or "")


async def test_auto_heal_can_be_disabled(settings: Settings) -> None:
    settings.wda.auto_heal = False
    fake = FakeWda()
    client = WdaClient("http://127.0.0.1:8100", settings.wda)
    client._http = fake.client_factory()
    session = WdaSession(client, settings)
    await session.open()
    fake.fail_next("/source", "invalid session id")
    with pytest.raises(Exception):  # noqa: B017 - SessionLost propagates untouched
        await session.source()
    assert session.recovered_count == 0


async def test_healing_is_attempted_only_once_per_call(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    """A persistently broken session must surface, not spin."""
    await wda_session.open()
    fake_wda.fail_next("/source", "invalid session id")
    fake_wda.fail_next("/source", "invalid session id")
    with pytest.raises(Exception):  # noqa: B017
        await wda_session.source()


async def test_app_lifecycle_tracks_the_foreground_bundle(wda_session: WdaSession) -> None:
    await wda_session.open()
    await wda_session.launch_app("com.apple.mobilesafari")
    assert wda_session.bundle_id == "com.apple.mobilesafari"
    assert await wda_session.app_state("com.apple.mobilesafari") == 4
    assert await wda_session.terminate_app("com.apple.mobilesafari") is True
    assert await wda_session.app_state("com.apple.mobilesafari") == 1


async def test_close_is_safe_on_an_already_dead_session(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    await wda_session.open()
    fake_wda.crash()
    await wda_session.close()  # must not raise
    assert wda_session.session_id is None


# -- locked devices ---------------------------------------------------------


async def test_a_locked_device_is_reported_clearly(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    """WebDriverAgent reports this as a wall of Apple error domains."""
    from ios_mcp.errors import DeviceLocked

    settings = wda_session.settings
    settings.wda.auto_heal = False
    await wda_session.open()
    fake_wda.locked = True

    with pytest.raises(DeviceLocked) as exc_info:
        await wda_session.source()
    assert "Auto-Lock" in (exc_info.value.hint or "")


async def test_a_slept_device_is_woken_and_the_call_retried(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    """A phone that merely slept is the commonest interruption of a long run."""
    await wda_session.open()
    fake_wda.locked = True

    root = await wda_session.source()  # must not raise

    assert root.type == "Application"
    assert fake_wda.unlock_calls == 1
    assert wda_session.recovered_count == 1


async def test_a_passcode_locked_device_surfaces_rather_than_looping(
    wda_session: WdaSession, fake_wda: FakeWda
) -> None:
    """WebDriverAgent cannot type a passcode, so retrying would never succeed."""
    from ios_mcp.errors import DeviceLocked

    await wda_session.open()
    fake_wda.locked = True
    fake_wda.passcode_locked = True

    with pytest.raises(DeviceLocked):
        await wda_session.source()
    assert fake_wda.unlock_calls == 1, "must try once, not spin"


async def test_a_sleeping_device_is_woken_rather_than_restarted(settings: Settings) -> None:
    """A sleeping phone stops answering and looks exactly like a hung runner.

    Waking costs a second; restarting the runner costs a minute, so the cheap
    explanation has to be tried first.
    """
    fake = FakeWda()
    client = WdaClient("http://127.0.0.1:8100", settings.wda)
    client._http = fake.client_factory()

    relaunches: list[int] = []

    async def relaunch() -> str:
        relaunches.append(1)
        return "http://127.0.0.1:8100"

    session = WdaSession(client, settings, relaunch=relaunch)
    await session.open()
    # Locked: the transport still answers, but every real call refuses.
    fake.locked = True

    root = await session.source()

    assert root.type == "Application"
    assert fake.unlock_calls == 1
    assert relaunches == [], "the runner must not be restarted for a nap"
