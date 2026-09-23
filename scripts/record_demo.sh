#!/usr/bin/env bash
# Record the terminal and the phone side by side, for the README.
#
# The two halves are captured separately and composited, rather than screen
# recording a region of the desktop:
#
#   the simulator  through `simctl io recordVideo`, which reads the device
#                  framebuffer, so there is no window chrome, no cursor, and
#                  nothing else on the desktop can drift through the shot
#   the terminal   through asciinema, which records the character stream, so
#                  the text stays crisp at any output size and the file stays
#                  small. A screen recorder would resample the font.
#
# Both start before the agent does and stop together, which is the only way the
# phone and the transcript stay in step. Compositing two independently timed
# recordings afterwards is guesswork.
#
# One rule this script cannot enforce and every reader has to keep: a task that
# has already been completed is never reenacted for the camera. What is
# published is a recording of a real run, first attempt included, or it is not
# published. The same reason the closing line insists the caption states the
# playback speed: every honest number in this repository is undercut by one
# dishonest picture.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- knobs ------------------------------------------------------------------
# Bundle id the session opens on. Leaving this empty does NOT mean "the home
# screen": WebDriverAgent activates whatever app was last in front, so the demo
# starts somewhere arbitrary. `com.apple.springboard` is the home screen, and
# is usually what you want when the agent is meant to open the app itself.
APP="${APP:-com.apple.springboard}"
DEVICE="${DEVICE:-}"                       # udid or name; empty means the booted one
# Where recordings accumulate. Each run gets its own timestamped directory
# under it, so a second run never silently overwrites the first, and so the
# directory this run wrote is a value that is *kept* rather than one the next
# reader rebuilds from a convention. `--latest` hands it back.
DEMO_ROOT="${DEMO_ROOT:-$ROOT/.artifacts/demo}"
OUT="${OUT:-}"                             # empty means "a fresh timestamped one"
TIMEOUT="${TIMEOUT:-180}"                  # hard cap; the TUI does not exit on its own
SPEED="${SPEED:-2}"                        # playback speed. Say so in the caption.
HEIGHT="${HEIGHT:-720}"                    # composite height, both halves scaled to it
FPS="${FPS:-12}"                           # GIF frame rate
GIF_WIDTH="${GIF_WIDTH:-1000}"             # GIF width; the mp4 keeps full resolution
COLS="${COLS:-100}"                        # terminal size to record at
ROWS="${ROWS:-30}"
FONT_SIZE="${FONT_SIZE:-16}"               # agg font size, drives terminal sharpness
THEME="${THEME:-asciinema}"                # agg theme
MAKE_GIF="${MAKE_GIF:-1}"                  # a GIF is what plays inline on GitHub
# agg compresses any pause longer than --idle-time-limit, five seconds by
# default. That silently desynchronises the two halves: the terminal's timeline
# gets shorter while the phone's stays real, so by the end they are showing
# different moments. Effectively disabled here, and the tail is cut with CLIP
# instead, which shortens both halves by the same amount.
IDLE_LIMIT="${IDLE_LIMIT:-86400}"
CLIP="${CLIP:-}"                           # seconds of real time to keep, "" for all
RESET_APP_STATE="${RESET_APP_STATE:-1}"    # 0 to keep whatever the app remembers
# Which apps get their state wiped. Defaults to APP, but is separate from it so
# a demo can start on the home screen with APP empty and still clear the app the
# agent is about to open. Leaving APP empty is usually the better demo: the
# agent opens the app itself, after the goal, instead of finding it already
# open before the goal has even appeared.
RESET_APPS="${RESET_APPS:-$APP}"
# Wiping an app's state also forgets that its first-run dialogs were ever
# answered, so they come back and the demo becomes a recording of someone
# dismissing onboarding. This opens the app once beforehand and taps them away,
# leaving the recording to show only the work. Point it at a *different* place
# than the demo uses, or the warm-up lands in the app's recents.
WARMUP_URL="${WARMUP_URL:-}"
WARMUP_DISMISS="${WARMUP_DISMISS:-Not Now,OK,Continue,Allow Once,Allow,Skip}"
# Seconds of each half to drop from the front. Bringing a device up takes 20s
# or more before the agent does anything, and nobody wants to watch that.
# Two knobs rather than one because the halves do not share a clock, see below.
START="${START:-0}"
PHONE_START="${PHONE_START:-$START}"
# Held on the phone's last frame at the end. `simctl io recordVideo` writes
# variable-frame-rate video whose reported duration does not match the wall
# time it covers, so the two halves cannot be aligned by scaling one onto the
# other; a linear stretch leaves the phone visibly behind. They are anchored at
# their *ends* instead, which is the moment that has to be right: the phone
# rests on its final screen while the agent's reply finishes printing.
PHONE_HOLD="${PHONE_HOLD:-8}"
# The reply is the payoff, and it appears in the last moment of the run. Ending
# there gives a reader no time to read it, and a GIF loops straight back to the
# beginning. Measured in finished-video seconds, after SPEED, so it means what
# it says regardless of how fast the run is played.
END_HOLD="${END_HOLD:-3}"
# The same courtesy at the start. A viewer needs a beat to take in what they
# are looking at, a phone on its home screen beside a terminal, before either
# of them starts moving. A GIF loops, so this doubles as the pause between
# repeats.
START_HOLD="${START_HOLD:-2.5}"

