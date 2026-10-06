"""Sprout: an onboarding app with bugs planted in it, for exploratory evals.

Every other task in this set has a fixed goal and is judged on the device. This
app exists for a different question: handed a vague goal ("get through
onboarding and tell me what is broken"), does an agent that reads the
accessibility tree find the bugs, and what does it cost? mobile-mcp #450 is the
only published number for that job: a Flutter signup with planted bugs,
$4.28 and 186 turns.

The bugs are chosen by class, so that a result can be read rather than only
counted. See ADR 0021 for the classes and the criteria, which were fixed before
any model ran.

- **Self-evident**: visible to anyone reading the screen with no spec. A dead
  Done button; "Profile saved" while Review says the name is not set; an upload
  that never finishes; a count that disagrees with the chips beside it; an
  email field that loses its first character, the bug #450 reported.
- **Needs a spec**: the name field takes 40 characters. That is a bug only if
  names are capped at 30, and the goal says so in one variant and not the
  other.
- **Visual**: the Welcome caption is drawn white on white. The tree carries the
  text as it would for any caption, so a reader of the tree cannot know.

Below the fold, as in #450, the terms switch on the account screen is off
screen until the page is scrolled. That is an obstacle, not a bug.

Screens are declared in code rather than as `Pane` rows because they are
forms, and their state (what was typed, what is selected, what is uploading) is
the point. Text fields are `FakeField`s, so typing goes through the same
read-back path a real field does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fake_wda import FakeField
from trees import node

BUNDLE = "com.example.sprout"
APP = "Sprout"
ENTRY = "sprout_welcome"
SCREENS = (
    "sprout_welcome",
    "sprout_account",
    "sprout_profile",
    "sprout_interests",
    "sprout_photo",
    "sprout_review",
)
INTERESTS = ("Running", "Reading", "Cooking", "Music")
#: The cap the spec states, in one variant of the goal.
NAME_CAP = 30

_X = 16.0
_W = 360.0
_H = 44.0
_BACK = (8.0, 44.0, 80.0, 52.0)
_BOTTOM = 780.0
#: Where the terms switch sits once the page has been scrolled to it.
_TERMS_Y = 640.0


@dataclass
class Element:
    """One thing on a Sprout screen, and what a tap on it does."""

    kind: str
    label: str | None
    y: float
    value: str | None = None
    name: str | None = None
    enabled: bool = True
    placeholder: str | None = None
    #: What tapping it does: a screen to go to, or an action name.
    to: str | None = None
    action: str | None = None
    h: float = _H

    def rect(self) -> tuple[float, float, float, float]:
        return (_X, self.y, _W, self.h)

    def hit(self, x: float, y: float) -> bool:
        left, top, width, height = self.rect()
        return left <= x <= left + width and top <= y <= top + height

    def tree(self) -> dict[str, Any]:
        out = node(
            self.kind,
            label=self.label,
            name=self.name,
            value=self.value,
            x=_X,
            y=self.y,
            w=_W,
            h=self.h,
            enabled=self.enabled,
        )
        if self.placeholder is not None:
            out["placeholderValue"] = self.placeholder
        return out


@dataclass
class Sprout:
    """The app's state, and the screens drawn from it."""

    #: The #450 bug: each burst of keys loses its first character.
    email: FakeField = field(default_factory=lambda: FakeField(placeholder="Email", drop_leading=1))
    password: FakeField = field(
        default_factory=lambda: FakeField(placeholder="Password", secure=True)
    )
    #: No cap. A bug only where the spec says 30.
    name: FakeField = field(default_factory=lambda: FakeField(placeholder="Full name"))
    focused: str | None = None
    #: Whether the account page has been scrolled far enough to show the terms.
    terms_visible: bool = False
    terms_accepted: bool = False
    #: The save that fails: the banner says saved, and nothing is kept.
    save_banner: bool = False
    saved_name: str | None = None
    selected: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(INTERESTS, False))
    uploading: bool = False
    #: Taps on the dead Done button, so a test can tell it was pressed.
    done_taps: int = 0

    # -- what is on screen -----------------------------------------------------

    def elements(self, screen: str) -> list[Element]:
        build = {
            "sprout_welcome": self._welcome,
            "sprout_account": self._account,
            "sprout_profile": self._profile,
            "sprout_interests": self._interests,
            "sprout_photo": self._photo,
            "sprout_review": self._review,
        }[screen]
        return build()

    def _welcome(self) -> list[Element]:
        return [
            Element("StaticText", "Welcome to Sprout", 120),
            Element("StaticText", "Grow one small habit a day.", 170),
            # The visual bug. Drawn white on a white background, so nobody can
            # read it; the tree reports it exactly as it would a legible one.
            Element("StaticText", "By continuing you accept our Terms.", 214),
            Element("Button", "Get Started", _BOTTOM, to="sprout_account"),
        ]

    def _account(self) -> list[Element]:
        ready = bool(self.email.text and self.password.text and self.terms_accepted)
        out = [
            Element(
                "TextField",
                "Email",
                140,
                value=self.email.text or None,
                placeholder="Email",
                action="focus:email",
            ),
            Element(
                "SecureTextField",
                "Password",
                200,
                value=chr(0x2022) * len(self.password.text) or None,
                placeholder="Password",
                action="focus:password",
            ),
            Element("StaticText", "Terms of Service", 270),
            Element(
                "StaticText",
                "Sprout stores your habits on our servers. You can delete your "
                "account at any time from Settings.",
                320,
                h=200,
            ),
        ]
        if self.terms_visible:
            out.append(
                Element(
                    "Switch",
                    "I agree to the Terms",
                    _TERMS_Y,
                    value="1" if self.terms_accepted else "0",
                    action="terms",
                )
            )
        out.append(Element("Button", "Continue", _BOTTOM, enabled=ready, to="sprout_profile"))
        return out

    def _profile(self) -> list[Element]:
        out = [
            Element(
                "TextField",
                "Full name",
                140,
                value=self.name.text or None,
                placeholder="Full name",
                action="focus:name",
            ),
            Element("Button", "Save", 200, action="save"),
        ]
        if self.save_banner:
            out.append(Element("StaticText", "Profile saved", 256))
        out.append(Element("Button", "Next", _BOTTOM, to="sprout_interests"))
        return out

    def _interests(self) -> list[Element]:
        chosen = sum(self.selected.values())
        # The count bug: one more than the chips beside it say.
        out = [Element("StaticText", f"{chosen + 1} selected", 110)]
        for index, interest in enumerate(INTERESTS):
            out.append(
                Element(
                    "Switch",
                    interest,
                    160 + 56 * index,
                    value="1" if self.selected[interest] else "0",
                    action=f"chip:{interest}",
                )
            )
        out.append(Element("Button", "Next", _BOTTOM, to="sprout_photo"))
        return out

    def _photo(self) -> list[Element]:
        out = [Element("Button", "Choose Photo", 200, action="upload")]
        if self.uploading:
            # The stuck upload: it says this on every read, forever.
            out.append(Element("ActivityIndicator", "Uploading photo", 256))
            out.append(Element("StaticText", "Uploading photo...", 300))
        out.append(Element("Button", "Skip", _BOTTOM, to="sprout_review"))
        return out

    def _review(self) -> list[Element]:
        chosen = [name for name, on in self.selected.items() if on]
        return [
            Element("StaticText", f"Email: {self.email.text or 'Not set'}", 140),
            # The failed save shows here: the name typed and "saved" is gone.
            Element("StaticText", f"Name: {self.saved_name or 'Not set'}", 190),
            Element("StaticText", f"Interests: {', '.join(chosen) or 'None'}", 240),
            # The dead button: it takes the tap and does nothing at all.
            Element("Button", "Done", _BOTTOM, action="done"),
        ]

    def tree(self, screen: str) -> dict[str, Any]:
        title = {
            "sprout_welcome": "Sprout",
            "sprout_account": "Create account",
            "sprout_profile": "Your profile",
            "sprout_interests": "Pick your interests",
            "sprout_photo": "Add a photo",
            "sprout_review": "Review",
        }[screen]
        nav = [node("StaticText", label=title, x=120, y=56, w=160, h=28)]
        if self.back_to(screen) is not None:
            bx, by, bw, bh = _BACK
            nav.insert(0, node("Button", label="Back", name="back_button", x=bx, y=by, w=bw, h=bh))
        return node(
            "Application",
            label=APP,
            name=APP,
            h=852,
            children=[
                node(
                    "Window",
                    h=852,
                    children=[
                        node("NavigationBar", name=title, y=44, h=52, children=nav),
                        *(element.tree() for element in self.elements(screen)),
                    ],
                )
            ],
        )

    @staticmethod
    def back_to(screen: str) -> str | None:
        index = SCREENS.index(screen)
        return SCREENS[index - 1] if index > 0 else None

    def focused_field(self) -> FakeField | None:
        return {"email": self.email, "password": self.password, "name": self.name}.get(
            self.focused or ""
        )

    # -- what the agent does ---------------------------------------------------

    def tap(self, screen: str, x: float, y: float) -> str | None:
        """Apply a tap. Returns the screen to go to, if the tap navigates."""
        back = self.back_to(screen)
        bx, by, bw, bh = _BACK
        if back is not None and bx <= x <= bx + bw and by <= y <= by + bh:
            return back
        for element in self.elements(screen):
            if not element.hit(x, y) or not element.enabled:
                continue
            if element.to is not None:
                return element.to
            if element.action is not None:
                self._do(element.action)
            return None
        return None

    def _do(self, action: str) -> None:
        verb, _, arg = action.partition(":")
        if verb == "focus":
            self.focused = arg
        elif verb == "terms":
            self.terms_accepted = not self.terms_accepted
        elif verb == "save":
            self.save_banner = True  # and the name is not kept: saved_name stays None
        elif verb == "chip":
            self.selected[arg] = not self.selected[arg]
        elif verb == "upload":
            self.uploading = True
        elif verb == "done":
            self.done_taps += 1

    def drag(self, screen: str, from_y: float, to_y: float) -> None:
        """Scrolling the account page down brings the terms switch on screen."""
        if screen == "sprout_account" and from_y > to_y:
            self.terms_visible = True

    def leave(self) -> None:
        self.focused = None
