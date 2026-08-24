"""Wire types for the WebDriverAgent HTTP API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Self

Button = Literal["home", "volumeUp", "volumeDown"]


@dataclass(slots=True, frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def distance_to(self, other: Rect) -> float:
        ax, ay = self.center
        bx, by = other.center
        return float(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5)

    @classmethod
    def from_wda(cls, data: dict[str, Any]) -> Self:
        return cls(
            x=float(data.get("x", 0)),
            y=float(data.get("y", 0)),
            width=float(data.get("width", 0)),
            height=float(data.get("height", 0)),
        )

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(slots=True, frozen=True)
class WdaStatus:
    ready: bool
    session_id: str | None
    ios_version: str | None
    device_name: str | None
    raw: dict[str, Any]

    @classmethod
    def from_wda(cls, payload: dict[str, Any]) -> Self:
        value = payload.get("value", payload)
        ios = value.get("ios") or {}
        return cls(
            ready=bool(value.get("ready", True)),
            session_id=payload.get("sessionId") or value.get("sessionId"),
            ios_version=ios.get("sdkVersion") or ios.get("ios"),
            device_name=(value.get("device") or ios.get("device")),
            raw=value,
        )


@dataclass(slots=True, frozen=True)
class AlertInfo:
    """A modal alert currently blocking the screen."""

    text: str
    buttons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "buttons": list(self.buttons)}


@dataclass(slots=True, frozen=True)
class SnapshotNode:
    """One node of the raw accessibility tree returned by ``GET /source``."""

    type: str
    label: str | None
    name: str | None
    value: str | None
    rect: Rect
    enabled: bool
    visible: bool
    accessible: bool
    placeholder: str | None
    children: tuple[SnapshotNode, ...]

    @property
    def identifier(self) -> str | None:
        """The accessibility identifier, when it differs from the visible label.

        WDA folds `identifier` into `name`, so `name` is the accessibility id
        when set and falls back to the label otherwise.
        """
        if self.name and self.name != self.label:
            return self.name
        return None

    @property
    def text(self) -> str | None:
        return self.label or self.value or self.name or self.placeholder

    def walk(self) -> list[SnapshotNode]:
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out

    @classmethod
    def from_wda(cls, data: dict[str, Any]) -> Self:
        return cls(
            type=str(data.get("type", "Other")),
            label=_clean(data.get("label")),
            name=_clean(data.get("name")),
            value=_clean(_stringify(data.get("value"))),
            rect=Rect.from_wda(data.get("rect", {})),
            enabled=_as_bool(data.get("isEnabled", data.get("enabled", True))),
            visible=_as_bool(data.get("isVisible", data.get("visible", True))),
            accessible=_as_bool(data.get("isAccessible", data.get("accessible", False))),
            placeholder=_clean(data.get("placeholderValue")),
            children=tuple(cls.from_wda(c) for c in data.get("children", []) or []),
        )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stringify(value: Any) -> Any:
    """WDA returns switch values as "0"/"1" and slider values as "40%"."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return value


def _as_bool(value: Any) -> bool:
    """WDA is inconsistent: booleans arrive as true, "true", 1, or "1"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False
