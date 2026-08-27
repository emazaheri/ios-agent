"""Synthetic accessibility trees that mimic real iOS structures."""

from __future__ import annotations

from typing import Any


def node(
    type_: str,
    *,
    label: str | None = None,
    name: str | None = None,
    value: Any = None,
    x: float = 0,
    y: float = 0,
    w: float = 393,
    h: float = 44,
    visible: bool = True,
    enabled: bool = True,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "label": label,
        "name": name if name is not None else label,
        "value": value,
        "rect": {"x": x, "y": y, "width": w, "height": h},
        "isVisible": "1" if visible else "0",
        "isEnabled": "1" if enabled else "0",
        "children": children or [],
    }


def list_screen(rows: int = 200, *, title: str = "Contacts") -> dict[str, Any]:
    """A long list, wrapped the way UIKit really wraps things.

    Each row is a Cell containing an Other wrapper containing a StaticText that
    echoes the cell's own label, plus a decorative unlabelled Image. That echo
    and those wrappers are the bulk of a real page source.
    """
    cells = []
    for i in range(rows):
        y = 100 + i * 44
        cells.append(
            node(
                "Cell",
                label=f"Contact {i:03d}",
                name=f"contact_cell_{i}",
                y=y,
                children=[
                    node(
                        "Other",
                        y=y,
                        children=[
                            node(
                                "StaticText", label=f"Contact {i:03d}", x=16, y=y + 10, w=180, h=24
                            ),
                            node("Image", x=350, y=y + 12, w=20, h=20),
                        ],
                    )
                ],
            )
        )
    return node(
        "Application",
        label="Contacts",
        name="Contacts",
        h=852,
        children=[
            node(
                "Window",
                h=852,
                children=[
                    node(
                        "NavigationBar",
                        name=title,
                        y=44,
                        h=52,
                        children=[
                            node("StaticText", label=title, x=16, y=56, w=120, h=28),
                            node("Button", label="Add", name="add", x=340, y=56, w=40, h=28),
                        ],
                    ),
                    node("Table", y=96, h=700, children=cells),
                    node(
                        "TabBar",
                        y=780,
                        h=72,
                        children=[
                            node("Button", label="Contacts", value="1", x=60, y=790, w=60, h=50),
                            node("Button", label="Recents", x=200, y=790, w=60, h=50),
                        ],
                    ),
                ],
            )
        ],
    )


def form_screen() -> dict[str, Any]:
    return node(
        "Application",
        label="Mail",
        h=852,
        children=[
            node(
                "NavigationBar",
                name="New Message",
                y=44,
                h=52,
                children=[
                    node("StaticText", label="New Message", x=120, y=56, w=150, h=28),
                    node("Button", label="Cancel", x=16, y=56, w=60, h=28),
                    node("Button", label="Send", name="send_button", x=330, y=56, w=50, h=28),
                ],
            ),
            node("TextField", label="To:", name="to_field", value="", y=110, h=44),
            node("TextField", label="Subject:", name="subject_field", value="", y=160, h=44),
            node("TextView", label="Body", name="body_field", value="", y=210, h=300),
            node("Button", label="Send", name="send_disabled", y=560, h=44, enabled=False),
            node("Other", y=0, w=0, h=0, visible=False),
        ],
    )


def settings_screen(airplane_on: bool = False) -> dict[str, Any]:
    return node(
        "Application",
        label="Settings",
        h=852,
        children=[
            node(
                "NavigationBar",
                name="Settings",
                y=44,
                h=52,
                children=[node("StaticText", label="Settings", x=16, y=56, w=100, h=28)],
            ),
            node(
                "Table",
                y=96,
                h=700,
                children=[
                    node(
                        "Cell",
                        label="Airplane Mode",
                        name="airplane_cell",
                        y=100,
                        children=[
                            node("StaticText", label="Airplane Mode", x=16, y=110, w=150, h=24),
                            node(
                                "Switch",
                                label="Airplane Mode",
                                name="airplane_switch",
                                value="1" if airplane_on else "0",
                                x=320,
                                y=108,
                                w=51,
                                h=31,
                            ),
                        ],
                    ),
                    node("Cell", label="Wi-Fi", name="wifi_cell", y=144),
                    node("Cell", label="Bluetooth", name="bt_cell", y=188),
                ],
            ),
        ],
    )


def third_party_card_screen() -> dict[str, Any]:
    """A card composed the way apps outside Apple's own tend to compose one.

    Modelled on a real screen, structurally rather than literally: the content
    is invented, the shapes are not. Two habits appear here that no Apple app
    exhibits, and both made real screens unreadable to the agent.

    First, a `StaticText` that splits a field across `label` and `value`: the
    label names the field and the value carries what it says. Reading only the
    label hands a reader the question and hides the answer.

    Second, an `Other` container carrying the entire card as its own label,
    with only images beneath it. Apple keeps its text in `StaticText` and
    `Cell`, so collapsing labelled `Other` nodes cost nothing on Settings and
    cost everything here.
    """
    return node(
        "Application",
        label="Cards",
        name="Cards",
        h=852,
        children=[
            node(
                "Window",
                h=852,
                children=[
                    node(
                        "NavigationBar",
                        name="Profile",
                        y=44,
                        h=52,
                        children=[node("StaticText", label="Profile", x=120, y=56, w=140, h=28)],
                    ),
                    # The label/value split.
                    node(
                        "StaticText",
                        label="Date prompt:",
                        name="Date prompt:",
                        value="Let's get together",
                        x=16,
                        y=120,
                        w=360,
                        h=40,
                    ),
                    # The whole card hung on a wrapping container.
                    node(
                        "Other",
                        label=(
                            "Prompt: Something my pet thinks about me. "
                            "Answer: She is obsessed with cuddling me"
                        ),
                        name="prompt_card_1",
                        x=16,
                        y=180,
                        w=360,
                        h=105,
                        children=[
                            node("Image", x=20, y=184, w=40, h=40),
                            node("Button", label="Like this answer", x=320, y=230, w=50, h=50),
                        ],
                    ),
                    # A container with no text of its own: still a wrapper.
                    node(
                        "Other",
                        x=16,
                        y=300,
                        w=360,
                        h=60,
                        children=[node("StaticText", label="Plain row", x=20, y=310, w=200, h=24)],
                    ),
                    # Values that are state or noise, which must stay hidden.
                    node("Other", label="Progress", value="45%", x=16, y=380, w=360, h=20),
                    node("Button", label="Badge", value="1", x=16, y=410, w=60, h=30),
                ],
            )
        ],
    )


