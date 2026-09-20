"""One version, asserted across every place that carries it.

Three distributions and a registry manifest each state the version
separately, and nothing but care kept them equal. Care already failed once:
publishing 0.1.0 to the MCP registry was refused because the manifest pointed
at a package version PyPI could not confirm, and since PyPI never accepts a
re-upload of a version, fixing it cost 0.1.1 outright.

`server.json` is the sharp edge, because it says the version *twice*: once for
the server entry and once for the PyPI package it points at. A registry entry
naming a version that does not exist is worse than no entry.

So this is the release checklist as a test. It runs offline in CI, and the
failure names the file that was missed.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Every distribution moves together. Independent numbers were tried and drifted
#: immediately: the repository sat at `ios-mcp` 0.1.1 with both others on 0.1.0,
#: under a single repository-level tag that therefore named none of them. Only
#: `ios-mcp` is published, so separate numbers bought nothing to begin with.
PYPROJECTS = ("pyproject.toml", "agent/pyproject.toml", "tui/pyproject.toml")


def _version(rel: str) -> str:
    with (ROOT / rel).open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_every_distribution_carries_the_same_version() -> None:
    versions = {rel: _version(rel) for rel in PYPROJECTS}
    assert len(set(versions.values())) == 1, f"versions disagree: {versions}"


def test_the_registry_manifest_matches_the_package() -> None:
    """Both of `server.json`'s version fields, not just the obvious one."""
    manifest = json.loads((ROOT / "server.json").read_text())
    expected = _version("pyproject.toml")

    assert manifest["version"] == expected, (
        f"server.json version {manifest['version']!r} != ios-mcp {expected!r}"
    )
    for package in manifest["packages"]:
        assert package["version"] == expected, (
            f"server.json packages[{package['identifier']}] "
            f"{package['version']!r} != ios-mcp {expected!r}"
        )


def test_the_manifest_points_at_the_published_package() -> None:
    """`ios-mcp` is the distribution on PyPI; the other two are not."""
    manifest = json.loads((ROOT / "server.json").read_text())
    identifiers = {p["identifier"] for p in manifest["packages"]}
    assert identifiers == {"ios-mcp"}, f"unexpected packages in server.json: {identifiers}"


def test_only_the_published_distribution_is_publishable() -> None:
    """A broad `uv publish` must not be able to ship the other two.

    PyPI rejects an upload carrying this classifier, so the guard is the
    index's rather than ours, which is the only kind worth relying on.
    """
    for rel in ("agent/pyproject.toml", "tui/pyproject.toml"):
        with (ROOT / rel).open("rb") as handle:
            classifiers = tomllib.load(handle)["project"].get("classifiers", [])
        assert "Private :: Do Not Upload" in classifiers, f"{rel} could be published"

    with (ROOT / "pyproject.toml").open("rb") as handle:
        published = tomllib.load(handle)["project"].get("classifiers", [])
    assert "Private :: Do Not Upload" not in published, "ios-mcp is the one that ships"
