"""The same agent loop, reaching the device over MCP instead of in-process.

`SessionBackend` calls `IosSession` directly, which is the production path and
what the no-MCP-imports rule in layers 1 to 4 was preserved for. This is the
other half of that decision: proof that the server works for a consumer that is
not privileged, and a number for what the protocol costs.

## Why it connects rather than imports

`tests/unit/test_layering.py` forbids `ios_agent` from importing
`ios_mcp.server`, and that rule is the point rather than an obstacle. Reaching
into the server in-process would exercise a code path no real client has, and
prove nothing about whether anyone else can drive it. So this takes a
`fastmcp.Client` and speaks the protocol. Whether the transport underneath is
an in-memory pipe or a stdio subprocess is the caller's business; the client is
constructed outside and handed in.

## Why the counters still work

The measurements this project reports are observations, actions and device
tokens, and all three are counted here exactly as `SessionBackend` counts them:
at the call site, on the payload that comes back. The MCP payload is the same
dict `ActionResult.to_dict()` produces, because the server serialises it
unchanged, so `device_tokens` is directly comparable between the two backends.
That comparability is the whole reason to build this.

## What it cannot do, and why that is honest

Two capabilities have no protocol equivalent, and both are deliberately absent
rather than faked:

- **Halting and loop detection** live on the session object. A remote client
  can ask `ios_session_status` for them, but that is a round trip per turn to
  poll a flag, which would distort the very numbers this exists to measure. The
  agent's own step budget still bounds a runaway loop.
- **Approval** arrives as an `action_requires_approval` error carrying a
  signature, which is exactly the human-in-the-loop path SAFETY.md documents
  for a client that cannot elicit. It is surfaced by re-raising, so the tool
  layer's `interrupt()` handles it identically to the direct path.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ios_agent.verify import Attempt, Verifier

#: Matches the digest's own estimate and both eval harnesses, so a number from
#: this backend can be put beside one from `SessionBackend` without conversion.
_CHARS_PER_TOKEN = 4


class McpClient(Protocol):
    """The slice of `fastmcp.Client` this needs.

    Narrow on purpose: it keeps the backend testable without a live server, and
    documents that nothing here depends on client internals.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class McpBackend:
    """Drives the device by calling MCP tools on a connected client."""

    def __init__(self, client: McpClient, verifier: Verifier | None = None) -> None:
        self.client = client
        self.stats = _Stats()
        self.last_screen = ""
        self.verifier = verifier or Verifier()
        #: Signatures a human has approved, replayed on the retry. The direct
        #: backend hands these to the session object; over MCP they travel as
        #: an argument, which is the documented re-entry path.
        self._approved: set[str] = set()

    # -- perception --------------------------------------------------------

    async def observe(self) -> str:
        payload = await self._call("ios_observe", {})
        self.stats.observations += 1
        self._charge(payload)
        self.last_screen = str(payload.get("text", ""))
        return self.last_screen

    # -- actions -----------------------------------------------------------

    async def tap(self, target: str, *, idem_key: str) -> str:
        return await self._act(
            ("tap", target.strip().lower(), ""),
            "ios_tap",
            {"target": target, "idem_key": idem_key},
        )

    async def type_text(self, text: str, target: str | None, *, idem_key: str) -> str:
        args: dict[str, Any] = {"text": text, "idem_key": idem_key}
        if target:
            args["target"] = target
        return await self._act(
            ("type_text", (target or "").strip().lower(), text.strip().lower()),
            "ios_type",
            args,
        )

    async def set_value(self, value: str, target: str, *, idem_key: str) -> str:
        return await self._act(
            ("set_value", target.strip().lower(), value.strip().lower()),
            "ios_set_value",
            {"value": value, "target": target, "idem_key": idem_key},
        )

    async def scroll(self, direction: str, until: str | None, *, idem_key: str) -> str:
        args: dict[str, Any] = {"direction": direction, "idem_key": idem_key}
        if until:
            args["until"] = until
        return await self._act(
            ("scroll", direction.strip().lower(), (until or "").strip().lower()),
            "ios_scroll",
            args,
        )

    async def press_button(self, name: str, *, idem_key: str) -> str:
        return await self._act(
            ("press_button", name.strip().lower(), ""),
            "ios_press_button",
            {"name": name, "idem_key": idem_key},
        )

    async def open_url(self, url: str) -> str:
        return await self._act(("open_url", url.strip().lower(), ""), "ios_open_url", {"url": url})

    # -- approval ----------------------------------------------------------

    def approve(self, signature: str) -> None:
        """Remember a human's yes, to be replayed on the retry.

        Over MCP a signature is passed back as `approve=` on the same call
        rather than recorded on a session object, so it has to be held here
        until that retry happens.
        """
        self._approved.add(signature)

    # -- stopping ----------------------------------------------------------

    def stop_reason(self) -> str | None:
        """Nothing to report without polling.

        `halted` and `looping` live on the session, and asking for them would
        cost a round trip per turn purely to read a flag, which would corrupt
        the latency comparison this backend exists to produce. The agent's step
        budget still bounds a runaway loop.
        """
        return None

    # -- internals ---------------------------------------------------------

    async def _act(self, key: Attempt, tool: str, args: dict[str, Any]) -> str:
        refusal = self.verifier.check(key)
        if refusal is not None:
            self.stats.refusals += 1
            return str(refusal.note)

        payload = await self._call(tool, args)
        self.stats.actions += 1
        self._charge(payload)

        verdict = self.verifier.record(key, _AsResult(payload))
        rendered = self._render(payload)
        return f"{rendered}\n{verdict.note}" if verdict.note else rendered

    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """One protocol round trip, with a structured error re-raised.

        The server marshals `IosAutomationError` into a `ToolError` whose text
        is the error's JSON, so this reconstructs the typed exception. Without
        it the agent would see a wall of JSON where the direct backend sees a
        readable failure with a hint and candidate elements attached.
        """
        from fastmcp.exceptions import ToolError

        if self._approved:
            # Replay any outstanding approval on the retry that follows it.
            args = {**args, "approve": next(iter(self._approved))}
        try:
            result = await self.client.call_tool(tool, args)
        except ToolError as exc:
            raise _typed(exc) from exc
        return _payload_of(result)

    def _render(self, payload: dict[str, Any]) -> str:
        """The same shape `SessionBackend` renders, from the same fields."""
        lines = [f"{payload.get('action', 'action')}: {'ok' if payload.get('ok') else 'failed'}"]
        if payload.get("screen_changed") is False:
            lines.append("the screen did not change")
        if payload.get("note"):
            lines.append(str(payload["note"]))
        if payload.get("hint"):
            lines.append(str(payload["hint"]))
        change = payload.get("change")
        if isinstance(change, dict) and change.get("text"):
            lines.append(str(change["text"]))
        screen = payload.get("screen")
        if isinstance(screen, dict) and screen.get("text"):
            self.last_screen = str(screen["text"])
            lines.append(self.last_screen)
        return "\n".join(lines)

    def _charge(self, payload: object) -> None:
        self.stats.device_tokens += len(json.dumps(payload, default=str)) // _CHARS_PER_TOKEN


