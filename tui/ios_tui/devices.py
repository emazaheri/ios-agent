"""Choosing which phone to drive.

Two things this screen is careful about, both inherited from the pool rather
than invented here.

**A physical device is never the pre-selected option.** The pool ranks a
simulator above a connected phone deliberately: acting on someone's real device
should be a choice, not what happened to be nearest. The cursor starts wherever
`DevicePool.resolve(None)` points, which is the same call an unattended run
makes, so landing on a phone always takes a keystroke and this screen cannot
drift away from the ranking it is meant to mirror.

**A device that cannot be driven is still shown.** `DeviceInfo.blockers` is
the diagnostic, and hiding an unusable device hides the reason it is unusable.
They are listed, dimmed, unselectable, with the blocker spelled out.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from ios_mcp.config import Settings
from ios_mcp.devices.base import DeviceInfo

#: `DeviceKind` reads "device" for a physical phone, which is not a word that
#: distinguishes it from anything on a screen listing devices.
_KIND_LABEL = {"simulator": "simulator", "device": "iPhone"}


class DevicePicker(ModalScreen[str | None]):
    """Lists what is reachable and returns the chosen UDID."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.devices: list[DeviceInfo] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label("Which device?", id="picker-title")
            yield Static("looking for devices\u2026", id="picker-status")
            # Hidden here rather than in `on_mount`, which runs before the
            # composed children exist.
            listing = OptionList(id="picker-list")
            listing.display = False
            yield listing
            yield Static("enter to choose \u00b7 esc to cancel", id="picker-keys")

    def on_mount(self) -> None:
        self.load()

    @work
    async def load(self) -> None:
        from ios_mcp.devices.discovery import list_devices
        from ios_mcp.devices.pool import DevicePool

        try:
            self.devices = await list_devices(self.settings)
            # Asked rather than reimplemented. `resolve(None)` is the same call
            # an unattended run makes, so the cursor cannot drift away from the
            # pool's ranking without this screen noticing.
            preferred = await DevicePool(self.settings).resolve(None)
        except Exception as exc:
            self.query_one("#picker-status", Static).update(f"could not look: {exc}")
            return

        status = self.query_one("#picker-status", Static)
        if not self.devices:
            status.update("Nothing reachable. `ios-agent doctor` says what is missing.")
            return

        ready = [d for d in self.devices if d.ready]
        options = [Option(_row(d), id=d.udid, disabled=not d.ready) for d in self.devices]
        listing = self.query_one("#picker-list", OptionList)
        listing.add_options(options)
        listing.display = True

        if ready:
            # The cursor lands where an unattended run would have gone, so a
            # phone is never pre-selected.
            listing.highlighted = next(
                (i for i, d in enumerate(self.devices) if d.udid == preferred.udid), 0
            )
            status.update(f"{len(ready)} ready of {len(self.devices)}")
        else:
            status.update("None of these can be driven yet. The blockers are below.")
        listing.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id) if event.option.id else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _row(device: DeviceInfo) -> Text:
    """One device, as a line.

    The kind comes first because it is the consequential field: everything else
    on the row describes a device, and this is the one that says whether it is
    someone's actual phone. It is also the only coloured thing here, for the
    same reason. A real device sitting in a list of simulators, distinguished
    only by a word in a column, is one arrow key away from being automated by
    accident.
    """
    physical = device.kind == "device"
    line = Text()
    line.append(
        f"{_KIND_LABEL.get(device.kind, device.kind):<10}",
        style="bold yellow" if physical else "dim",
    )
    line.append(f"{device.name:<28}")
    line.append(f" iOS {device.os_version:<8}", style="dim")
    line.append(device.state, style="dim")
    if not device.ready and device.blockers:
        line.append("\n" + " " * 10 + "; ".join(device.blockers), style="red")
    return line
