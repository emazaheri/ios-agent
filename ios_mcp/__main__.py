"""CLI entry point: `ios-mcp serve | doctor | devices | reset`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from ios_mcp.config import Settings, set_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ios-mcp", description="iOS automation MCP server")
    parser.add_argument("--config", type=Path, default=None, help="Path to a TOML config file")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the MCP server (default)")
    serve.add_argument("--transport", choices=["stdio", "http"], default=None)
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    doctor = sub.add_parser("doctor", help="Run preflight diagnostics")
    doctor.add_argument("--json", action="store_true")

    devices = sub.add_parser("devices", help="List simulators and attached devices")
    devices.add_argument("--json", action="store_true")

    reset = sub.add_parser(
        "reset",
        help="Find WebDriverAgent processes a crashed run left behind",
        description=(
            "A leftover runner holds the device and makes the next run time out. "
            "This lists every WebDriverAgent process on the machine and stops them "
            "with --yes. It cannot tell an orphan from a session in use right now, "
            "so it lists by default and --device narrows what it touches."
        ),
    )
    reset.add_argument("--json", action="store_true")
    reset.add_argument("--device", help="Only processes driving this UDID.")
    reset.add_argument(
        "-y", "--yes", action="store_true", help="Stop what was found, rather than listing it."
    )

    args = parser.parse_args(argv)

    settings = Settings.load(args.config)
    if args.log_level:
        settings.log_level = args.log_level
    set_settings(settings)
    logging.basicConfig(
        level=settings.log_level.upper(),
        stream=sys.stderr,  # stdout belongs to the MCP protocol
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    command = args.command or "serve"
    if command == "doctor":
        return _cmd_doctor(settings, json_out=args.json)
    if command == "devices":
        return _cmd_devices(settings, json_out=args.json)
    if command == "reset":
        return _cmd_reset(settings, json_out=args.json, udid=args.device, kill=args.yes)
    return _cmd_serve(settings, args)


def _cmd_doctor(settings: Settings, *, json_out: bool) -> int:
    from ios_mcp.devices.doctor import run_doctor

    report = asyncio.run(run_doctor(settings))
    if json_out:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return 0 if not any(c.status == "fail" for c in report.checks) else 1


def _cmd_reset(settings: Settings, *, json_out: bool, udid: str | None, kill: bool) -> int:
    """Exit 1 while a problem is still standing, which is what a script wants.

    Listing something is a problem, because the device is still held. Stopping
    it is not. That makes `ios-mcp reset && run-the-thing` mean "only if the
    machine is clear", and `ios-mcp reset -y && run-the-thing` mean "clear it,
    then go".
    """
    from ios_mcp.devices.processes import reset

    report = asyncio.run(reset(settings, udid=udid, kill=kill))
    if json_out:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return 1 if report.problems else 0


def _cmd_devices(settings: Settings, *, json_out: bool) -> int:
    from ios_mcp.devices.discovery import list_devices

    devices = asyncio.run(list_devices(settings))
    if json_out:
        print(json.dumps([d.to_dict() for d in devices], indent=2))
        return 0
    if not devices:
        print("No devices found. Run `ios-mcp doctor` to see what is missing.")
        return 1
    for d in devices:
        flag = "" if d.ready else "  (blocked: " + "; ".join(d.blockers) + ")"
        print(f"{d.kind:9} {d.name:28} iOS {d.os_version:8} {d.state:10} {d.udid}{flag}")
    return 0


def _cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    from ios_mcp.server.app import build_server

    transport = args.transport or settings.server.transport
    mcp = build_server(settings)
    if transport == "http":
        mcp.run(
            transport="http",
            host=args.host or settings.server.host,
            port=args.port or settings.server.port,
        )
    else:
        mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