usage() {
  cat <<'USAGE'
usage: scripts/record_demo.sh [--latest] [--overwrite] ["the goal, in plain English"]

Records the iOS Simulator and the ios-agent terminal app side by side while the
agent pursues one goal, and writes an mp4 and a GIF.

  --latest                    print the paths the most recent recording wrote,
                              and exit. Nothing is recorded.
  --overwrite                 allow writing into an OUT that already holds a
                              finished demo.mp4. Only reachable with OUT= set,
                              since a bare run gets a fresh directory.

  APP=com.apple.springboard   bundle id the session opens on; springboard is
                              the home screen. Empty means "whatever was last
                              in front", which is rarely what you want.
  DEVICE=                     udid or name; default is the booted simulator
  DEMO_ROOT=.artifacts/demo   where the timestamped run directories accumulate
  OUT=<DEMO_ROOT>/<stamp>     this run's directory; set it to pin the path
  TIMEOUT=180                 seconds before the recording is cut short
  SPEED=2                     playback speed of the finished video
  HEIGHT=720                  height both halves are scaled to
  CLIP=                       seconds of real time to keep, "" for the whole run
  START=0  PHONE_START=$START  seconds to cut from the front of each half
  PHONE_HOLD=8                that many seconds of the phone's last frame, so
                              it waits on screen while the reply prints
  END_HOLD=3                  seconds the finished video rests on its last
                              frame, so the reply can actually be read
  START_HOLD=2.5              seconds it rests on the first frame, so the
                              phone's home screen registers before it moves
  RESET_APP_STATE=1           wipe saved state first, so the agent has to work
                              rather than tap something it did last time
  RESET_APPS=$APP             which bundle ids that applies to
  WARMUP_URL=                 open this once first and dismiss the first-run
                              dialogs, so they are not in the recording
  WARMUP_DISMISS=...          the button labels that count as dismissal
  FPS=12  GIF_WIDTH=1000      GIF quality against GIF size
  COLS=100  ROWS=30           terminal size to record at
  FONT_SIZE=16  THEME=asciinema
  MAKE_GIF=1                  0 to skip the GIF and keep only the mp4

The terminal app does not exit when a goal finishes, by design. Press ctrl+q
when the run looks done, or let TIMEOUT end it.
USAGE
}

