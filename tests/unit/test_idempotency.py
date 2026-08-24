"""Act-once semantics: the property LangGraph's interrupt/resume depends on."""

from __future__ import annotations

import time

from fake_device import make_session
from trees import form_screen, settings_screen

from ios_mcp.actions.idempotency import IdempotencyCache


def test_a_repeated_key_returns_the_stored_value() -> None:
    cache = IdempotencyCache()
    cache.put("k", "result")
    assert cache.get("k") == "result"
    assert cache.hits == 1


def test_no_key_means_no_caching() -> None:
    cache = IdempotencyCache()
    cache.put(None, "result")
    assert cache.get(None) is None
    assert len(cache) == 0


def test_entries_expire() -> None:
    cache = IdempotencyCache(ttl_s=0.0)
    cache.put("k", "result")
    time.sleep(0.001)
    assert cache.get("k") is None


def test_the_cache_is_bounded() -> None:
    cache = IdempotencyCache(max_entries=3)
    for i in range(10):
        cache.put(f"k{i}", i)
    assert len(cache) <= 3


async def test_a_retried_action_does_not_touch_the_device_twice() -> None:
    """A resumed LangGraph node must not send the message a second time."""
    session, fake, _ = make_session(form_screen())
    await session.observe()

    first = await session.tap(target="Send", idem_key="send-once")
    taps_after_first = len(fake.taps())
    second = await session.tap(target="Send", idem_key="send-once")

    assert len(fake.taps()) == taps_after_first, "the device must not be touched again"
    assert second.from_cache is True
    assert first.from_cache is False
    assert "was not touched" in second.to_dict()["note"]


async def test_different_keys_act_independently() -> None:
    session, fake, _ = make_session(settings_screen())
    await session.observe()
    await session.tap(target="Wi-Fi", idem_key="a")
    await session.tap(target="Wi-Fi", idem_key="b")
    assert len(fake.taps()) == 2


async def test_actions_without_a_key_always_execute() -> None:
    session, fake, _ = make_session(settings_screen())
    await session.observe()
    await session.tap(target="Wi-Fi")
    await session.tap(target="Wi-Fi")
    assert len(fake.taps()) == 2


async def test_caching_covers_scrolls_and_swipes_too() -> None:
    session, fake, _ = make_session(settings_screen())
    await session.observe()
    await session.scroll("down", idem_key="scroll-once")
    drags = len([p for p, _ in fake.gestures if p.endswith("dragfromtoforduration")])
    result = await session.scroll("down", idem_key="scroll-once")
    assert result.from_cache is True
    assert len([p for p, _ in fake.gestures if p.endswith("dragfromtoforduration")]) == drags
