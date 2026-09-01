"""Opening an app by the name a person would use for it."""

from __future__ import annotations

from ios_mcp.devices.base import AppInfo, best_app_match, closest_app_names


def _apps() -> list[AppInfo]:
    return [
        AppInfo(bundle_id="com.apple.Maps", name="Maps"),
        AppInfo(bundle_id="com.apple.Preferences", name="Settings"),
        AppInfo(bundle_id="com.apple.mobilesafari", name="Safari"),
        AppInfo(bundle_id="com.example.mapsettings", name="Map Settings"),
    ]


def test_an_exact_name_wins() -> None:
    assert best_app_match("Maps", _apps()) == "com.apple.Maps"
    assert best_app_match("  maps ", _apps()) == "com.apple.Maps"


def test_a_bundle_id_is_accepted_too() -> None:
    assert best_app_match("com.apple.mobilesafari", _apps()) == "com.apple.mobilesafari"


def test_a_partial_name_prefers_the_shortest_match() -> None:
    """'Map' must reach Maps rather than Map Settings, which merely contains it."""
    assert best_app_match("Map", _apps()) == "com.apple.Maps"


def test_a_typo_still_resolves() -> None:
    assert best_app_match("Safri", _apps()) == "com.apple.mobilesafari"


def test_nonsense_resolves_to_nothing() -> None:
    assert best_app_match("Spotify", _apps()) is None
    assert best_app_match("   ", _apps()) is None


def test_ranking_never_refuses_when_two_could_match() -> None:
    """An ambiguity error is a dead end for a caller whose only handle on an
    app is its name, so ranking always produces one winner."""
    apps = [
        AppInfo(bundle_id="com.a.maps", name="Maps"),
        AppInfo(bundle_id="com.b.maps", name="Maps Pro"),
    ]
    assert best_app_match("Maps", apps) == "com.a.maps"


def test_a_miss_offers_what_is_installed() -> None:
    closest = closest_app_names("Setings", _apps())
    assert any("Settings" in c for c in closest)
    assert all("(" in c for c in closest), "a candidate must carry its bundle id"
