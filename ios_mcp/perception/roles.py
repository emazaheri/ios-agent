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
    }
)

#: Roles an app can *draw* a control as instead of composing one out of a
#: control class. A heart icon rendered into an image, a card built out of a
#: tappable `Other`: neither is a Button, and neither is decoration either.
#:
#: This is a bet, stated as one. It says these three roles are the ones worth
#: a second look, and it would be falsified by an app that draws a control as
#: something else entirely. What keeps it narrow is the second half of the
#: rule in `_is_actionable`: only an *unlabelled* node with an accessibility
#: id qualifies, because a developer who named something nobody can read named
#: it so that something could find it.
DRAWN_CONTROL_ROLES: frozenset[str] = frozenset({"image", "other", "group"})

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
#: Containers whose own label is never content, so they collapse even when
#: labelled. An application's label is its name, which the digest already
#: carries separately, and a window's is noise.
ALWAYS_COLLAPSE: frozenset[str] = frozenset({"application", "window"})

#: Containers that are usually pure wrappers but sometimes carry the screen's
#: whole meaning on themselves. They survive on anything that names them, an
#: accessibility id included, which the rest of `CONTAINER_ROLES` does not get:
#: a `nav` or a `tabbar` named "Profile" is chrome whose label the digest
#: header already reports, while an `other` named `compose_button` is the
#: control itself. React Native produces the second shape whenever a developer
#: sets `testID` and no `accessibilityLabel`, which is reported to be the most
#: common third-party case there is.
#:
#: Apple's own apps never reveal this: Settings puts its text in `StaticText`
#: and `Cell`, so collapsing `other` unconditionally lost nothing. Third-party
#: apps routinely compose a card out of images and hang the readable version on
#: the wrapping `Other`. On a real Hinge profile the entire prompt arrives that
#: way, as one `Other` labelled "Prompt: ... Answer: ...", and collapsing it
#: leaves the agent looking at a screen of unlabelled boxes.
COLLAPSE_WHEN_EMPTY: frozenset[str] = frozenset({"other", "group"})

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


#: Names iOS attaches to purely decorative disclosure glyphs, whether as a
#: label or as an accessibility id. Their only job is to draw an arrow at the
#: end of a row; the row is the thing to tap, never the arrow. A stock Settings
#: screen emits fifteen or more of them.
#:
#: They arrive in two shapes and the second was invisible until unlabelled
#: nodes with ids started being kept. Some are *disabled buttons carrying the
#: glyph name as a label*. On iOS 26 the Settings root instead emits *enabled
#: images with no label at all* and `chevron.forward` as the id, one per row,
#: which is why matching has to look at the identifier and must not require the
#: node to be disabled.
DECORATIVE_LABELS: frozenset[str] = frozenset(
    {
        "chevron",
        "chevron.right",
        "chevron.left",
        "chevron.up",
        "chevron.down",
        "chevron.forward",
        "chevron.backward",
        "disclosure",
        "disclosureindicator",
    }
)

#: Which element wins when two nodes describe the same thing. The inner
#: control is more precise than the row that wraps it.
#:
#: A bet, stated as one: this ordering is Apple's vocabulary in Apple's order.
#: A role that is not on the list sorts last, which is the safe direction, but
#: it means a framework that reports its controls as something else entirely
#: loses every merge to whatever wraps it.
ROLE_PRECEDENCE: tuple[str, ...] = (
    "switch",
    "slider",
    "stepper",
    "textfield",
    "securefield",
    "searchfield",
    "textview",
    "button",
    "link",
    "menuitem",
    "cell",
    "text",
    "image",
)
