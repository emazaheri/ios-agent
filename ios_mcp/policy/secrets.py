"""Resolve secrets by reference so they never enter the model's context.

The agent asks to type ``secret_ref="icloud-password"``; the value is fetched
here and sent straight to the device. It appears in no prompt, no tool result,
and no audit entry.
"""

from __future__ import annotations

import os
import platform

from ios_mcp.devices.shell import run, which
from ios_mcp.errors import SecretNotFound

#: Environment variable prefix used as the cross-platform fallback.
ENV_PREFIX = "IOS_MCP_SECRET_"
#: Keychain service under which secrets are looked up on macOS.
KEYCHAIN_SERVICE = "ios-mcp"


async def resolve_secret(ref: str) -> str:
    """Look up a secret by reference: environment first, then the macOS keychain."""
    if not ref or not ref.replace("-", "").replace("_", "").isalnum():
        raise SecretNotFound(
            f"Invalid secret reference {ref!r}",
            hint="Use a simple name such as 'icloud-password'.",
        )

    env_key = ENV_PREFIX + ref.upper().replace("-", "_")
    value = os.environ.get(env_key)
    if value:
        return value

    if platform.system() == "Darwin" and which("security"):
        result = await run(
            "security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            ref,
            "-w",
            timeout=15.0,
        )
        if result.ok and result.stdout:
            return result.stdout

    raise SecretNotFound(
        f"No secret stored under {ref!r}",
        hint=(
            f"Add it to the keychain with: security add-generic-password "
            f"-s {KEYCHAIN_SERVICE} -a {ref} -w, "
            f"or set the environment variable {env_key}."
        ),
    )
