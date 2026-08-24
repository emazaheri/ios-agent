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
