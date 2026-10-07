# Build on the library

The MCP server and the bundled agent are both thin layers over one async
Python library, `IosSession`. Your own agent can use it directly and skip the
protocol: payloads are identical, and over MCP each call costs about 1.6 ms
more and no extra tokens.

Both examples below were run against an iOS 27 simulator as written.

## Drive the device yourself

```python
import asyncio

from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession


async def main() -> None:
    settings = Settings.load()
    pool = DevicePool(settings)
    try:
        lease = await pool.acquire()  # or a UDID, or part of a device name
        session = IosSession(lease, settings)

        await session.launch_app("com.apple.Preferences", fresh=True)
        print((await session.observe()).render())

        await session.tap(target="Accessibility")
        await session.tap(target="Display & Text Size")

        result = await session.set_value("on", target="Bold Text")
        print(result.ok, result.screen_changed, result.already_satisfied)
        # True True False

        result = await session.set_value("on", target="Bold Text")
        print(result.ok, result.screen_changed, result.already_satisfied)
        # True False True: already on, so nothing was touched
    finally:
        await pool.release_all()


asyncio.run(main())
```

Things to know:

- `pool.acquire()` with no argument picks a ready device and prefers a
  simulator. Pass a UDID or part of a name to choose. `release_all()` stops
  the WebDriverAgent runner; the runner also stops if your process dies.
- `observe()` returns a `Digest`. `render()` is the compact text a model
  reads; `to_dict()` is the same screen as data.
- Name things by `target` (the text on screen) or by `ref` (`"e3"` from the
  last `observe()`). Never coordinates.
- Every action returns an `ActionResult`: `ok`, `screen_changed`,
  `already_satisfied`, and the screen it produced as `digest` or, when
  similar, as `delta`. Read the result rather than observing again.
- A failure raises a typed error from `ios_mcp.errors` with a `code` and a
  `hint`. A gated action raises `ActionRequiresApproval`; see
  [Approvals and secrets](approvals-and-secrets.md).
- Settings come from the environment, `.env` and `ios-mcp.toml`; see the
  [configuration reference](../.env.example).

The modules above, with `ios_mcp.errors`, `ios_mcp.actions.result`,
`ios_mcp.perception.digest` and `ios_mcp.devices.base`, are the supported
surface. A test holds the bundled agent to that list, so they are the ones
that stay stable.

## Hand it a goal

The bundled agent runs over the same session:

```python
import asyncio
from typing import Any

from ios_agent import run_goal
from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession


async def approve(request: dict[str, Any]) -> bool:
    """Called before anything the gate stops. Return True to allow it."""
    answer = input(f"{request['action']}: {request['reason']}. Allow? [y/N] ")
    return answer.strip().lower() == "y"


async def main() -> None:
    settings = Settings.load()
    pool = DevicePool(settings)
    try:
        session = IosSession(await pool.acquire(), settings)
        outcome = await run_goal(session, "turn on bold text", approve=approve)
        print(outcome.verified, outcome.summary)
        print(outcome.stats.actions, "actions,", outcome.turns, "model turns")
    finally:
        await pool.release_all()


asyncio.run(main())
```

```
True Bold Text is now turned on.
1 actions, 3 model turns
```

- The model comes from `IOS_AGENT_PROVIDER` and `IOS_AGENT_MODEL`; see
  [Choose a model](../agent/README.md). `run_goal` also takes `settings=`,
  `max_steps=`, and a `model=` factory for tests.
- Read `outcome.verified`, not `outcome.succeeded`. The second is the agent's
  claim; the first is that claim after the device has been allowed to
  contradict it.
- Without `approve`, everything gated is refused, because nobody is there to
  ask.
