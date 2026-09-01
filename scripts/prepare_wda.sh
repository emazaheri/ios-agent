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
  # The team must come from a provisioning profile, not from the signing
  # certificate's name. On a free personal team those are different strings
  # (cert "Apple Development: you (ABCDE12345)" against team FGHIJ67890), and
  # passing the certificate's one makes xcodebuild report "No Account for Team".
  #
  # `|| true` throughout: grep exits non-zero when it finds nothing, and under
  # `set -e -o pipefail` that would kill the script before the error message
  # explaining what was missing could print.
  if [ -z "$TEAM_ID" ]; then
    TEAM_ID="$(python3 -c '
import glob, plistlib, sys, pathlib
newest, team = 0, None
for path in glob.glob(str(pathlib.Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles/*.mobileprovision")):
    try:
        blob = pathlib.Path(path).read_bytes()
        start, end = blob.find(b"<?xml"), blob.find(b"</plist>")
        if start == -1 or end == -1:
            continue
        parsed = plistlib.loads(blob[start:end + 8])
        stamp = pathlib.Path(path).stat().st_mtime
        ids = parsed.get("TeamIdentifier") or []
        if ids and stamp > newest:
            newest, team = stamp, ids[0]
    except Exception:
        continue
print(team or "")
' 2>/dev/null)" || true
  fi
  # Without a profile to read, leave DEVELOPMENT_TEAM alone: whatever the user
  # selected in Xcode's Signing & Capabilities is more trustworthy than a guess.
  TEAM_ARGS=()
  if [ -n "$TEAM_ID" ]; then
    TEAM_ARGS+=("DEVELOPMENT_TEAM=$TEAM_ID")
  else
    echo "note: no provisioning profile found to read a team from;" >&2
    echo "  using whatever the Xcode project already has configured." >&2
  fi
  # go-ios first (it is cheap), then CoreDevice, which is the only one that
  # sees a device attached over Wi-Fi rather than USB.
  if [ -z "$UDID" ] && command -v ios >/dev/null 2>&1; then
    UDID="$(ios list 2>/dev/null | grep -oE '"[0-9A-Fa-f-]{25,}"' | head -1 | tr -d '"')" || true
  fi
  if [ -z "$UDID" ]; then
    DEVJSON="$(mktemp -t devicectl)" || true
    xcrun devicectl list devices --json-output "$DEVJSON" >/dev/null 2>&1 || true
    UDID="$(python3 -c '
import json, sys
try:
    devices = json.load(open(sys.argv[1]))["result"]["devices"]
except Exception:
    sys.exit(0)
for d in devices:
    udid = d.get("hardwareProperties", {}).get("udid")
    if udid:
        print(udid)
        break
' "$DEVJSON" 2>/dev/null)" || true
    rm -f "$DEVJSON"
  fi
  if [ -z "$UDID" ]; then
    echo "error: no device found. Connect an iPhone over USB, or pass UDID=..." >&2
    echo "  A device already paired can be used over Wi-Fi, but it must be" >&2
    echo "  awake and on the same network." >&2
    exit 1
  fi

  echo "==> Building WebDriverAgentRunner for device $UDID (team ${TEAM_ID:-from project})"
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
    CODE_SIGN_STYLE=Automatic \
    "${TEAM_ARGS[@]}" \
    "${BUNDLE_ARGS[@]}"
fi

# Both platforms build into the same DerivedData, so the products directory
# holds a Debug-iphoneos and a Debug-iphonesimulator app at once. Picking the
# first match copied the unsigned simulator bundle for a device build, which
# then failed to install with a bare verification error.
if [ "$TARGET" = "device" ]; then
  PRODUCTS="$DERIVED/Build/Products/Debug-iphoneos"
else
  PRODUCTS="$DERIVED/Build/Products/Debug-iphonesimulator"
fi
RUNNER="$PRODUCTS/WebDriverAgentRunner-Runner.app"
[ -d "$RUNNER" ] || {
  echo "error: no runner app at $RUNNER" >&2
  echo "  The build reported success but produced nothing for this platform." >&2
  exit 1
}

DEST="$VENDOR/WebDriverAgentRunner-Runner.app"
rm -rf "$DEST"
# ditto, not cp -R: it preserves the extended attributes and resource forks a
# signed bundle carries, so the copy verifies the same as the original.
ditto "$RUNNER" "$DEST"

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
  # Capture first, then match. Piping codesign into `grep -q` under
  # `set -o pipefail` is unreliable: grep exits on its first match and the
  # resulting SIGPIPE can surface as a pipeline failure, so a correctly signed
  # bundle intermittently reports as unsigned.
  SIGNING="$(codesign -dvv "$DEST" 2>&1)" || true
  case "$SIGNING" in
    *"Authority=Apple Development"*) ;;
    *)
      echo "error: the bundle is not signed by a development certificate." >&2
      echo "  build-for-testing leaves Apple's stock XCTRunner in place unless" >&2
      echo "  codesign can reach your key. If you saw repeated keychain prompts," >&2
      echo "  authorize it once with:" >&2
      echo "    security set-key-partition-list -S apple-tool:,apple:,codesign: -s \\" >&2
      echo "      ~/Library/Keychains/login.keychain-db" >&2
      echo "  codesign reported:" >&2
      echo "$SIGNING" | sed 's/^/    /' >&2
      exit 1
      ;;
  esac

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
