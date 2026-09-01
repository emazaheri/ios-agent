"""From a fresh clone to a moving simulator, in one command.

Every piece of this already existed and none of it was strung together: the
doctor knows what is missing and how to fix each thing, `create_simulator`
makes the cheap repair, and `prepare_wda.sh` builds the runner. What a new
reader had instead was a list of commands to run in the right order, deciding
for themselves which failure meant what.

Three rules shape it.

**Ask before changing the machine.** Creating a simulator and building
WebDriverAgent are both things someone might reasonably not want, and the fact
that they are cheap is not the same as their being ours to decide. `--yes`
exists for scripts, and says so.

**Never start a download this size.** A missing iOS runtime is about 8 GB. It
is named, with the command that fetches it, and not run: a download that size
belongs to a command someone starts deliberately, where they can watch it and
stop it. That distinction is already made in `doctor`, and it is kept here.

**End somewhere that needs no API key.** The last step is `manual` mode, which
drives the same nine verbs as the agent with no model in the loop. Someone can
see a real device respond before deciding whether to spend anything on it.

No Textual. This runs before there is any reason to believe a terminal UI will
work, and `tests/unit/test_layering.py` keeps this half of the package
importable without it.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from ios_mcp.config import Settings
from ios_mcp.devices.discovery import create_simulator
from ios_mcp.devices.doctor import DoctorReport, run_doctor

#: Written next to the package rather than found by searching upward: a clone
#: has it here, and an installed wheel does not have it at all, which is a
#: difference worth reporting rather than papering over.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREPARE_WDA = _REPO_ROOT / "scripts" / "prepare_wda.sh"


class Quickstart:
    """The steps, in order, each one reporting what it did."""

    def __init__(self, settings: Settings, *, assume_yes: bool = False) -> None:
        self.settings = settings
        self.assume_yes = assume_yes

    # -- output ------------------------------------------------------------

    def say(self, text: str = "") -> None:
        print(text)

    def step(self, text: str) -> None:
        print(f"\n==> {text}")

    def fail(self, text: str, *remedies: str) -> None:
        print(f"\n{text}", file=sys.stderr)
        for line in remedies:
            print(f"  {line}", file=sys.stderr)

    def ask(self, question: str) -> bool:
        """Consent, or the absence of a person to give it.

        A non-interactive stdin answers no rather than yes. A script that
        wanted this to proceed can say so with `--yes`; one that merely has no
        terminal has not agreed to anything.
        """
        if self.assume_yes:
            print(f"{question} yes (--yes)")
            return True
        if not sys.stdin.isatty():
            print(f"{question} no (nothing is attached to answer; --yes to proceed)")
            return False
        try:
            return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            print()
            return False

    # -- the steps ---------------------------------------------------------

    async def run(self) -> int:
        self.step("Checking the toolchain")
        report = await run_doctor(self.settings)
        self.say(report.summary)

        if not self._toolchain_is_usable(report):
            return 1
        if not await self._ensure_a_simulator(report):
            return 1
        if not self._ensure_webdriveragent(report):
            return 1

        self.step("Ready")
        self.say("Everything a simulator needs is in place. Starting manual mode,")
        self.say("which drives the device by hand and needs no API key.")
        self.say("Type `help` for the verbs, `ctrl+q` to quit.")
        self.say()
        return 0

    def _toolchain_is_usable(self, report: DoctorReport) -> bool:
        """Xcode and its command line tools, which nothing here can install."""
        for name in ("xcode", "simctl"):
            check = next((c for c in report.checks if c.name == name), None)
            if check is not None and check.status == "fail":
                self.fail(
                    f"{name}: {check.detail}",
                    check.remedy or "Install Xcode from the App Store.",
                )
                return False
        return True

    async def _ensure_a_simulator(self, report: DoctorReport) -> bool:
        """A runtime is a download; a device made from one is a second.

        Telling these apart is the whole point. Only one of them is reasonable
        to offer to do for someone.
        """
        check = next((c for c in report.checks if c.name == "simulators"), None)
        if check is None or check.status == "ok":
            return True

        if not check.data.get("can_create"):
            self.fail(
                f"No iOS runtime: {check.detail}",
                check.remedy or "xcodebuild -downloadPlatform iOS",
                "That is about 8 GB, so it is yours to start. Re-run this afterwards.",
            )
            return False

        self.step("Creating a simulator")
        self.say(check.detail)
        if not self.ask("A runtime is installed, so this takes about a second. Create one?"):
            self.say("Not created. `xcrun simctl create` does it by hand.")
            return False
        try:
            device = await create_simulator(runtime=check.data.get("runtime"))
        except Exception as exc:
            self.fail(f"Could not create a simulator: {exc}")
            return False
        self.say(f"Created {device.name} on iOS {device.os_version}.")
        return True

    def _ensure_webdriveragent(self, report: DoctorReport) -> bool:
        """The runner the simulator is driven through.

        Measured at about 19 seconds from nothing, clone included, which is why
        this offers to run it rather than shipping a prebuilt copy: an artifact
        tied to an SDK version, with its own checksum and licence notice, is a
        great deal of machinery to save nineteen seconds.
        """
        check = next((c for c in report.checks if c.name == "wda-bundle"), None)
        if check is not None and "xctestrun" in check.data:
            return True

        self.step("Building WebDriverAgent")
        if not _PREPARE_WDA.exists():
            self.fail(
                "No prepare_wda.sh beside this package.",
                "Quickstart builds from a clone of the repository:",
                "  git clone https://github.com/emazaheri/ios-agent",
                "  cd ios-agent && uv run ios-agent quickstart",
            )
            return False

        self.say("About 20 seconds, including the clone, and only ever done once.")
        if not self.ask("Build it now?"):
            self.say(f"Not built. {_PREPARE_WDA} simulator does it by hand.")
            return False

        result = subprocess.run([str(_PREPARE_WDA), "simulator"], cwd=_REPO_ROOT, check=False)
        if result.returncode != 0:
            self.fail(
                "The build failed.",
                f"Run it directly to see why: {_PREPARE_WDA} simulator",
            )
            return False
        self.say("Built.")
        return True


def quickstart(settings: Settings, *, assume_yes: bool = False) -> int:
    """Run the steps. Returns a process exit code."""
    return asyncio.run(Quickstart(settings, assume_yes=assume_yes).run())
