#!/usr/bin/env python3
"""Give the agent a goal in plain English, from the command line.

    uv run python scripts/ask.py "turn on bold text"
    uv run python scripts/ask.py --app com.hinge.mobile.ios "open the first profile"
    uv run python scripts/ask.py --device "Ehsan's iPhone" --approve "turn wi-fi off"

The whole API is `run_goal(session, goal)`. Everything else here is getting a
session and printing what happened.

Two flags worth understanding before pointing this at a real phone.

`--device` picks the hardware. Without it the pool's default ranks simulators
above phones deliberately, because acting on someone's real device should take
intent rather than being what happened to be nearest.

`--approve` decides what happens when the agent wants to do something the
policy gate flags: send, pay, delete, erase. Without it the run is unattended
and every such action is refused, since a question nobody can answer is not
consent. With it the run stops and asks you in the terminal, one action at a
time; approving one never approves another.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

from ios_agent import (
    AgentSettings,
    SessionBackend,
    export_provider_credentials,
    run_goal,
)

from ios_mcp.config import Settings
from ios_mcp.devices.discovery import list_devices
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("goal", nargs="?", help="What you want done, in plain English.")
    p.add_argument(
        "--device",
        help="UDID or part of a device name. Omit and the pool chooses, preferring simulators.",
    )
    p.add_argument(
        "--app",
        help="Bundle id to open first, e.g. com.apple.Preferences. Omit to use the current screen.",
    )
    p.add_argument(
        "--approve",
        action="store_true",
        help="Ask before destructive actions instead of refusing them outright.",
    )
    p.add_argument(
        "--max-steps",
        type=int,
        help="Turns before the agent gives up. Default 24.",
    )
    p.add_argument("--list", action="store_true", help="List devices and exit.")
    return p.parse_args()


async def confirm(request: dict[str, Any]) -> bool:
    """Ask in the terminal. Anything but an explicit yes is a no."""
    print(f"\n  The agent wants to: {request['action']}")
    print(f"  Flagged because   : {request.get('reason') or 'a destructive-looking label'}")
    print(f"  On                : {request.get('signature')}")
    answer = input("  Allow this one action? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def tuned() -> Settings:
    """Timings a real device needs.

    A snapshot costs about 3.7s on a phone against well under a second on a
    simulator, so the settle ceiling has to exceed several snapshots or a
    device times out on every action.
    """
    cfg = Settings()
    cfg.stabilize.max_wait_s = 20.0
    cfg.wda.startup_timeout_s = 300.0
    return cfg


async def main() -> int:
    args = parse_args()
    export_provider_credentials()
    cfg = tuned()

    if args.list:
        # Listing is a lookup, not a run, so it does not need a goal.
        for d in await list_devices(cfg):
            ready = "ready" if d.ready else "not ready"
            print(f"  {d.kind:9} {d.name:28} iOS {d.os_version:7} {ready}")
        return 0

    if not args.goal:
        print("Give me something to do, in quotes. See --help.", file=sys.stderr)
        return 2

    pool = DevicePool(cfg)
    try:
        lease = await pool.acquire(args.device, bundle_id=args.app)
        session = IosSession(lease, cfg)
        print(
            f"  device : {lease.device.name} ({lease.device.kind}, iOS {lease.device.os_version})"
        )
        print(f"  model  : {AgentSettings().describe()}")
        print(f"  goal   : {args.goal}\n")

        backend = SessionBackend(session)
        started = time.monotonic()
        outcome = await run_goal(
            session,
            args.goal,
            backend=backend,
            approve=confirm if args.approve else None,
            max_steps=args.max_steps,
        )
        elapsed = time.monotonic() - started

        print("\n  what it did")
        for entry in session.audit.entries:
            target = entry.target or entry.args.get("url") or entry.args.get("bundle_id") or ""
            changed = entry.screen_changed
            print(
                f"    {entry.seq:>2}. {entry.action:<12} {str(target)[:40]:<40} changed={changed}"
            )

        print("\n  what it cost")
        print(
            f"    {backend.stats.actions} actions, {backend.stats.observations} observation(s), "
            f"{backend.stats.device_tokens} device tokens, {elapsed:.1f}s"
        )
        print(f"    model: {outcome.prompt_tokens} in / {outcome.completion_tokens} out")
        if outcome.approvals_asked:
            print(f"    stopped to ask you {outcome.approvals_asked} time(s)")

        print("\n  what it says")
        print(f"    succeeded: {outcome.succeeded}")
        print(f"    {outcome.summary or '(no summary)'}")
        if outcome.stopped_because:
            print(f"    stopped because: {outcome.stopped_because}")

        print("\n  the screen it ended on")
        for line in backend.last_screen.splitlines()[:20]:
            print(f"    {line}")

        # The agent's own claim is not evidence. Read the device yourself.
        return 0 if outcome.succeeded else 1
    finally:
        await pool.release_all()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
