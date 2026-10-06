"""Measure what settling after an action costs, by tree polling or by frames.

Runs the golden flows with the session's settle wrapped, so every tree read and
every screenshot inside it is timed. The numbers behind ADR 0019 came from here.

``confirm_s`` is the time after the tree read that first showed the screen's
final state: the poll sleep and the reads that only proved nothing moved. On
the tree signal it is the ceiling on what any other signal could save.

``--oracle`` checks each settle. After it returns, an untimed tree settle runs
and the two fingerprints are compared; a difference means the settle returned
a screen that was still changing. Run it on both signals, or the comparison
judges one arm by a standard the other never faced: the tree loop returned
early 3 times in 291 settles on a phone.

    uv run python scripts/settle_baseline.py --label tree --oracle
    uv run python scripts/settle_baseline.py --label frames --signal frames --oracle
    IOS_MCP_ALLOW_DEVICE=1 uv run python scripts/settle_baseline.py --device iPhone

On a physical device, only flows that leave nothing changed are run: the
switch flow and the clipboard flow are skipped, because hardware being present
is not consent to change it. Simulator timings moved by 70% between two arms
running identical code, so only device numbers are evidence of speed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from flows import FLOWS
from harness import TokenMeter

import ios_mcp.session as session_module
from ios_mcp.actions import stabilize
from ios_mcp.config import Settings, StabilizeSettings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.perception.digest import Digest
from ios_mcp.session import IosSession

SETTINGS_APP = "com.apple.Preferences"
#: Flows that change something on the device, even if they put it back. The
#: long list seeds 300 contacts, which on a phone would be someone's real
#: address book.
CHANGES_DEVICE = {"toggle_a_switch", "clipboard_roundtrip", "scroll_a_300_row_list"}


@dataclass
class SettleRecord:
    flow: str
    #: The session method that settled: `_act`, `scroll`, `launch_app` ...
    caller: str
    baseline: bool
    settled: bool
    #: "frames" when the screenshots decided, "tree" when polling did.
    via: str
    elapsed_s: float
    snapshot_s: list[float]
    frame_s: list[float]
    confirm_s: float
    #: Set only with `--oracle`: a later tree settle saw a different screen.
    premature: bool = False


@dataclass
class ActionRecord:
    flow: str
    action: str
    elapsed_ms: int


@dataclass
class Log:
    flow: str = ""
    oracle: bool = False
    settles: list[SettleRecord] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)


LOG = Log()
_real_settle = stabilize.settle


async def _timed_settle(
    observe: Callable[[], Awaitable[Digest]],
    settings: StabilizeSettings,
    *,
    baseline: str | None = None,
    frame: Callable[[], Awaitable[bytes]] | None = None,
) -> stabilize.SettleOutcome:
    # Two frames up: `IosSession._settle` is the caller, its caller the action.
    caller = sys._getframe(2).f_code.co_name
    marks: list[tuple[float, float, str]] = []  # (start, end, fingerprint)
    frame_s: list[float] = []

    async def timed_observe() -> Digest:
        start = time.monotonic()
        digest = await observe()
        marks.append((start, time.monotonic(), digest.fingerprint))
        return digest

    async def timed_frame() -> bytes:
        assert frame is not None
        start = time.monotonic()
        try:
            return await frame()
        finally:
            frame_s.append(round(time.monotonic() - start, 3))

    started = time.monotonic()
    outcome = await _real_settle(
        timed_observe,
        settings,
        baseline=baseline,
        frame=timed_frame if frame is not None else None,
    )
    ended = time.monotonic()

    final = outcome.digest.fingerprint
    # The end of the first read in the final run of identical fingerprints.
    first_final_end = marks[-1][1]
    for _, end, fp in reversed(marks):
        if fp != final:
            break
        first_final_end = end

    premature = False
    if LOG.oracle:
        tree_only = settings.model_copy(update={"min_delay_s": 0.0, "signal": "tree"})
        oracle = await _real_settle(observe, tree_only)
        premature = oracle.digest.fingerprint != final

    LOG.settles.append(
        SettleRecord(
            flow=LOG.flow,
            caller=caller,
            baseline=baseline is not None,
            settled=outcome.settled,
            via=outcome.via,
            elapsed_s=round(ended - started, 3),
            snapshot_s=[round(e - s, 3) for s, e, _ in marks],
            frame_s=frame_s,
            confirm_s=round(ended - first_final_end, 3),
            premature=premature,
        )
    )
    return outcome


class TimingMeter(TokenMeter):
    async def act(self, coro: Awaitable[Any]) -> Any:
        result = await super().act(coro)
        LOG.actions.append(ActionRecord(LOG.flow, result.action, result.elapsed_ms))
        return result


def _settings(args: argparse.Namespace, *, device: bool) -> Settings:
    cfg = Settings()
    cfg.wda.startup_timeout_s = 300.0
    cfg.stabilize.max_wait_s = 20.0 if device else 8.0
    cfg.stabilize.signal = args.signal
    if args.min_delay is not None:
        cfg.stabilize.min_delay_s = args.min_delay
    if args.poll is not None:
        cfg.stabilize.poll_interval_s = args.poll
    if args.quiet_s is not None:
        cfg.stabilize.quiet_s = args.quiet_s
    cfg.policy.confirm_destructive = False
    cfg.policy.loop_detection_window = 50
    return cfg


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3) if values else 0.0,
        "p90": round(_pct(values, 0.9), 3),
        "sum": round(sum(values), 3),
    }


def summarise(log: Log) -> dict[str, Any]:
    return {
        "settle_s": _summary([r.elapsed_s for r in log.settles]),
        "snapshot_s": _summary([s for r in log.settles for s in r.snapshot_s]),
        "frame_s": _summary([f for r in log.settles for f in r.frame_s]),
        "confirm_s": _summary([r.confirm_s for r in log.settles]),
        "action_s": _summary([a.elapsed_ms / 1000 for a in log.actions]),
        "via": {
            via: sum(1 for r in log.settles if r.via == via)
            for via in sorted({r.via for r in log.settles})
        },
        "unsettled": sum(1 for r in log.settles if not r.settled),
        "premature": sum(1 for r in log.settles if r.premature) if log.oracle else None,
        "by_caller": {
            caller: _summary([r.elapsed_s for r in log.settles if r.caller == caller])
            for caller in sorted({r.caller for r in log.settles})
        },
    }


async def main(args: argparse.Namespace) -> int:
    session_module.settle = _timed_settle  # type: ignore[assignment]
    LOG.oracle = args.oracle

    info = await DevicePool(Settings()).resolve(args.device)
    is_device = info.kind == "device"
    if is_device and os.environ.get("IOS_MCP_ALLOW_DEVICE") != "1":
        print("Refusing to drive a physical device without IOS_MCP_ALLOW_DEVICE=1")
        return 2

    cfg = _settings(args, device=is_device)
    pool = DevicePool(cfg)
    flows = {
        name: flow
        for name, flow in FLOWS.items()
        if not (is_device and name in CHANGES_DEVICE) and (not args.flows or name in args.flows)
    }
    failures: list[str] = []
    try:
        lease = await pool.acquire(info.udid)
        for run in range(args.runs):
            for name, flow in flows.items():
                session = IosSession(lease, cfg)
                await session.terminate_app(SETTINGS_APP)
                await session.launch_app(SETTINGS_APP, fresh=True)
                LOG.flow = name
                meter = TimingMeter(session)
                try:
                    ok = await flow(session, meter)
                except Exception as exc:  # measured, not fatal
                    ok = False
                    print(f"  {name}: {type(exc).__name__}: {exc}", flush=True)
                if not ok:
                    failures.append(f"run {run + 1} {name}")
                print(f"run {run + 1} {name}: {'ok' if ok else 'FAIL'}", flush=True)
    finally:
        await pool.release_all()

    report = {
        "label": args.label,
        "device": {"name": info.name, "kind": info.kind, "os": info.os_version},
        "stabilize": cfg.stabilize.model_dump(),
        "oracle": args.oracle,
        "runs": args.runs,
        "flows": list(flows),
        "failures": failures,
        "summary": summarise(LOG),
        "settles": [asdict(r) for r in LOG.settles],
        "actions": [asdict(a) for a in LOG.actions],
    }
    out = Path(args.out or f".artifacts/evals/settle-{info.kind}-{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="UDID, name or substring; default as the pool picks")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--label", default="default")
    parser.add_argument("--flows", nargs="*", help="only these flows")
    parser.add_argument("--signal", choices=["tree", "frames"], default="tree")
    parser.add_argument("--oracle", action="store_true", help="check each settle afterwards")
    parser.add_argument("--quiet-s", type=float, help="frames: no novel frame for this long")
    parser.add_argument("--min-delay", type=float)
    parser.add_argument("--poll", type=float)
    parser.add_argument("--out")
    sys.exit(asyncio.run(main(parser.parse_args())))
