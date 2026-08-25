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
# Pinned deliberately: WebDriverAgent tracks Xcode closely, and an
# unpinned clone turns an Xcode upgrade into a silent behaviour change.
# Check https://github.com/appium/WebDriverAgent/tags before bumping.
WDA_REF="${WDA_REF:-v16.8.0}"
TARGET="${1:-simulator}"   # simulator | device
TEAM_ID="${TEAM_ID:-}"
UDID="${UDID:-}"
# Free Apple IDs cannot claim com.facebook.*, and any bundle id already
# registered by someone else is refused, so device builds need a unique one.
WDA_BUNDLE_ID="${WDA_BUNDLE_ID:-}"

command -v xcodebuild >/dev/null 2>&1 || {
  echo "error: xcodebuild not found. Install the full Xcode, then:" >&2
  echo "  sudo xcode-select -s /Applications/Xcode.app" >&2
  exit 1
}

if [ ! -d "$WDA_SRC/.git" ]; then
  echo "==> Cloning WebDriverAgent $WDA_REF"
  rm -rf "$WDA_SRC"
  if ! git clone --depth 1 --branch "$WDA_REF" \
      https://github.com/appium/WebDriverAgent.git "$WDA_SRC"; then
    rm -rf "$WDA_SRC"
    echo "error: could not clone WebDriverAgent at tag $WDA_REF." >&2
    echo "  Check the tag exists: git ls-remote --tags https://github.com/appium/WebDriverAgent.git" >&2
    echo "  Then re-run with: WDA_REF=<tag> scripts/prepare_wda.sh $TARGET" >&2
    exit 1
  fi
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
    TEAM_ID="$(security find-identity -v -p codesigning 2>/dev/null \
      | grep -oE '\(([A-Z0-9]{10})\)' | head -1 | tr -d '()')"
  fi
  if [ -z "$TEAM_ID" ]; then
    echo "error: no signing identity found." >&2
    echo "  Sign in via Xcode > Settings > Accounts, then open the WebDriverAgent" >&2
    echo "  project once and pick your team under Signing & Capabilities. Apple only" >&2
    echo "  issues a certificate when a project first asks for one." >&2
    exit 1
  fi
  if [ -z "$UDID" ]; then
    UDID="$(ios list 2>/dev/null | grep -oE '"[0-9A-Fa-f-]{25,}"' | head -1 | tr -d '"')"
  fi
  if [ -z "$UDID" ]; then
    echo "error: no device found. Connect an iPhone, or pass UDID=..." >&2
    exit 1
  fi

  echo "==> Building WebDriverAgentRunner for device $UDID (team $TEAM_ID)"
  BUNDLE_ARGS=()
  if [ -n "$WDA_BUNDLE_ID" ]; then
    echo "    bundle id: $WDA_BUNDLE_ID"
    BUNDLE_ARGS+=("PRODUCT_BUNDLE_IDENTIFIER=$WDA_BUNDLE_ID")
  fi
  # The destination must name the actual device. With a generic iOS
  # destination Apple never registers the phone with the team, so no
  # provisioning profile is issued and the build fails claiming the team has
  # no devices.
  xcodebuild build-for-testing \
    -project "$WDA_SRC/WebDriverAgent.xcodeproj" \
    -scheme WebDriverAgentRunner \
    -destination "id=$UDID" \
    -derivedDataPath "$DERIVED" \
    -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    CODE_SIGN_STYLE=Automatic \
    "${BUNDLE_ARGS[@]}"
fi

RUNNER="$(find "$DERIVED/Build/Products" -name 'WebDriverAgentRunner-Runner.app' -maxdepth 3 | head -1)"
[ -n "$RUNNER" ] || { echo "error: build produced no runner app" >&2; exit 1; }

rm -rf "$VENDOR/WebDriverAgentRunner-Runner.app"
cp -R "$RUNNER" "$VENDOR/"
DEST="$VENDOR/WebDriverAgentRunner-Runner.app"

# Do NOT strip the embedded XCTest frameworks here. Removing anything from a
# signed bundle invalidates its signature, and the device then refuses to
# install it with a bare ApplicationVerificationFailed. Launching through
# `ios runwda` works with the frameworks left in place.

echo
echo "Runner ready at: $DEST"
if [ "$TARGET" = "device" ]; then
  if [ ! -f "$DEST/embedded.mobileprovision" ]; then
    echo "error: no embedded.mobileprovision, so this build is NOT signed" >&2
    echo "  and will not launch on a phone. Check DEVELOPMENT_TEAM." >&2
    exit 1
  fi
  if ! codesign -dvv "$DEST" 2>&1 | grep -q "Authority=Apple Development"; then
    echo "error: the bundle is not signed by a development certificate." >&2
    echo "  build-for-testing leaves Apple's stock XCTRunner in place unless" >&2
    echo "  codesign can reach your key. If you saw repeated keychain prompts," >&2
    echo "  authorize it once with:" >&2
    echo "    security set-key-partition-list -S apple-tool:,apple:,codesign: -s \\" >&2
    echo "      ~/Library/Keychains/login.keychain-db" >&2
    exit 1
  fi

  BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DEST/Info.plist" 2>/dev/null)"
  echo
  echo "Install it:  ios install --path \"$DEST\" --udid $UDID"
  echo "Then on the phone, trust the developer under"
  echo "  Settings > General > VPN & Device Management."
  echo "iOS will not launch a free-account app until you do."
  echo
  echo "Point the server at it with:  IOS_MCP_WDA__BUNDLE_ID=$BUNDLE_ID"
  echo "Check the signing expiry with: uv run ios-mcp doctor"
fi
