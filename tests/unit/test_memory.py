"""Memory: what survives a session, and what must not.

Memory is the pillar with the worst failure mode. A wrong route costs an
action; a note believed over the screen in front of the agent costs a wrong
action on someone's phone. So most of this module is about forgetting rather
than remembering.
"""

from __future__ import annotations

import json
from pathlib import Path

from ios_agent.backend import SessionBackend
from ios_agent.memory import Memory, default_path
from screens import DeviceModel, Injection, build_session

from ios_mcp.config import Settings

APP = "com.apple.Preferences"


def _fast_settings() -> Settings:
    cfg = Settings()
    cfg.stabilize.min_delay_s = 0.0
    cfg.stabilize.poll_interval_s = 0.001
    cfg.stabilize.max_wait_s = 0.2
    cfg.stabilize.stable_samples = 2
    cfg.policy.loop_detection_window = 50
    cfg.policy.confirm_destructive = False
    return cfg


def test_a_note_names_the_control_and_what_it_did() -> None:
    memory = Memory()
    memory.note_unresponsive(APP, "Airplane Mode", attempts=3)

    briefing = memory.briefing(APP)

    assert briefing is not None
    assert "airplane mode" in briefing.lower()
    assert "3 attempt" in briefing


def test_the_briefing_subordinates_itself_to_the_screen() -> None:
    """The wording is load-bearing, so it is asserted.

    A note that reads as fact competes with perception. It has to read as
    something previously observed, explicitly outranked by what is on screen,
    or memory becomes a way to be confidently wrong.
    """
    memory = Memory()
    memory.note_unresponsive(APP, "Airplane Mode", attempts=3)

    briefing = memory.briefing(APP) or ""

    assert "Trust the screen over these notes" in briefing
    assert "not what is true now" in briefing


def test_nothing_learned_means_nothing_said() -> None:
    assert Memory().briefing(APP) is None


def test_an_unknown_app_returns_everything_rather_than_nothing() -> None:
    """Notes are filed under the app the digest reported, which is only known
    once something has been observed, while a briefing is assembled before the
    loop starts. Returning nothing in that window would disable memory in a way
    that looks identical to having learned nothing."""
    memory = Memory()
    memory.note_unresponsive(APP, "Airplane Mode", attempts=2)

    assert memory.about(None) != []


async def test_a_repeated_no_op_is_learned_from_the_device() -> None:
    """Notes come from what the device did, never from what the model claimed."""
    memory = Memory()
    session, _, _ = build_session(
        DeviceModel(injections=frozenset({Injection.DEAD_SWITCH})), _fast_settings()
    )
    backend = SessionBackend(session, memory=memory)
    await backend.observe()

    for i in range(3):
        await backend.set_value("on", "Airplane Mode", idem_key=f"k{i}")

    assert [n.target for n in memory.about(APP)] == ["airplane mode"]


async def test_a_single_no_op_is_not_learned() -> None:
    """One no-op can be a slow transition, or a control already correct.

    Filing that as "does not work" would poison every later session with a note
    that was never true, which is worse than not remembering at all.
    """
    memory = Memory()
    session, _, _ = build_session(
        DeviceModel(injections=frozenset({Injection.DEAD_SWITCH})), _fast_settings()
    )
    backend = SessionBackend(session, memory=memory)
    await backend.observe()

    await backend.set_value("on", "Airplane Mode", idem_key="once")

    assert memory.about(APP) == []


async def test_the_device_contradicting_a_note_deletes_it() -> None:
    """Apps get updated and devices get fixed.

    A note that argues with the screen in front of it has stopped being
    evidence, so it goes immediately rather than ageing out.
    """
    memory = Memory()
    memory.note_unresponsive(APP, "Airplane Mode", attempts=3)

    session, _, _ = build_session(DeviceModel(), _fast_settings())
    backend = SessionBackend(session, memory=memory)
    await backend.observe()
    await backend.set_value("on", "Airplane Mode", idem_key="works")

    assert memory.about(APP) == []


async def test_a_note_never_stops_the_agent_trying() -> None:
    """Memory is a hint, not a gate.

    If a remembered failure blocked the action, a device that had since been
    fixed could never prove it, and the note would become self-confirming.
    """
    memory = Memory()
    memory.note_unresponsive(APP, "Airplane Mode", attempts=9)
    session, fake, _ = build_session(DeviceModel(), _fast_settings())
    backend = SessionBackend(session, memory=memory)
    await backend.observe()

    await backend.set_value("on", "Airplane Mode", idem_key="try")

    assert len(fake.taps()) == 1, "the remembered failure suppressed a real attempt"


def test_notes_survive_a_round_trip_through_disk(tmp_path: Path) -> None:
    path = default_path(tmp_path)
    written = Memory(path=path)
    written.note_unresponsive(APP, "Airplane Mode", attempts=4, fingerprint="abc123")
    written.save()

    reloaded = Memory(path=path).load()

    assert [n.target for n in reloaded.about(APP)] == ["airplane mode"]
    assert reloaded.about(APP)[0].attempts == 4
    assert reloaded.about(APP)[0].fingerprint == "abc123"


def test_loading_an_absent_file_is_not_an_error(tmp_path: Path) -> None:
    """A first run has no memory and must not be a special case."""
    assert Memory(path=tmp_path / "nothing.json").load().about(APP) == []


def test_the_file_is_readable_rather_than_a_blob(tmp_path: Path) -> None:
    """Someone has to be able to see what the agent believes, and delete it."""
    path = default_path(tmp_path)
    memory = Memory(path=path)
    memory.note_unresponsive(APP, "Airplane Mode", attempts=2)
    memory.save()

    payload = json.loads(path.read_text())

    assert payload["notes"][0]["app"] == APP
    assert "never changed" in payload["notes"][0]["detail"]
