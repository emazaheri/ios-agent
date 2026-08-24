"""Small async subprocess helper shared by the device adapters."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import Any

from ios_mcp.errors import ToolchainMissing


@dataclass(slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self) -> Any:
        return json.loads(self.stdout)


async def run(
    *argv: str,
    timeout: float = 60.0,
    check: bool = False,
    stdin: bytes | None = None,
) -> CommandResult:
    """Run a command, capturing output. Never raises on non-zero unless ``check``."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolchainMissing(
            f"Command timed out after {timeout}s: {' '.join(argv)}",
            hint="The tool may be waiting on a device that is locked or disconnected.",
        ) from None

    result = CommandResult(
        argv=tuple(argv),
        returncode=proc.returncode or 0,
        stdout=out.decode(errors="replace").strip(),
        stderr=err.decode(errors="replace").strip(),
    )
    if check and not result.ok:
        raise ToolchainMissing(
            f"Command failed ({result.returncode}): {' '.join(argv)}",
            hint=result.stderr[:400] or None,
            details={"stdout": result.stdout[:400]},
        )
    return result


def which(binary: str) -> str | None:
    """Locate an executable on PATH."""
    return shutil.which(binary)


async def probe(*argv: str, timeout: float = 15.0) -> CommandResult | None:
    """Run a command if its binary exists, else return None."""
    if which(argv[0]) is None:
        return None
    try:
        return await run(*argv, timeout=timeout)
    except ToolchainMissing:
        return None
