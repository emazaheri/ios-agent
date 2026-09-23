#!/usr/bin/env python3
"""Move the version everywhere it is written, in one command.

The version is stated in six places: three workspace `pyproject.toml` files,
`server.json` twice, and `uv.lock`, which records every workspace member with
its version. That last one is the whole problem. It does not look like part of
a version bump, it is regenerated rather than edited, and forgetting it ships a
tree that fails `uv sync --locked`.

That has already happened once. 0.3.0 moved the four obvious files and left the
lock behind, and nothing said so, because both workflows ran a plain `uv sync`
which rewrites the lock in place and carries on. Every job went green against a
file it had just corrected. `uv sync --locked` in CI is what closed that, and
`tests/unit/test_version.py` is what names the file when it happens.

This removes the class instead of detecting it. `uv version --package` knows
how to set a member's version and re-lock, so the only thing left to hand is
`server.json`, which uv has no reason to know about.

    python scripts/release.py 0.4.1
    python scripts/release.py --bump minor
    python scripts/release.py --bump patch --dry-run

It stops before committing and before tagging, and prints both commands. A tag
push publishes to PyPI, which can never be undone or re-uploaded, so the last
word stays with a person.

Stdlib only, and it shells out to `uv` rather than importing anything, so it
runs from a bare checkout.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Distribution name -> the pyproject that carries its version. Every member
#: moves together: independent numbers were tried and drifted immediately.
MEMBERS = {
    "ios-mcp": Path("pyproject.toml"),
    "ios-agent": Path("agent/pyproject.toml"),
    "ios-tui": Path("tui/pyproject.toml"),
}

#: The gates the release workflow runs before it publishes. Run here too, so a
#: bump that cannot ship fails on a laptop in a minute rather than on a runner
#: after a tag has already been pushed and a tag is not a thing to move.
GATES: tuple[tuple[str, list[str]], ...] = (
    ("lockfile is current", ["uv", "lock", "--check"]),
    ("environment matches the lock", ["uv", "sync", "--locked"]),
    ("ruff", ["uv", "run", "ruff", "check", "."]),
    ("ruff format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy", ["uv", "run", "mypy", "ios_mcp", "agent/ios_agent", "tui/ios_tui"]),
    ("offline tests", ["uv", "run", "pytest", "tests/unit", "tests/tui", "-q"]),
)

SEMVER = re.compile(r"^\d+\.\d+\.\d+([.-]?(a|b|rc|post|dev)\d+)?$")


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"failed: {' '.join(command)}")
    return (result.stdout or "").strip()


def current_version() -> str:
    with (ROOT / MEMBERS["ios-mcp"]).open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def tree_is_clean() -> bool:
    """Tracked files only. Untracked ones are someone's scratch work."""
    dirty = run(["git", "diff", "--name-only", "HEAD"], capture=True)
    return not dirty


def set_versions(value: str | None, bump: str | None) -> str:
    """Move every member, and let uv re-lock once at the end.

    `--frozen` on each member because re-locking three times to reach one
    answer is three resolutions for nothing; the single `uv lock` after is
    what writes the file, and `uv lock --check` in the gates proves it landed.
    """
    for name in MEMBERS:
        command = ["uv", "version", "--frozen", "--package", name]
        command += ["--bump", bump] if bump else [str(value)]
        print(f"  {run(command, capture=True)}")
    run(["uv", "lock"], capture=True)
    return current_version()


def set_manifest(version: str) -> int:
    """`server.json` states the version twice, and the second one is the trap.

    One field is the server entry and the other is the PyPI package it points
    at. A registry entry naming a version PyPI cannot confirm is worse than no
    entry at all, and fixing one cost 0.1.1 outright, because PyPI never
    accepts a re-upload.
    """
    path = ROOT / "server.json"
    manifest = json.loads(path.read_text())
    changed = 0
    if manifest.get("version") != version:
        manifest["version"] = version
        changed += 1
    for package in manifest.get("packages", []):
        if package.get("version") != version:
            package["version"] = version
            changed += 1
    if changed:
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", nargs="?", help="the exact version to move to")
    parser.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="derive the version from the current one instead of naming it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would move, write nothing",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="proceed with other changes in the tree (the diff stops being just the bump)",
    )
    args = parser.parse_args()

    if bool(args.version) == bool(args.bump):
        parser.error("name a version or pass --bump, not both and not neither")
    if args.version and not SEMVER.match(args.version):
        parser.error(f"{args.version!r} is not a version this project would publish")

    was = current_version()
    if not args.allow_dirty and not tree_is_clean():
        print(
            "the tree has uncommitted changes, so the release commit would carry "
            "more than the bump.\nCommit them first, or pass --allow-dirty.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        flag = ["--bump", args.bump] if args.bump else [args.version]
        print(f"currently {was}")
        for name in MEMBERS:
            said = run(["uv", "version", "--dry-run", "--package", name, *flag], capture=True)
            print(f"  {said}")
        print("  server.json: both version fields, and uv.lock: three member entries")
        return 0

    print(f"moving from {was}")
    now = set_versions(args.version, args.bump)
    fields = set_manifest(now)
    print(f"  server.json: {fields} field(s)")

    print("\ngates:")
    for label, command in GATES:
        print(f"  {label} ... ", end="", flush=True)
        run(command, capture=True)
        print("ok")

    print(
        f"\n{was} -> {now} in the tree, nothing committed.\n\n"
        "  git add -u && git commit\n"
        f"  git tag -a v{now} -m 'ios-mcp {now}'\n"
        f"  git push origin main && git push origin v{now}\n\n"
        "The tag push publishes to PyPI. That cannot be undone, and PyPI never\n"
        "accepts a re-upload of a version, so read the diff first."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
