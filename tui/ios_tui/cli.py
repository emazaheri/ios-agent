"""CLI entry point: `ios-agent devices | doctor`.

The command is `ios-agent` and the distribution is `ios-tui`. `ios-mcp` serves
the server; this drives a phone.

`devices` and `doctor` deliberately never touch Textual. They answer a question
and exit, and a full-screen app that paints and tears down a canvas to print
nine lines is worse at that than `print` is. Both also have to work on a
machine where the interesting half cannot run at all, which is exactly the
machine `doctor` exists for.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from ios_mcp.config import Settings, set_settings

#: Subcommands. Anything else in the first position is treated as a goal, so
#: `ios-agent "turn on bold text"` keeps working without the verb.
_COMMANDS = frozenset({"run", "devices", "doctor", "manual"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ios-agent",
        description="Drive an iPhone or simulator with a goal-directed agent.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to a TOML config file")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Give the agent a goal")
    run.add_argument("goal", nargs="?", help="What you want done, in plain English.")
    run.add_argument(
        "--device",
        help="UDID or part of a device name. Omit and the pool chooses, preferring simulators.",
    )
    run.add_argument(
        "--app",
        help="Bundle id to open first, e.g. com.apple.Preferences. Omit to use the current screen.",
    )
    run.add_argument(
        "--approve",
        action="store_true",
        help="Ask before destructive actions instead of refusing them outright.",
    )
    run.add_argument("--max-steps", type=int, help="Turns before the agent gives up. Default 24.")
    run.add_argument(
        "--no-tui",
        action="store_true",
        help="Print to stdout instead of taking over the terminal.",
    )
    run.add_argument(
        "--inline",
        action="store_true",
        help="Run in a short live region under the prompt instead of full screen.",
    )
    run.add_argument(
        "--no-stream",
        action="store_true",
        help="Wait for each model turn instead of showing it as it arrives.",
    )
    run.add_argument("-v", "--verbose", action="store_true", help="Show device startup lines.")

    manual = sub.add_parser("manual", help="Drive the device by hand, with no model in the loop")
    manual.add_argument("--device", help="UDID or part of a device name.")
    manual.add_argument("--app", help="Bundle id to open first.")
    manual.add_argument("--inline", action="store_true", help="Run under the prompt.")
    manual.add_argument("-v", "--verbose", action="store_true")

    devices = sub.add_parser("devices", help="List simulators and attached devices")
    devices.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="Diagnose anything that would stop a run")
    doctor.add_argument("--json", action="store_true")

    return parser


def _normalise(argv: list[str]) -> list[str]:
    """Let a bare goal stand in for `run <goal>`.

    A verb people have to remember in order to say the one thing the tool is
    for is a tax rather than a feature, so `ios-agent "turn on bold text"` and
    `ios-agent --device iPhone "turn wi-fi off"` both mean `run`.

    The test is whether a subcommand appears anywhere, not whether it comes
    first, because the flags that would precede one (`--device`, `--app`) are
    exactly what makes the first token unreliable.
    """
    if not argv or argv[0] in {"-h", "--help"}:
        return argv
    if any(token in _COMMANDS for token in argv):
        return argv
    return ["run", *argv]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(_normalise(list(sys.argv[1:] if argv is None else argv)))

    settings = Settings.load(args.config)
    if args.log_level:
        settings.log_level = args.log_level
    # One Settings for the whole process. `list_devices` and `run_doctor` fall
    # back to the module singleton when called without one, and a front end
    # holding different settings than the functions it calls is a bug that
    # only shows up as a device that is listed and then cannot be acquired.
    set_settings(settings)
    # WARNING rather than `settings.log_level`, which defaults to INFO for the
    # server's benefit. At INFO every WebDriverAgent request logs a line, and a
    # single action makes several: the run's own output ends up outnumbered by
    # its transport. The device progress worth reading does not come through
    # here at all, it comes through `progress.device_progress`, which sets its
    # own level and stops propagating. `--log-level` overrides this.
    logging.basicConfig(
        level=(args.log_level or "WARNING").upper(),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "doctor":
        return _cmd_doctor(settings, json_out=args.json)
    if args.command == "devices":
        return _cmd_devices(settings, json_out=args.json)
    if args.command == "manual":
        return _cmd_manual(settings, args)
    if args.command == "run":
        if not args.goal:
            print("Give me something to do, in quotes. See --help.", file=sys.stderr)
            return 2
        return _cmd_run(settings, args)
    build_parser().print_help()
    return 2


def _cmd_doctor(settings: Settings, *, json_out: bool) -> int:
    import json

    from ios_mcp.devices.doctor import run_doctor

    report = asyncio.run(run_doctor(settings))
    if json_out:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        # `render()` already leads with `summary`; printing it again is a
        # duplicate, not a footer.
        print(report.render())
    return 0 if not any(c.status == "fail" for c in report.checks) else 1


def _cmd_devices(settings: Settings, *, json_out: bool) -> int:
    import json

    from ios_mcp.devices.discovery import list_devices

    devices = asyncio.run(list_devices(settings))
    if json_out:
        print(json.dumps([d.to_dict() for d in devices], indent=2))
        return 0
    if not devices:
        print("No devices found. Run `ios-agent doctor` to see what is missing.")
        return 1
    for d in devices:
        blocked = "" if d.ready else "  (blocked: " + "; ".join(d.blockers) + ")"
        print(f"  {d.kind:9} {d.name:28} iOS {d.os_version:8} {d.state:10} {d.udid}{blocked}")
    return 0


async def _confirm(request: dict[str, object]) -> bool:
    """Ask on stdin. Anything but an explicit yes is a no.

    SAFETY.md: a client that cannot answer is treated as refusal, because an
    unanswerable question is not consent.
    """
    answer = await asyncio.to_thread(input, "  Allow this one action? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    # Imported here rather than at module scope so `devices` and `doctor` do
    # not pay for LangGraph. They are the two commands that have to work on a
    # machine where nothing else does.
    from ios_tui.runner import tuned

    cfg = tuned(settings)
    set_settings(cfg)
    if args.no_tui:
        return asyncio.run(_run_plain(cfg, args))
    return _run_app(cfg, args)


def _cmd_manual(settings: Settings, args: argparse.Namespace) -> int:
    """No provider is resolved at all, so this works with no API key."""
    from ios_tui.app import IosAgentApp
    from ios_tui.bus import EventSink
    from ios_tui.runner import GoalRunner, tuned

    cfg = tuned(settings)
    set_settings(cfg)

    def factory(sink: EventSink) -> GoalRunner:
        return GoalRunner(sink, cfg, device=args.device, bundle_id=args.app)

    app = IosAgentApp(factory, manual=True, inline=args.inline)
    return app.run(inline=args.inline, inline_no_clear=args.inline) or 0


def _run_app(settings: Settings, args: argparse.Namespace) -> int:
    from ios_agent import AgentSettings, export_provider_credentials

    from ios_tui.app import IosAgentApp
    from ios_tui.bus import EventSink
    from ios_tui.runner import GoalRunner
    from ios_tui.stream import streaming_chat_model

    export_provider_credentials()

    agent = AgentSettings()

    def factory(sink: EventSink) -> GoalRunner:
        # The app owns the queue and hands the sink in, so the runner never
        # learns that a terminal exists.
        #
        # `--no-stream` falls back to the agent's own `chat_model`, which is
        # the escape hatch for a provider whose streaming is broken or whose
        # chunks assemble badly: the run still works, it just arrives a turn at
        # a time.
        model = None if args.no_stream else streaming_chat_model(agent, sink)
        return GoalRunner(
            sink, settings, agent, device=args.device, bundle_id=args.app, model=model
        )

    app = IosAgentApp(
        factory,
        goal=args.goal,
        approve=args.approve,
        max_steps=args.max_steps,
        inline=args.inline,
    )
    # `inline_no_clear` keeps the last frame in scrollback instead of wiping
    # the region on exit, which is the difference between a live view and one
    # that leaves no trace of what it just did.
    return app.run(inline=args.inline, inline_no_clear=args.inline) or 0


async def _run_plain(settings: Settings, args: argparse.Namespace) -> int:
    from ios_agent import AgentSettings, export_provider_credentials

    from ios_tui.printer import Printer
    from ios_tui.runner import GoalRunner

    # The vendor SDK reads its key from the process environment, and
    # pydantic-settings only ever put `.env` into a settings object.
    export_provider_credentials()

    runner = GoalRunner(
        Printer(verbose=args.verbose),
        settings,
        AgentSettings(),
        device=args.device,
        bundle_id=args.app,
    )
    try:
        session = await runner.start()
        before = len(session.audit.entries)
        outcome = await runner.run(
            args.goal,
            approve=_confirm if args.approve else None,
            max_steps=args.max_steps,
        )

        # The audit trail, not the event stream. These rows are the device's
        # account of what happened rather than the agent's, which is the whole
        # reason to print them: `outcome.succeeded` is a claim.
        print("\n  what it did")
        for entry in session.audit.entries[before:]:
            target = entry.target or entry.args.get("url") or entry.args.get("bundle_id") or ""
            print(
                f"    {entry.seq:>2}. {entry.action:<12} "
                f"{str(target)[:40]:<40} changed={entry.screen_changed}"
            )

        print("\n  the screen it ended on")
        for line in runner.last_screen.splitlines()[:20]:
            print(f"    {line}")

        # The agent's own claim is not evidence. Read the device yourself.
        return 0 if outcome.succeeded else 1
    finally:
        await runner.close()


if __name__ == "__main__":
    sys.exit(main())
