"""The golden flows.

Each is a real task with a deterministic success assertion, expressed the way
an agent would express it: refs and plain descriptions, never coordinates.
They run against stock Apple apps so they need no fixture app.
"""

from __future__ import annotations

from harness import TokenMeter

from ios_mcp.errors import ActionRequiresApproval, IosAutomationError
from ios_mcp.session import IosSession


async def toggle_a_switch(session: IosSession, meter: TokenMeter) -> bool:
    """Flip a real switch, verify it moved, and put it back.

    Two levels deep, because the Accessibility root holds only navigation rows;
    the switches live inside its sub-panes.
    """
    await meter.act(session.tap(target="Accessibility"))
    await meter.act(session.tap(target="Display & Text Size"))
    digest = await meter.observe()

    switch = next((n for n in digest.nodes if n.role == "switch" and n.identifier), None)
    if switch is None:
        return False

    was = switch.value
    await meter.act(session.set_value("off" if was == "1" else "on", ref=switch.ref))

    after = await meter.observe()
    now = next((n for n in after.nodes if n.identifier == switch.identifier), None)
    if now is None or now.value == was:
        return False

    # Leave the device as we found it.
    await meter.act(session.set_value("on" if was == "1" else "off", ref=now.ref))
    return True


async def navigate_and_return(session: IosSession, meter: TokenMeter) -> bool:
    """Enter a sub-pane and come back, which is the commonest agent motion."""
    await session.open_url("App-prefs:root")
    start = await meter.observe()
    await meter.act(session.tap(target="General"))
    inner = await meter.observe()
    if inner.fingerprint == start.fingerprint:
        return False

    await meter.act(session.tap(target="Settings", role="button"))
    back = await meter.observe()
    return back.fingerprint != inner.fingerprint


async def scroll_to_find(session: IosSession, meter: TokenMeter) -> bool:
    """Find an item that starts below the fold."""
    await session.open_url("App-prefs:root")
    await meter.observe()
    result = await meter.act(session.scroll("down", until="Privacy", max_scrolls=15))
    return "found" in (result.note or "")


async def search_within_an_app(session: IosSession, meter: TokenMeter) -> bool:
    """Type into a search field and confirm the results narrow.

    Targets the field by ref: "Search" also names a Settings row, and the
    resolver correctly refuses to guess between them.
    """
    await session.open_url("App-prefs:root")
    before = await meter.observe()
    field = next((n for n in before.nodes if n.role == "searchfield"), None)
    if field is None:
        return False

    await meter.act(session.type_text("Airplane", ref=field.ref))
    after = await meter.observe()
    return after.fingerprint != before.fingerprint


async def read_content_out(session: IosSession, meter: TokenMeter) -> bool:
    """Extract text rather than acting, which is half of what agents are asked for."""
    await session.open_url("App-prefs:root")
    await meter.observe()
    await meter.act(session.tap(target="General"))
    text = await session.read_text()
    meter.charge({"text": text})
    # Assert on structure, not on a particular row: the General pane's contents
    # shift between iOS releases, and a flow that pins them tests Apple.
    return len(text) > 20 and len(text.splitlines()) >= 3


async def deep_link_shortcut(session: IosSession, meter: TokenMeter) -> bool:
    """Launch an app straight to a screen by URL.

    Only `App-prefs:root` is exercised: iOS 26 accepts sub-pane URLs such as
    `App-prefs:root=WIFI` and silently ignores them, so a flow asserting on one
    would be testing Apple's bug rather than this code.
    """
    await session.open_url("App-prefs:root")
    digest = await meter.observe()
    return any("General" in (n.label or "") for n in digest.nodes)


async def clipboard_roundtrip(session: IosSession, meter: TokenMeter) -> bool:
    """Set and read the clipboard, the cheap path for long or awkward strings."""
    await session.set_clipboard("ios-mcp eval marker")
    return await session.get_clipboard() == "ios-mcp eval marker"


async def wait_for_a_screen(session: IosSession, meter: TokenMeter) -> bool:
    """A semantic wait must beat a fixed sleep and must report honestly."""
    await session.open_url("App-prefs:root")
    found = await meter.act(session.wait_for("General", timeout_s=10))
    missing = await meter.act(session.wait_for("Not On This Screen", timeout_s=2))
    return found.ok and not missing.ok


async def recover_from_a_stale_ref(session: IosSession, meter: TokenMeter) -> bool:
    """A ref taken before the screen moves must still resolve afterwards."""
    await session.open_url("App-prefs:root")
    digest = await meter.observe()
    target = next((n for n in digest.nodes if n.actionable and n.identifier), None)
    if target is None:
        return False

    await meter.act(session.scroll("down"))
    try:
        result = await meter.act(session.tap(ref=target.ref))
    except IosAutomationError:
        return False
    return result.ok


async def refuse_an_unfindable_element(session: IosSession, meter: TokenMeter) -> bool:
    """A miss must name candidates rather than just failing."""
    await session.open_url("App-prefs:root")
    await meter.observe()
    try:
        await meter.act(session.tap(target="Definitely Not A Real Control"))
    except IosAutomationError as exc:
        return "closest" in exc.details
    return False


async def policy_blocks_a_destructive_action(session: IosSession, meter: TokenMeter) -> bool:
    """The gate must actually stop something, on a real screen.

    Runs with the gate forced on, since the other flows disable it to keep the
    suite non-interactive.
    """
    session.gate.settings.confirm_destructive = True
    try:
        await session.open_url("App-prefs:root")
        digest = await meter.observe()
        # Any real control will do; what is under test is that the gate fires
        # on a destructive-looking instruction before the device is touched.
        field = next((n for n in digest.nodes if n.role == "searchfield"), None)
        if field is None:
            return False
        try:
            await meter.act(session.type_text("delete everything", ref=field.ref))
        except ActionRequiresApproval:
            return True
        except IosAutomationError:
            return False
        return False
    finally:
        session.gate.settings.confirm_destructive = False


#: Name -> flow. Ordered cheapest first so a broken setup fails fast.
FLOWS = {
    "clipboard_roundtrip": clipboard_roundtrip,
    "deep_link_shortcut": deep_link_shortcut,
    "wait_for_a_screen": wait_for_a_screen,
    "read_content_out": read_content_out,
    "toggle_a_switch": toggle_a_switch,
    "navigate_and_return": navigate_and_return,
    "scroll_to_find": scroll_to_find,
    "search_within_an_app": search_within_an_app,
    "recover_from_a_stale_ref": recover_from_a_stale_ref,
    "refuse_an_unfindable_element": refuse_an_unfindable_element,
    "policy_blocks_a_destructive_action": policy_blocks_a_destructive_action,
}
