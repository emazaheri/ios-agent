"""The import boundaries, enforced rather than documented.

Two invariants have been load-bearing since the first commit and were policed
only by review until now:

- Layers 1 to 4 must not import MCP. That is what lets an agent framework
  import `IosSession` directly instead of paying a protocol round-trip, and it
  is the reason the library is worth anything to a consumer that is not an MCP
  client.
- `ios_agent` reaches `ios_mcp` only through its public surface. It is a
  separate distribution precisely so this is checkable, and checking it is the
  difference between a boundary and a naming convention.

Both are read statically from the source rather than by importing anything, so
the test costs nothing and does not need the agent's dependencies installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_IOS_MCP = _REPO / "ios_mcp"
_IOS_AGENT = _REPO / "agent" / "ios_agent"

#: The only package allowed to know MCP exists.
_MCP_LAYER = _IOS_MCP / "server"

#: What `ios_agent` may import from `ios_mcp`, and nothing else.
#:
#: These are the modules that define the public surface: the session itself,
#: its configuration, its error taxonomy, and the types its methods return.
#: `actions.result` and `perception.digest` are on the list because
#: `ActionResult` and `Digest` are what every action and observation hand back;
#: a consumer cannot type its own code without them.
#:
#: Adding to this list is a deliberate act. If a slice needs something not
#: here, widening the surface is the decision being made, and it belongs in a
#: commit message rather than in a quiet edit.
_PUBLIC_SURFACE = frozenset(
    {
        "ios_mcp.session",
        "ios_mcp.config",
        "ios_mcp.errors",
        "ios_mcp.actions.result",
        "ios_mcp.perception.digest",
        "ios_mcp.devices.base",
        "ios_mcp.devices.pool",
    }
)


def _modules_imported_by(path: Path) -> set[str]:
    """Every dotted module name this file imports, however it spells it."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            found.update(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            if statement.level:
                continue  # relative, so it cannot cross a package boundary
            if statement.module:
                found.add(statement.module)
    return found


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _offenders(root: Path, predicate: object) -> list[str]:
    assert callable(predicate)
    bad: list[str] = []
    for path in _python_files(root):
        for module in sorted(_modules_imported_by(path)):
            if predicate(module):
                bad.append(f"{path.relative_to(_REPO)} imports {module}")
    return bad


def test_only_the_server_package_imports_mcp() -> None:
    """Layers 1 to 4 stay a plain async library.

    Break this and the library stops being importable by anything that is not
    an MCP client, which is most of the reason it is shaped the way it is.
    """
    offenders = [
        line
        for line in _offenders(
            _IOS_MCP, lambda m: m == "mcp" or m == "fastmcp" or m.startswith(("mcp.", "fastmcp."))
        )
        if not line.startswith("ios_mcp/server/")
    ]
    assert offenders == [], "only ios_mcp/server may import MCP:\n" + "\n".join(offenders)


def test_the_server_package_is_the_one_that_does() -> None:
    """A guard against the check above passing because nothing imports MCP at all."""
    imports = {m for path in _python_files(_MCP_LAYER) for m in _modules_imported_by(path)}
    assert any(m == "fastmcp" or m.startswith("fastmcp.") for m in imports)


def test_the_library_never_imports_the_agent() -> None:
    """The dependency points one way. `ios-mcp` must work for any agent."""
    offenders = _offenders(_IOS_MCP, lambda m: m == "ios_agent" or m.startswith("ios_agent."))
    assert offenders == [], "ios_mcp must not depend on ios_agent:\n" + "\n".join(offenders)


def test_the_agent_uses_only_the_public_surface() -> None:
    """The agent calls the tools; it does not reach past them.

    Importing `ios_mcp.perception.resolve` or `ios_mcp.wda` would mean the
    agent had started reimplementing resolution or talking to WebDriverAgent
    itself, which is the failure this whole split exists to make visible.
    """
    offenders = _offenders(
        _IOS_AGENT,
        lambda m: m.startswith("ios_mcp") and m not in _PUBLIC_SURFACE,
    )
    assert offenders == [], (
        "the agent may only import the public surface "
        f"({', '.join(sorted(_PUBLIC_SURFACE))}):\n" + "\n".join(offenders)
    )


def test_the_agent_never_imports_the_mcp_server() -> None:
    """Going through MCP is a backend choice, not something baked into the loop.

    The MCP-backed variant reaches the server as a client over a transport, the
    way any other consumer would. Importing `ios_mcp.server` in-process would
    prove nothing about whether the server works for anyone else.
    """
    offenders = _offenders(
        _IOS_AGENT, lambda m: m == "ios_mcp.server" or m.startswith("ios_mcp.server.")
    )
    assert offenders == [], "the agent must not import the server package:\n" + "\n".join(offenders)
