#!/usr/bin/env bash
# Build WebDriverAgent into this clone's vendor/wda/.
#
# The script itself ships inside the package, at ios_mcp/devices/prepare_wda.sh,
# so an installed copy can build its own runner with `ios-mcp prepare-wda`. This
# wrapper keeps the clone's path and its documented command working.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WDA_HOME="${WDA_HOME:-$ROOT/vendor/wda}"
exec bash "$ROOT/ios_mcp/devices/prepare_wda.sh" "$@"
