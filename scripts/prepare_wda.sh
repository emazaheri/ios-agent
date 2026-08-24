#!/usr/bin/env bash
# Build and sign a WebDriverAgent runner into vendor/wda/.
#
# A prebuilt runner starts sessions several times faster than building on
# demand, and on iOS 17+ it is the only reliable way to launch WDA without
# xcodebuild (see the --preinstalled path in the real-device adapter).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/wda"
WDA_SRC="${WDA_SRC:-$VENDOR/WebDriverAgent}"
WDA_REF="${WDA_REF:-v9.16.0}"
TARGET="${1:-simulator}"   # simulator | device
TEAM_ID="${TEAM_ID:-}"

command -v xcodebuild >/dev/null 2>&1 || {
  echo "error: xcodebuild not found. Install the full Xcode, then:" >&2
  echo "  sudo xcode-select -s /Applications/Xcode.app" >&2
  exit 1
}

if [ ! -d "$WDA_SRC" ]; then
  echo "==> Cloning WebDriverAgent $WDA_REF"
  git clone --depth 1 --branch "$WDA_REF" https://github.com/appium/WebDriverAgent.git "$WDA_SRC"
fi

DERIVED="$VENDOR/DerivedData"
mkdir -p "$DERIVED"

if [ "$TARGET" = "simulator" ]; then
  echo "==> Building WebDriverAgentRunner for the simulator"
  xcodebuild build-for-testing \
    -project "$WDA_SRC/WebDriverAgent.xcodeproj" \
    -scheme WebDriverAgentRunner \
    -destination 'generic/platform=iOS Simulator' \
    -derivedDataPath "$DERIVED" \
    CODE_SIGNING_ALLOWED=NO
else
  if [ -z "$TEAM_ID" ]; then
    echo "error: TEAM_ID is required for device builds." >&2
    echo "  Find it with: security find-identity -v -p codesigning" >&2
    exit 1
  fi
  echo "==> Building WebDriverAgentRunner for a physical device (team $TEAM_ID)"
  xcodebuild build-for-testing \
    -project "$WDA_SRC/WebDriverAgent.xcodeproj" \
    -scheme WebDriverAgentRunner \
    -destination 'generic/platform=iOS' \
    -derivedDataPath "$DERIVED" \
    -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    CODE_SIGN_STYLE=Automatic
fi

RUNNER="$(find "$DERIVED/Build/Products" -name 'WebDriverAgentRunner-Runner.app' -maxdepth 3 | head -1)"
[ -n "$RUNNER" ] || { echo "error: build produced no runner app" >&2; exit 1; }

rm -rf "$VENDOR/WebDriverAgentRunner-Runner.app"
cp -R "$RUNNER" "$VENDOR/"
DEST="$VENDOR/WebDriverAgentRunner-Runner.app"

# On iOS 17+ testmanagerd moved to com.apple.dt.testmanagerd.runner and the
# runner must bind the device's own XCTest libraries rather than its embedded
# copies, or it crashes on launch outside xcodebuild.
if [ "$TARGET" = "device" ]; then
  echo "==> Stripping embedded XCTest frameworks (required for iOS 17+ preinstalled mode)"
  rm -rf "$DEST"/Frameworks/XC*.framework
fi

echo
echo "Runner ready at: $DEST"
if [ -f "$DEST/embedded.mobileprovision" ]; then
  echo "Check signing expiry with: uv run ios-mcp doctor"
fi