def drawn_controls_screen() -> dict[str, Any]:
    """A screen whose controls are drawn rather than composed.

    The third habit outside Apple's own apps, after the label/value split and
    the labelled wrapper that `third_party_card_screen` models. Here the
    accessibility data is thinner still:

    * a hit target with **no label at all**, identified only by its
      accessibility id, the way a heart or a bookmark icon arrives when it is
      rendered instead of built out of a `UIButton`;
    * a decoration that is genuinely noise: no label, no id, nothing to act on.
      Telling these two apart is the whole job, and a rule that looks only at
      the role cannot do it;
    * an `Other` carrying only an id, which React Native produces whenever a
      developer sets `testID` and no `accessibilityLabel`. That is reported as
      the most common third-party case there is.
    """
    return node(
        "Application",
        label="Drawn",
        name="Drawn",
        h=852,
        children=[
            node(
                "Window",
                h=852,
                children=[
                    node(
                        "NavigationBar",
                        name="Feed",
                        y=44,
                        h=52,
                        children=[node("StaticText", label="Feed", x=16, y=56, w=140, h=28)],
                    ),
                    node(
                        "Other",
                        label="A post worth reacting to",
                        name="post_1",
                        x=16,
                        y=120,
                        w=360,
                        h=120,
                        children=[
                            # Noise: nothing names it and nothing acts on it.
                            node("Image", x=20, y=170, w=44, h=44),
                            # A control. Only the id says so.
                            node("Image", name="like_post_1", x=300, y=170, w=44, h=44),
                        ],
                    ),
                    # React Native's everyday shape: a testID and no label.
                    node(
                        "Other",
                        name="compose_button",
                        x=16,
                        y=260,
                        w=360,
                        h=48,
                    ),
                ],
            ),
        ],
    )


def custom_header_screen() -> dict[str, Any]:
    """A screen titled by a drawn header rather than a `NavigationBar`.

    `_screen_title` walks for the `nav` role, and the title is part of the
    fingerprint precisely so that navigating between two structurally similar
    screens is not read as an action that did nothing. An app that draws its
    own header gives the fingerprint nothing to tell those screens apart with.

    **The geometry here is measured, and an invented version of it was wrong.**
    The first draft of this fixture put the header at 7% down the screen, which
    made a 15% band look generous. A real third-party screen was captured on an
    iPhone 17 Pro Max and puts a row of filter controls *above* its title, so
    the title sits at y=197 of 956, 21% down, with the next line of text at 24%.
    The band tuned against the invented layout did nothing on the real one.

    So the proportions below are the real screen's and the content is invented,
    which is the same trade `third_party_card_screen` makes.
    """
    return node(
        "Application",
        label="Custom",
        name="Custom",
        w=440,
        h=956,
        children=[
            node(
                "Window",
                w=440,
                h=956,
                children=[
                    # The filter row that pushes the title down the screen.
                    node("Button", label="Filters", name="filter_all", x=20, y=82, w=76, h=32),
                    node("Button", label="Age", name="filter_age", x=104, y=82, w=76, h=32),
                    node("StaticText", label="Inbox", x=20, y=197, w=112, h=35),
                    node("StaticText", label="unread", x=20, y=234, w=90, h=20),
                    node("Cell", label="First message", name="msg_1", y=300),
                    node("Cell", label="Second message", name="msg_2", y=344),
                ],
            ),
        ],
    )


def webview_screen() -> dict[str, Any]:
    """Login, payment, and settings behind a `WKWebView`.

    The web content is a second tree that XCUITest does not descend into, so
    the accessibility tree stops at the boundary. Reaching it needs an explicit
    context switch this project has no concept of; what it can do is notice the
    boundary and say so, which beats returning a screen that merely looks
    empty.
    """
    return node(
        "Application",
        label="Hybrid",
        name="Hybrid",
        h=852,
        children=[
            node(
                "Window",
                h=852,
                children=[
                    node(
                        "NavigationBar",
                        name="Sign in",
                        y=44,
                        h=52,
                        children=[node("StaticText", label="Sign in", x=16, y=56, w=140, h=28)],
                    ),
                    node("WebView", name="login_web_view", y=96, h=700),
                ],
            ),
        ],
    )


def opaque_canvas_screen() -> dict[str, Any]:
    """One canvas covering the screen, with nothing beneath it.

    A Flutter app presents as a single `FlutterView`; a game or a custom-drawn
    view presents much the same way. There is no tree to capture and no amount
    of tuning produces one.
    """
    return node(
        "Application",
        label="Canvas",
        name="Canvas",
        h=852,
        children=[
            node(
                "Window",
                h=852,
                children=[node("Other", name="FlutterView", y=0, w=393, h=852)],
            ),
        ],
    )