class _Stats:
    """Mirrors `BackendStats` so both backends report identically."""

    def __init__(self) -> None:
        self.observations = 0
        self.actions = 0
        self.device_tokens = 0
        self.refusals = 0

    @property
    def observation_overhead(self) -> float:
        return self.observations / self.actions if self.actions else float(self.observations)


class _AsResult:
    """Adapts an MCP payload to what `Verifier.record` reads.

    Verification is deliberately shared between the two backends rather than
    reimplemented: if it judged differently over MCP, the comparison between
    them would be measuring the verifier instead of the transport.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.screen_changed = bool(payload.get("screen_changed"))
        screen = payload.get("screen")
        self.digest = screen if isinstance(screen, dict) and screen else None
        change = payload.get("change")
        self.delta = _AsDelta(change) if isinstance(change, dict) else None


class _AsDelta:
    def __init__(self, change: dict[str, Any]) -> None:
        self._empty = not (change.get("added") or change.get("removed") or change.get("changed"))

    @property
    def empty(self) -> bool:
        return self._empty


def _payload_of(result: Any) -> dict[str, Any]:
    """Pull the tool's dict out of whatever the client version returns."""
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        inner = structured.get("result", structured)
        return inner if isinstance(inner, dict) else structured
    if isinstance(result, dict):
        return result
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {"ok": False, "note": str(result)}


def _typed(exc: Exception) -> Exception:
    """Rebuild the typed error the server serialised into a ToolError."""
    from ios_mcp.errors import ErrorCode, IosAutomationError

    try:
        payload = json.loads(str(exc))
    except (json.JSONDecodeError, ValueError):
        return exc
    if not isinstance(payload, dict) or "error" not in payload:
        return exc
    try:
        code = ErrorCode(payload["error"])
    except ValueError:
        return exc
    return IosAutomationError(
        payload.get("message", str(exc)),
        code=code,
        hint=payload.get("hint"),
        details=payload.get("details"),
        recoverable=bool(payload.get("recoverable")),
    )
