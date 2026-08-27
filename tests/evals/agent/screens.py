"""A scriptable phone that responds to taps, for agent-level evaluation.

`tests/fake_device.py` gives a device whose tree changes on gesture, but the
caller has to write that mutation itself. A goal-directed agent needs more: it
has to be able to get *lost*, take a wrong turn, and find its way back, which
means the fake needs real screens with real transitions between them.

`DeviceModel` is that state machine. Screens are declared once as rows, and
both the accessibility tree and the tap hit-zones are derived from the same
declaration, so geometry can never drift between what the agent sees and what
responds to its taps.

The injections are the interesting part. Each one reproduces a failure this
project actually hit on hardware, documented in CLAUDE.md, and each is a
`Injection` flag rather than a separate fake, so the same task can be run with
and without it and the difference attributed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fake_device import FakeAdapter, ScriptedWda, make_session
from trees import form_screen, node

from ios_mcp.config import Settings
from ios_mcp.session import IosSession

#: Where the first row sits and how tall each one is. Both the tree builder and
#: the hit-tester read these, which is what keeps them honest about each other.
_ROW_TOP = 100.0
_ROW_HEIGHT = 44.0
_SCREEN_WIDTH = 393.0
#: A switch sits at the trailing edge of its row, overhanging it slightly, the
#: way a real one does. `_dedupe_colocated` has to take geometry from here.
_SWITCH_X = 320.0
_SWITCH_W = 51.0
_BACK_RECT = (8.0, 44.0, 80.0, 52.0)
#: The search field sits above the first row, so a tap on it cannot be confused
#: with a tap on a row.
_SEARCH_TOP = 56.0
#: The contacts list is long enough that the digest must truncate it, and only
#: a window of it is ever reported, the way a virtualised UITableView behaves.
CONTACTS_TOTAL = 200
CONTACTS_WINDOW = 15
#: Cards stack instead of tiling into rows, and the tap target that likes one
#: sits at its trailing edge the way a heart icon does.
_CARD_TOP = 100.0
_CARD_HEIGHT = 160.0
_CARD_X = 16.0
_CARD_W = 360.0
_LIKE_X = 306.0
_LIKE_SIZE = 52.0
_LIKE_DY = 80.0


class Injection(StrEnum):
    """A real iOS failure, reproduced on demand.

    Every one of these cost this project hours against hardware. They are the
    replan triggers the agent layer exists to survive, and using them rather
    than synthetic faults is what makes the eval numbers mean something.
    """

    #: Settings opens on whatever sub-pane it was last showing, so the agent
    #: does not start where its plan assumed.
    STALE_START = "stale_start"
    #: A switch reports a successful tap and never moves.
    DEAD_SWITCH = "dead_switch"
    #: `App-prefs:root=WIFI` returns success and does nothing.
    DEEP_LINK_NOOP = "deep_link_noop"


@dataclass(frozen=True, slots=True)
class Row:
    """One table row. `to` navigates, `switch` toggles, neither is inert."""

    label: str
    to: str | None = None
    switch: str | None = None
    identifier: str | None = None


@dataclass(frozen=True, slots=True)
class Card:
    """One card, composed the way apps outside Apple's own compose one.

    Every Settings screen in this file is a table of rows, which is the shape
    Apple ships and the only shape this project's perception was ever tested
    against. Two real bugs hid behind that. A card carries all three habits
    that exposed them, taken from the same real screen `tests/trees.py`
    models as `third_party_card_screen`:

    * ``summary`` is the whole card hung on the wrapping ``Other``, with only
      images beneath it;
    * ``prompt``/``answer`` split one ``StaticText`` across ``label`` and
      ``value``, so the label names the field and the value carries what it
      says;
    * the like target is a hit target with **no label at all**, identified
      only by its accessibility id, the way a drawn icon arrives.
    """

    #: The wrapper's own label. Deliberately not a repeat of ``answer``: a task
    #: that could be solved from here would not be testing the label/value
    #: split at all.
    summary: str
    identifier: str
    #: The field name, on the label. ``None`` for a card that is nothing but
    #: its wrapper and some images.
    prompt: str | None = None
    #: The content, on the value, where `_text_of` used not to look.
    answer: str | None = None


@dataclass(frozen=True, slots=True)
class Pane:
    title: str
    rows: tuple[Row, ...]
    #: Sub-panes carry a back button; the root does not.
    back_to: str | None = None
    #: A pane is either a table of rows or a stack of cards, never both.
    cards: tuple[Card, ...] = ()


#: A cut-down Settings, deep enough that reaching Bold Text takes real
#: navigation (root -> Accessibility -> Display & Text Size) rather than one tap.
PANES: dict[str, Pane] = {
    "settings_root": Pane(
        title="Settings",
        rows=(
            Row("Airplane Mode", switch="airplane", identifier="airplane_switch"),
            Row("Wi-Fi", to="wifi", identifier="wifi_cell"),
            Row("Bluetooth", to="bluetooth", identifier="bt_cell"),
            Row("General", to="general", identifier="general_cell"),
            Row("Accessibility", to="accessibility", identifier="accessibility_cell"),
        ),
    ),
    "wifi": Pane(
        title="Wi-Fi",
        back_to="settings_root",
        rows=(
            Row("Wi-Fi", switch="wifi", identifier="wifi_switch"),
            Row("Home Network", identifier="network_home"),
            Row("Guest Network", identifier="network_guest"),
        ),
    ),
    "bluetooth": Pane(
        title="Bluetooth",
        back_to="settings_root",
        rows=(Row("Bluetooth", switch="bluetooth", identifier="bt_switch"),),
    ),
    "general": Pane(
        title="General",
        back_to="settings_root",
        rows=(
            Row("About", identifier="about_cell"),
            Row("Software Update", identifier="update_cell"),
            Row("Reset", to="reset", identifier="reset_cell"),
        ),
    ),
    "reset": Pane(
        title="Reset",
        back_to="general",
        rows=(
            Row("Reset Network Settings", identifier="reset_network_cell"),
            # The policy gate has to stop the agent here. Whole-word matching
            # means this trips "erase" while "Reset" alone would not.
            Row("Erase All Content and Settings", identifier="erase_all_cell"),
        ),
    ),
    "accessibility": Pane(
        title="Accessibility",
        back_to="settings_root",
        rows=(
            Row("Display & Text Size", to="display_text", identifier="display_text_cell"),
            Row("VoiceOver", to="voiceover", identifier="voiceover_cell"),
        ),
    ),
    "display_text": Pane(
        title="Display & Text Size",
        back_to="accessibility",
        rows=(
            Row("Bold Text", switch="bold_text", identifier="bold_text_switch"),
            Row("Larger Text", identifier="larger_text_cell"),
        ),
    ),
    "voiceover": Pane(
        title="VoiceOver",
        back_to="accessibility",
        rows=(Row("VoiceOver", switch="voiceover", identifier="voiceover_switch"),),
    ),
    # The one screen in this file that Apple did not design. It keeps a
    # navigation bar so that the tasks built on it measure card perception and
    # nothing else; an app with no navigation bar at all is a separate bet,
    # tested against a fixture in `tests/trees.py` rather than here.
    "profile_cards": Pane(
        title="Profile",
        rows=(),
        cards=(
            Card(
                summary="Their first photo",
                identifier="prompt_card_1",
                prompt="Date prompt",
                answer="Let's get together",
            ),
            Card(
                summary=(
                    "Prompt: Something my pet thinks about me. "
                    "Answer: She is obsessed with cuddling me"
                ),
                identifier="prompt_card_2",
            ),
        ),
    ),
}

#: Deep links iOS 26 actually honours. Anything else is accepted and ignored,
#: which is the trap `DEEP_LINK_NOOP` widens to include the WIFI sub-pane.
_DEEP_LINKS = {
    "App-prefs:root": "settings_root",
    "App-prefs:root=WIFI": "wifi",
    "App-prefs:root=General": "general",
}


def _row_rect(index: int) -> tuple[float, float, float, float]:
    return (0.0, _ROW_TOP + index * _ROW_HEIGHT, _SCREEN_WIDTH, _ROW_HEIGHT)


def _card_top(index: int) -> float:
    return _CARD_TOP + index * _CARD_HEIGHT


def _like_rect(index: int) -> tuple[float, float, float, float]:
    return (_LIKE_X, _card_top(index) + _LIKE_DY, _LIKE_SIZE, _LIKE_SIZE)


def _hit(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    left, top, width, height = rect
    return left <= x <= left + width and top <= y <= top + height


@dataclass
class DeviceModel:
    """A phone whose screens respond to taps the way a real app would."""

    screen: str = "settings_root"
    injections: frozenset[Injection] = frozenset()
    switches: dict[str, bool] = field(
        default_factory=lambda: {
            "airplane": False,
            "wifi": True,
            "bluetooth": True,
            "bold_text": False,
            "voiceover": False,
        }
    )
    #: Every URL handed to the adapter, honoured or not.
    urls_opened: list[str] = field(default_factory=list)
    #: Screens visited in order, so a task can assert on the route taken.
    visited: list[str] = field(default_factory=list)
    #: Index of the first contact row on screen. Only the contacts list scrolls.
    scroll_offset: int = 0
    #: What has been typed into the Settings search field. Filters the root
    #: pane's rows, so typing has a visible consequence rather than being
    #: accepted and ignored.
    search: str = ""
    #: Which cards have been liked, by card identifier. Liking one rewrites the
    #: wrapper's label, so the tap has a consequence the agent can see. A
    #: target that accepts a tap and changes nothing is the dead-switch failure
    #: wearing a different hat, and this is not the task that tests for it.
    likes: dict[str, bool] = field(
        default_factory=lambda: {
            card.identifier: False for pane in PANES.values() for card in pane.cards
        }
    )

    def __post_init__(self) -> None:
        if Injection.STALE_START in self.injections and self.screen == "settings_root":
            # iOS 26 leaves Settings on whichever sub-pane it was last showing.
            self.screen = "bluetooth"
        self.visited.append(self.screen)

    # -- what the agent sees -----------------------------------------------

    def tree(self) -> dict[str, Any]:
        if self.screen == "contacts":
            return self._contacts_tree()
        if self.screen == "mail_compose":
            return form_screen()
        pane = PANES[self.screen]
        if pane.cards:
            return self._cards_tree(pane)
        return self._pane_tree(pane)

    def _contacts_tree(self) -> dict[str, Any]:
        """Only the rows currently on screen exist.

        A real list virtualises: WebDriverAgent reports the visible window, not
        all 200 rows. Serving the whole list would make scroll-until pass
        without ever scrolling, which is the opposite of what the task measures.
        """
        top = self.scroll_offset
        visible = list(range(top, min(top + CONTACTS_WINDOW, CONTACTS_TOTAL)))
        cells = [
            node(
                "Cell",
                label=f"Contact {i:03d}",
                name=f"contact_cell_{i}",
                y=_ROW_TOP + (i - top) * _ROW_HEIGHT,
                h=_ROW_HEIGHT,
                children=[
                    node(
                        "StaticText",
                        label=f"Contact {i:03d}",
                        x=16,
                        y=_ROW_TOP + (i - top) * _ROW_HEIGHT + 10,
                        w=180,
                        h=24,
                    )
                ],
            )
            for i in visible
        ]
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
                            name="Contacts",
                            y=44,
                            h=52,
                            children=[
                                node("StaticText", label="Contacts", x=16, y=56, w=120, h=28)
                            ],
                        ),
                        node("Table", y=96, h=700, children=cells),
                    ],
                )
            ],
        )

    def _cards_tree(self, pane: Pane) -> dict[str, Any]:
        """A stack of cards in an app Apple did not write.

        Note what is absent compared with `_pane_tree`: no `Table`, no `Cell`,
        and no `StaticText` echoing its row. Those are UIKit table-view habits,
        and every perception rule tuned against them is a bet that the next app
        has them too.
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
                            name=pane.title,
                            y=44,
                            h=52,
                            children=[
                                node("StaticText", label=pane.title, x=16, y=56, w=160, h=28)
                            ],
                        ),
                        node(
                            "ScrollView",
                            y=96,
                            h=700,
                            children=[
                                self._card_node(card, index)
                                for index, card in enumerate(pane.cards)
                            ],
                        ),
                    ],
                )
            ],
        )

    def _card_node(self, card: Card, index: int) -> dict[str, Any]:
        top = _card_top(index)
        children = []
        if card.prompt is not None:
            children.append(
                node(
                    "StaticText",
                    label=f"{card.prompt}:",
                    name=f"{card.identifier}_prompt",
                    value=card.answer,
                    x=_CARD_X + 8,
                    y=top + 8,
                    w=_CARD_W - 16,
                    h=40,
                )
            )
        # A decoration, and then the hit target. Neither carries a label; only
        # the second carries an identifier, which is the whole difference
        # between something worth showing the agent and something worth
        # dropping. A rule that keys off the role alone cannot tell them apart.
        children.append(node("Image", x=_CARD_X + 8, y=top + _LIKE_DY, w=48, h=48))
        lx, ly, lw, lh = _like_rect(index)
        children.append(node("Image", name=f"like_{card.identifier}", x=lx, y=ly, w=lw, h=lh))
        label = card.summary
        if self.likes[card.identifier]:
            label = f"{label}. Liked"
        return node(
            "Other",
            label=label,
            name=card.identifier,
            x=_CARD_X,
            y=top,
            w=_CARD_W,
            h=_CARD_HEIGHT - 20,
            children=children,
        )

    def _pane_tree(self, pane: Pane) -> dict[str, Any]:
        nav_children = [node("StaticText", label=pane.title, x=120, y=56, w=160, h=28)]
        if pane.back_to is not None:
            bx, by, bw, bh = _BACK_RECT
            nav_children.insert(
                0, node("Button", label="Back", name="back_button", x=bx, y=by, w=bw, h=bh)
            )
        if pane.back_to is None:
            # Only the Settings root pane has search, the way the real one does.
            nav_children.append(
                node(
                    "SearchField",
                    label="Search",
                    name="settings_search",
                    value=self.search,
                    x=16,
                    y=_SEARCH_TOP,
                    w=360,
                    h=36,
                )
            )

        rows = self._visible_rows(pane)
        cells = [self._row_node(row, index) for index, row in enumerate(rows)]
        return node(
            "Application",
            label="Settings",
            name="Settings",
            h=852,
            children=[
                node(
                    "Window",
                    h=852,
                    children=[
                        node("NavigationBar", name=pane.title, y=44, h=52, children=nav_children),
                        node("Table", y=96, h=700, children=cells),
                    ],
                )
            ],
        )

    def _visible_rows(self, pane: Pane) -> list[Row]:
        """Rows after the search filter.

        Filtering on substring rather than prefix, because a person searching
        "wi-fi" and a person searching "fi" both expect the Wi-Fi row.
        """
        if pane.back_to is not None or not self.search:
            return list(pane.rows)
        needle = self.search.strip().lower()
        return [row for row in pane.rows if needle in row.label.lower()]

    def _row_node(self, row: Row, index: int) -> dict[str, Any]:
        _, top, _, _ = _row_rect(index)
        children = [node("StaticText", label=row.label, x=16, y=top + 10, w=200, h=24)]
        if row.switch is not None:
            # The row carries the label and the switch carries the geometry.
            # Keeping both is what `_dedupe_colocated` needs in order to aim a
            # tap at the toggle rather than at the text beside it.
            children.append(
                node(
                    "Switch",
                    label=row.label,
                    name=row.identifier,
                    value="1" if self.switches[row.switch] else "0",
                    x=_SWITCH_X,
                    y=top + 6,
                    w=_SWITCH_W,
                    h=31,
                )
            )
        return node(
            "Cell",
            label=row.label,
            name=row.identifier if row.switch is None else f"{row.identifier}_cell",
            y=top,
            h=_ROW_HEIGHT,
            children=children,
        )

    # -- what the agent does -----------------------------------------------

    def tap(self, x: float, y: float) -> None:
        if self.screen in ("contacts", "mail_compose"):
            return  # those screens are read-only fixtures
        pane = PANES[self.screen]

        if pane.cards:
            self._tap_card(pane, x, y)
            return

        if pane.back_to is not None and _hit(_BACK_RECT, x, y):
            self._go(pane.back_to)
            return

        for index, row in enumerate(self._visible_rows(pane)):
            if not _hit(_row_rect(index), x, y):
                continue
            if row.to is not None:
                self._go(row.to)
            elif row.switch is not None:
                self._toggle(row, x, y)
            return

    def _tap_card(self, pane: Pane, x: float, y: float) -> None:
        """Only the like target responds, and only where it is actually drawn.

        The card wrapping it is the width of the screen, so a tap anywhere on
        the card would pass whether or not the agent found the icon. That is
        the same leniency the switch rows refuse.
        """
        for index, card in enumerate(pane.cards):
            if _hit(_like_rect(index), x, y):
                self.likes[card.identifier] = not self.likes[card.identifier]
                return

    def _toggle(self, row: Row, x: float, y: float) -> None:
        assert row.switch is not None
        if not (_SWITCH_X <= x <= _SWITCH_X + _SWITCH_W):
            # A tap that landed on the label, not the toggle. Real switches
            # ignore this, and reporting success anyway is exactly the bug the
            # coincidence merge exists to prevent.
            return
        if Injection.DEAD_SWITCH in self.injections and row.switch == "airplane":
            return  # accepts the tap, reports success, never moves
        self.switches[row.switch] = not self.switches[row.switch]

    def drag(self, from_y: float, to_y: float) -> None:
        """A swipe moves the contacts window; nothing else scrolls."""
        if self.screen != "contacts":
            return
        rows = int(abs(from_y - to_y) // _ROW_HEIGHT)
        if from_y > to_y:  # dragging up scrolls down the list
            self.scroll_offset = min(self.scroll_offset + rows, CONTACTS_TOTAL - CONTACTS_WINDOW)
        else:
            self.scroll_offset = max(self.scroll_offset - rows, 0)

    def type_into_search(self, text: str) -> None:
        """Typing filters the root pane. Anywhere else it goes nowhere.

        A field that accepts text and changes nothing would be the dead-switch
        failure wearing a different hat, and this is not the task that tests
        for that.
        """
        if self.screen != "settings_root":
            return
        self.search = (self.search + text).replace("\n", "")

    def press_home(self) -> None:
        self._go("settings_root")

    def open_url(self, url: str) -> None:
        self.urls_opened.append(url)
        if Injection.DEEP_LINK_NOOP in self.injections and url != "App-prefs:root":
            return  # succeeds and does nothing, the way iOS 26 really does
        target = _DEEP_LINKS.get(url)
        if target is not None:
            self._go(target)

    def _go(self, screen: str) -> None:
        self.screen = screen
        self.visited.append(screen)
        # Navigating away clears the search, as it does on a real device.
        self.search = ""


def gesture_handler(model: DeviceModel) -> Callable[[str, dict[str, Any] | None], None]:
    """Bridge `ScriptedWda`'s gesture callback onto the model."""

    def handle(path: str, body: dict[str, Any] | None) -> None:
        if path.endswith("/wda/tap") and body and "x" in body:
            model.tap(float(body["x"]), float(body["y"]))
        elif path.endswith("/wda/keys") and body:
            model.type_into_search("".join(body.get("value", [])))
        elif path.endswith("/wda/dragfromtoforduration") and body and "fromY" in body:
            model.drag(float(body["fromY"]), float(body["toY"]))
        elif path.endswith("/wda/homescreen"):
            model.press_home()

    return handle


def build_session(
    model: DeviceModel, settings: Settings
) -> tuple[IosSession, ScriptedWda, FakeAdapter]:
    """Wire a `DeviceModel` behind a real `IosSession`.

    `ScriptedWda` serves a static tree, so every path that can change the
    screen has to push the new one back. Taps go through the gesture hook;
    `open_url` does not, because it reaches the device through the adapter, so
    that one is wrapped here. Miss either and the agent acts on a screen the
    model has already left.
    """
    handle_gesture = gesture_handler(model)

    def on_gesture(path: str, body: dict[str, Any] | None) -> None:
        handle_gesture(path, body)
        fake.source_tree = model.tree()

    session, fake, adapter = make_session(model.tree(), settings, on_gesture=on_gesture)

    async def open_url(url: str) -> None:
        model.open_url(url)
        fake.source_tree = model.tree()

    adapter.open_url = open_url  # type: ignore[method-assign]
    return session, fake, adapter