# --- flags ------------------------------------------------------------------
# Parsed before the goal, which is whatever is left. A goal is free text and
# may begin with anything, so only the leading tokens are read as flags and the
# loop stops at the first thing that is not one.
OVERWRITE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --overwrite) OVERWRITE=1; shift ;;
    --latest)
      # The point of `--latest` is that nothing here re-derives a path. The
      # directories are listed and the newest that actually finished wins, so
      # an aborted run does not become the answer.
      latest=""
      for dir in "$DEMO_ROOT"/*/; do
        if [ -s "${dir}demo.mp4" ]; then latest="$dir"; fi
      done
      if [ -z "$latest" ]; then
        echo "No finished recording under $DEMO_ROOT" >&2
        exit 1
      fi
      echo "${latest%/}/demo.mp4"
      if [ -s "${latest}demo.gif" ]; then
        echo "${latest%/}/demo.gif"
        echo
        echo "To use it as the README demo:"
        echo "  cp ${latest%/}/demo.gif $ROOT/docs/images/demo.gif"
      fi
      exit 0
      ;;
    --) shift; break ;;
    *) break ;;
  esac
done

# The goal is the point of the whole thing, so it is the first argument after
# any flags. "Turn on Bold Text in Settings." is the default because it runs at
# the oracle's floor, 3 actions and 1 observation, which is a demo that shows
# the system working rather than the model flailing.
GOAL="${1:-Turn on Bold Text in Settings.}"

# A fresh directory per run unless one was pinned, and never a silent overwrite
# of a finished recording. Recording is expensive and a demo is often kept for
# comparison; the failure worth preventing is the second run erasing the first.
if [ -z "$OUT" ]; then
  OUT="$DEMO_ROOT/$(date +%Y%m%d-%H%M%S)"
elif [ -s "$OUT/demo.mp4" ] && [ "$OVERWRITE" != "1" ]; then
  echo "error: $OUT already holds a finished demo.mp4." >&2
  echo "  Pass --overwrite to replace it, or unset OUT for a fresh directory." >&2
  exit 1
fi

# --- what has to be installed ----------------------------------------------
need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: $1 not found." >&2
    echo "  $2" >&2
    exit 1
  }
}
need xcrun     "Install Xcode, then: sudo xcode-select -s /Applications/Xcode.app"
need ffmpeg    "brew install ffmpeg"
need asciinema "brew install asciinema"
need agg       "brew install agg   # renders an asciinema cast to a GIF"

# --- which simulator --------------------------------------------------------
if [ -n "$DEVICE" ]; then
  UDID="$DEVICE"
else
  # Booted only. Recording a shut-down simulator produces a black rectangle,
  # which is a failure worth catching here rather than in the composite.
  UDID="$(xcrun simctl list devices booted -j |
    python3 -c 'import json,sys
d=json.load(sys.stdin)["devices"]
ids=[x["udid"] for v in d.values() for x in v if x.get("state")=="Booted"]
print(ids[0] if ids else "")')"
fi
[ -n "$UDID" ] || {
  echo "error: no booted simulator." >&2
  echo "  uv run ios-agent devices      # see what exists" >&2
  echo "  xcrun simctl boot <udid>      # then re-run this" >&2
  exit 1
}

mkdir -p "$OUT"
SIM_MP4="$OUT/simulator.mp4"
CAST="$OUT/terminal.cast"
TUI_GIF="$OUT/terminal.gif"
TUI_MP4="$OUT/terminal.mp4"
FINAL_MP4="$OUT/demo.mp4"
FINAL_GIF="$OUT/demo.gif"

REC_PID=""
cleanup() {
  # SIGINT, never SIGKILL: recordVideo finalises the container on interrupt and
  # leaves an unplayable file if it is killed outright.
  if [ -n "$REC_PID" ] && kill -0 "$REC_PID" 2>/dev/null; then
    kill -INT "$REC_PID" 2>/dev/null || true
    wait "$REC_PID" 2>/dev/null || true
  fi
  xcrun simctl status_bar "$UDID" clear >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "==> Simulator $UDID"
# Idempotent, about 70ms, and does not steal focus. `simctl boot` starts the
# runtime headlessly, so without this there is a running simulator and no window.
open -a Simulator

# A moving clock and a draining battery make two recordings of the same demo
# differ for no reason, and a stranger's carrier name is not worth publishing.
xcrun simctl status_bar "$UDID" override \
  --time "9:41" --batteryState charged --batteryLevel 100 \
  --cellularMode active --cellularBars 4 --wifiBars 3 \
  --operatorName "" >/dev/null 2>&1 || echo "  (status bar override unavailable, continuing)"

# An app that remembers the last run makes the demo a lie: Maps offers the
# previous destination in Recents, the agent taps it, and the search the demo
# exists to show never happens. Cleared before every recording for that reason.
#
# Two places, because an app's own container is not the whole story: Maps keeps
# its history in a device-wide store that survives clearing the container.
reset_app_state() {
  local bundle="$1" container dev_data
  [ -n "$bundle" ] || return 0
  dev_data="$HOME/Library/Developer/CoreSimulator/Devices/$UDID/data"

  container="$(xcrun simctl get_app_container "$UDID" "$bundle" data 2>/dev/null || true)"
  # Never delete a path that is not inside this simulator's own data directory.
  # An empty or unexpected container here would otherwise expand to rm -rf on
  # something else entirely.
  case "$container" in
    "$dev_data"/*) rm -rf "${container:?}/Library" "${container:?}/Documents" "${container:?}/tmp" ;;
  esac

  if [ "$bundle" = "com.apple.Maps" ] && [ -d "$dev_data" ]; then
    rm -rf "${dev_data:?}/Library/Maps" \
      "${dev_data:?}/Library/Caches/com.apple.Maps.Suggestions" \
      "${dev_data:?}/Library/Caches/com.apple.MapsIntelligence"
  fi
}

# A demo that opens on whatever the last run left behind is a demo of the last
# run. There is no `simctl home`, and pressing the button needs a WebDriverAgent
# session this script does not have, so the equivalent is terminating the apps
# that could be in front: killing the frontmost app drops you at the home
# screen. HOME_APPS is a list because simctl cannot say what is frontmost.
HOME_APPS="${HOME_APPS:-$APP com.apple.Preferences com.apple.Maps com.apple.mobilesafari}"
echo "==> Back to the home screen"
for app in $HOME_APPS; do
  # Terminating SpringBoard resprings the device, which is not "going home".
  [ -z "$app" ] || [ "$app" = "com.apple.springboard" ] && continue
  xcrun simctl terminate "$UDID" "$app" >/dev/null 2>&1 || true
done
if [ "$RESET_APP_STATE" = "1" ] && [ -n "$RESET_APPS" ]; then
  echo "==> Clearing state for:$(printf ' %s' $RESET_APPS)"
  for bundle in $RESET_APPS; do reset_app_state "$bundle"; done
fi

if [ -n "$WARMUP_URL" ]; then
  echo "==> Answering first-run dialogs so the demo does not have to"
  (cd "$ROOT" && uv run python - "$UDID" "$WARMUP_URL" "$WARMUP_DISMISS" <<'WARMUP') || {
import asyncio, sys
from ios_mcp.config import Settings
from ios_mcp.devices.pool import DevicePool
from ios_mcp.session import IosSession

udid, url, labels = sys.argv[1], sys.argv[2], [x.strip() for x in sys.argv[3].split(",")]

async def main() -> None:
    cfg = Settings(); cfg.stabilize.max_wait_s = 20.0
    pool = DevicePool(cfg)
    lease = await pool.acquire(udid)
    session = IosSession(lease, cfg)
    try:
        await session.open_url(url)
        for _ in range(5):
            screen = await session.observe()
            on_screen = {n.label for n in screen.nodes if n.label}
            hit = next((x for x in labels if x in on_screen), None)
            if hit is None:
                break
            print(f"    dismissed {hit!r}")
            await session.tap(target=hit)
    finally:
        await pool.release(udid)

asyncio.run(main())
WARMUP
    echo "  (warm-up failed; the dialogs may appear in the recording)"
  }
  for app in $HOME_APPS; do
    [ -z "$app" ] || [ "$app" = "com.apple.springboard" ] && continue
    xcrun simctl terminate "$UDID" "$app" >/dev/null 2>&1 || true
  done
fi

sleep 1   # let the closing animation finish before the first frame is captured

echo "==> Recording the framebuffer"
# The recorder's output goes to a log rather than /dev/null, and the next few
# lines check it actually started. Discarding it means a recorder that refused
# to start is not discovered until the file is missing at the end, which is
# after the whole run has been performed and paid for.
REC_LOG="$OUT/recordVideo.log"
xcrun simctl io "$UDID" recordVideo --codec h264 --force "$SIM_MP4" >"$REC_LOG" 2>&1 &
REC_PID=$!
sleep 2   # let the recorder attach before anything happens on screen

if ! kill -0 "$REC_PID" 2>/dev/null; then
  echo "error: the framebuffer recorder exited immediately." >&2
  sed 's/^/  /' "$REC_LOG" >&2
  if grep -q "already in progress" "$REC_LOG"; then
    echo >&2
    echo "  A previous recording was killed instead of interrupted, so" >&2
    echo "  CoreSimulator still believes one is running. Clear it with:" >&2
    echo "    killall Simulator && open -a Simulator" >&2
  fi
  exit 1
fi

# --- the run ----------------------------------------------------------------
# The command goes into a file rather than into asciinema's --command string.
# A goal is free text that will contain spaces and may contain quotes, and
# building a shell string around it means quoting it twice. printf %q works on
# the bash 3.2 that macOS still ships, where the obvious ${a[*]@Q} does not.
INNER="$OUT/run.sh"
{
  echo '#!/usr/bin/env bash'
  echo 'set -euo pipefail'
  printf 'cd %q\n' "$ROOT"
  printf 'exec uv run ios-agent run %q --device %q' "$GOAL" "$UDID"
  [ -n "$APP" ] && printf ' --app %q' "$APP"
  echo
} >"$INNER"
chmod +x "$INNER"

# asciinema 3 sets the recording size with --window-size COLSxROWS; 2 has no
# equivalent and records whatever the terminal happens to be. Recording at a
# fixed size matters here: the composite scales the terminal to a set height,
# so a stray window size changes how large the text ends up beside the phone.
REC_HELP="$(asciinema rec --help 2>&1 || true)"
REC_ARGS=(rec --overwrite --command "$INNER")
if printf '%s' "$REC_HELP" | grep -q -- "--window-size"; then
  REC_ARGS+=(--window-size "${COLS}x${ROWS}")
elif printf '%s' "$REC_HELP" | grep -q -- "--cols"; then
  REC_ARGS+=(--cols "$COLS" --rows "$ROWS")
else
  echo "  (this asciinema cannot set a size; recording at $(tput cols)x$(tput lines))"
fi

echo "==> Recording the terminal. Press ctrl+q when the run looks done."
set +e
if command -v timeout >/dev/null 2>&1; then
  timeout --foreground "$TIMEOUT" asciinema "${REC_ARGS[@]}" "$CAST"
else
  asciinema "${REC_ARGS[@]}" "$CAST"   # no coreutils: rely on ctrl+q
fi
set -e

echo "==> Stopping the framebuffer recording"
cleanup
REC_PID=""
trap - EXIT INT TERM

[ -s "$CAST" ] || { echo "error: no terminal recording at $CAST" >&2; exit 1; }
[ -s "$SIM_MP4" ] || { echo "error: no simulator recording at $SIM_MP4" >&2; exit 1; }

# --- render and composite ---------------------------------------------------
echo "==> Rendering the terminal"
agg --font-size "$FONT_SIZE" --theme "$THEME" --fps-cap 30 \
  --idle-time-limit "$IDLE_LIMIT" "$CAST" "$TUI_GIF"
ffmpeg -y -loglevel error -i "$TUI_GIF" \
  -movflags +faststart -pix_fmt yuv420p -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" "$TUI_MP4"

echo "==> Compositing"
# Both halves are scaled to one height and sped up by the same factor before
# they are stacked, so they stay in step. `shortest` ends the result when the
# first of the two runs out, which is normally the phone.
CLIP_ARGS=()
[ -n "$CLIP" ] && CLIP_ARGS=(-t "$CLIP")
ffmpeg -y -loglevel error "${CLIP_ARGS[@]}" -i "$TUI_MP4" "${CLIP_ARGS[@]}" -i "$SIM_MP4" -filter_complex "
  [0:v]trim=start=$START,setpts=PTS-STARTPTS,scale=-2:$HEIGHT,setpts=PTS/$SPEED,fps=30[tui];
  [1:v]trim=start=$PHONE_START,setpts=PTS-STARTPTS,scale=-2:$HEIGHT,setpts=PTS/$SPEED,fps=30,
       tpad=stop_mode=clone:stop_duration=$PHONE_HOLD[sim];
  [tui][sim]hstack=inputs=2:shortest=1,
    tpad=start_mode=clone:start_duration=$START_HOLD:stop_mode=clone:stop_duration=$END_HOLD[v]" \
  -map "[v]" -movflags +faststart -pix_fmt yuv420p -crf 20 "$FINAL_MP4"

if [ "$MAKE_GIF" = "1" ]; then
  echo "==> GIF (this is the one that plays inline on GitHub)"
  # A generated palette rather than the default 216-colour one: terminal text
  # against a dark background banding is the usual reason a demo GIF looks bad.
  ffmpeg -y -loglevel error -i "$FINAL_MP4" -vf "
    fps=$FPS,scale=$GIF_WIDTH:-2:flags=lanczos,split[a][b];
    [a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3" \
    "$FINAL_GIF"
fi

echo
echo "Wrote, in $OUT:"
echo "  $FINAL_MP4   $(du -h "$FINAL_MP4" | cut -f1)"
if [ "$MAKE_GIF" = "1" ]; then
  echo "  $FINAL_GIF   $(du -h "$FINAL_GIF" | cut -f1)"
  echo
  echo "To make this the README demo:"
  echo "  cp $FINAL_GIF $ROOT/docs/images/demo.gif"
  echo
  echo "That path is printed rather than assumed. \`$0 --latest\` prints it"
  echo "again later; nothing reconstructs it from a naming convention."
fi
echo
echo "GitHub plays a committed GIF inline; it does not play a committed mp4."
echo "If the GIF is more than a few MB, lower GIF_WIDTH or FPS and re-run the"
echo "last step rather than re-recording."
echo
echo "The video is $SPEED times real time. Say so in the caption: a run that"
echo "looks faster than it is undercuts every honest number in this repository."
