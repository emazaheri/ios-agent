"""Map XCUIElementType names to a small vocabulary an agent can reason about.

XCTest exposes roughly 80 element types. Most of the distinctions are
irrelevant to an agent deciding what to tap, and every extra token spent on
type names is a token not spent on labels, so they collapse to ~16 roles.
"""

from __future__ import annotations

#: XCUIElementType -> agent-facing role.
ROLE_MAP: dict[str, str] = {
    "Button": "button",
    "Key": "key",
    "NavigationBar": "nav",
    "TabBar": "tabbar",
    "ToolBar": "toolbar",
    "Link": "link",
    "StaticText": "text",
    "TextField": "textfield",
    "SecureTextField": "securefield",
    "SearchField": "searchfield",
    "TextView": "textview",
    "Switch": "switch",
    "Toggle": "switch",
    "Slider": "slider",
    "Stepper": "stepper",
    "PageIndicator": "pageindicator",
    "SegmentedControl": "segmented",
    "PickerWheel": "picker",
    "Picker": "picker",
    "DatePicker": "datepicker",
    "Cell": "cell",
    "Image": "image",
    "Icon": "image",
    "Alert": "alert",
    "Sheet": "sheet",
    "Menu": "menu",
    "MenuItem": "menuitem",
    "CheckBox": "checkbox",
    "RadioButton": "radio",
    "ProgressIndicator": "progress",
    "ActivityIndicator": "spinner",
    "Keyboard": "keyboard",
    "ScrollView": "scroll",
    "Table": "table",
    "CollectionView": "collection",
    "WebView": "webview",
    "Map": "map",
    "TabGroup": "tabbar",
    "StatusBar": "statusbar",
}

#: Roles a tap, type, or drag can meaningfully target.
INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "button",
        "link",
        "textfield",
        "securefield",
        "searchfield",
        "textview",
        "switch",
        "slider",
        "stepper",
        "segmented",
        "picker",
        "datepicker",
        "cell",
        "menuitem",
        "checkbox",
        "radio",
        "key",
        "tab",
    }
)

#: Roles that can be scrolled to reveal more content.
SCROLLABLE_ROLES: frozenset[str] = frozenset({"scroll", "table", "collection", "webview", "picker"})

#: Roles that only ever wrap other things and carry no meaning of their own.
CONTAINER_ROLES: frozenset[str] = frozenset(
    {
        "other",
        "window",
        "application",
        "group",
        "scroll",
        "table",
        "collection",
        "toolbar",
        "nav",
        "tabbar",
        "statusbar",
    }
)

#: Wrappers that are dropped even when they carry a label, because the label is
#: already reported in the digest header (the app name and the screen title).
ALWAYS_COLLAPSE: frozenset[str] = frozenset({"application", "window", "other", "group"})

#: Roles whose value is part of their state and must survive into the digest.
STATEFUL_ROLES: frozenset[str] = frozenset(
    {"switch", "slider", "stepper", "textfield", "securefield", "searchfield", "textview", "picker"}
)


def role_of(element_type: str) -> str:
    """Normalise an XCUIElementType name, tolerating the `XCUIElementType` prefix."""
    name = element_type.removeprefix("XCUIElementType")
    return ROLE_MAP.get(name, name.lower() or "other")


#: Roles whose value can be set directly rather than by tapping at them.
SETTABLE_ROLES: frozenset[str] = frozenset(
    {"switch", "slider", "stepper", "picker", "segmented", "checkbox", "radio"}
)
