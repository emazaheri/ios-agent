"""The action invariants, asserted against the source rather than remembered.

CLAUDE.md states four of these in prose, and one of them had already drifted
before this test existed: "Idempotency keys stay on every action" is not true
of `handle_alert`, `wait_for`, `launch_app` or `open_url`, and has not been for
some time. A sentence cannot notice that about itself.

Everything here reads `ios_mcp/session.py` with `ast`, the way
`test_layering.py` reads imports, so the checks are against what the code does
and not against a second copy of the table.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ios_mcp.actions.catalog import CATALOG, DESTRUCTIVE_ACTIONS, READ_ONLY_ACTIONS

_SESSION = Path(__file__).resolve().parents[2] / "ios_mcp" / "session.py"


def _methods() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(_SESSION.read_text())
    session = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "IosSession")
    return {
        node.name: node
        for node in session.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def _calls(fn: ast.AST) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _returns_action_result(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return fn.returns is not None and ast.unparse(fn.returns) == "ActionResult"


@pytest.mark.parametrize("method", sorted(CATALOG))
def test_every_catalogued_action_exists(method: str) -> None:
    assert method in _methods(), f"the catalog names {method}, IosSession does not have it"


def test_every_action_returning_a_result_is_catalogued() -> None:
    """An action nobody catalogued is an action nobody gated or audited."""
    uncatalogued = {
        name
        for name, fn in _methods().items()
        if not name.startswith("_") and _returns_action_result(fn) and name not in CATALOG
    }
    assert not uncatalogued, f"actions missing from the catalog: {sorted(uncatalogued)}"


@pytest.mark.parametrize("method", sorted(CATALOG))
def test_routing_through_act_matches_the_source(method: str) -> None:
    """`_act` is what re-reads the screen and gates before acting.

    Getting this wrong in either direction is a real bug. An action that
    should resolve an element and does not acts on stale coordinates; one that
    routes through `_act` unexpectedly gets a target resolved for it that it
    never wanted.
    """
    methods = _methods()
    spec = CATALOG[method]
    body = methods[spec.delegates_to or method]

    assert ("_act" in _calls(body)) is spec.routes_through_act, (
        f"{method}: catalog says routes_through_act={spec.routes_through_act}, "
        f"the source says otherwise"
    )


@pytest.mark.parametrize("method", sorted(m for m in CATALOG if not CATALOG[m].read_only))
def test_every_acting_method_records_itself(method: str) -> None:
    """The audit invariant, made executable.

    Recording lives in `_finish`, and the actions that never pass through
    `_act` have to reach it themselves. When that was left to memory the trail
    went blind to scroll, swipe, drag, handle_alert, launch_app and open_url,
    which is most of a session.
    """
    methods = _methods()
    spec = CATALOG[method]
    calls = _calls(methods[spec.delegates_to or method])

    assert "_act" in calls or "_finish" in calls, (
        f"{method} changes the device and reaches neither _act nor _finish, "
        "so nothing about it enters the audit trail"
    )


@pytest.mark.parametrize("method", sorted(CATALOG))
def test_idempotency_keys_are_where_the_catalog_says(method: str) -> None:
    """Agent frameworks re-run the node an interrupt was raised from.

    Without a key a resumed graph taps Send twice. The catalog records which
    actions take one; this holds it to the signature, and the four that do not
    are the ones where a repeat lands in the same place anyway.
    """
    fn = _methods()[method]
    args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}

    assert ("idem_key" in args) is CATALOG[method].takes_idem_key, (
        f"{method}: catalog says takes_idem_key={CATALOG[method].takes_idem_key}"
    )


def test_the_gate_treats_exactly_the_read_verbs_as_safe() -> None:
    """The gate's safe list is the catalog's, not a second opinion about it."""
    from ios_mcp.config import Settings
    from ios_mcp.policy.gate import PolicyGate

    gate = PolicyGate(Settings().policy)
    for name in READ_ONLY_ACTIONS:
        assert not gate.classify(name, None).needs_approval, f"{name} should be safe"
    for name in DESTRUCTIVE_ACTIONS:
        assert name not in READ_ONLY_ACTIONS


def test_the_advertised_annotations_agree_with_the_catalog() -> None:
    """What clients are told, against what the table says.

    The two disagreed when this was written, in both directions, which is the
    argument for the table. `ios_type_secret` advertises merely mutating where
    `ios_type` advertises destructive, because the value reaches no transcript;
    `ios_handle_alert` advertises destructive though it touches nothing itself,
    because the button it presses is the one granting a permission. Both are
    deliberate, and neither was written down anywhere until now.
    """
    from ios_mcp.actions.catalog import ActionSpec

    tools = {
        "ios_tap": "tap",
        "ios_type": "type_text",
        "ios_type_secret": "type_secret",
        "ios_set_value": "set_value",
        "ios_scroll": "scroll",
        "ios_swipe": "swipe",
        "ios_drag": "drag",
        "ios_press_button": "press_button",
        "ios_handle_alert": "handle_alert",
        "ios_launch_app": "launch_app",
        "ios_open_url": "open_url",
        "ios_observe": "observe",
        "ios_screenshot": "screenshot",
        "ios_read_text": "read_text",
        "ios_find": "find",
        "ios_wait_for": "wait_for",
    }
    annotations = _advertised_annotations()

    for tool, method in tools.items():
        spec: ActionSpec = CATALOG[method]
        assert tool in annotations, f"{tool} is not registered on the server"
        hints = annotations[tool]
        assert hints["destructiveHint"] is spec.destructive, (
            f"{tool} advertises destructiveHint={hints['destructiveHint']}, "
            f"the catalog says {spec.destructive}"
        )
        assert hints["readOnlyHint"] is spec.read_only, (
            f"{tool} advertises readOnlyHint={hints['readOnlyHint']}, "
            f"the catalog says {spec.read_only}"
        )


def _advertised_annotations() -> dict[str, dict[str, bool]]:
    """Read the annotation constant each tool is decorated with, statically.

    Statically because importing the server to ask it would drag FastMCP into
    a unit test for no gain: the decorator argument is a name in the source,
    and the constants it names are four dictionaries in one file.
    """
    from ios_mcp.server import annotations as annotation_module

    constants = {
        name: value
        for name, value in vars(annotation_module).items()
        if name.isupper() and isinstance(value, dict)
    }
    out: dict[str, dict[str, bool]] = {}
    server_dir = _SESSION.parent / "server"
    for path in server_dir.glob("tools_*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for keyword in decorator.keywords:
                    if keyword.arg == "annotations" and isinstance(keyword.value, ast.Name):
                        out[node.name] = constants[keyword.value.id]
    return out
