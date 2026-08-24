#!/usr/bin/env bash
# Start the go-ios RemoteXPC tunnel daemon required by iOS 17+ physical devices.
#
# iOS 17 moved device communication from TCP/lockdown to QUIC + RemoteXPC, so a
# tunnel must exist before WebDriverAgent can be reached. The daemon needs root
# to create the interface. Start it once and leave it running; the server talks
# to its HTTP control API on 127.0.0.1:28100 rather than spawning per session.
set -euo pipefail

command -v ios >/dev/null 2>&1 || {
  echo "error: go-ios not found. Install with: brew install go-ios" >&2
  exit 1
}

if curl -fsS --max-time 2 http://127.0.0.1:28100/tunnel/list >/dev/null 2>&1; then
  echo "Tunnel daemon already running on 127.0.0.1:28100"
  ios tunnel ls || true
  exit 0
fi

echo "==> Starting tunnel daemon (requires sudo)"
sudo ios tunnel start

# To run this at login instead, install a launchd agent:
#
#   sudo tee /Library/LaunchDaemons/com.ios-mcp.tunnel.plist >/dev/null <<'PLIST'
#   <?xml version="1.0" encoding="UTF-8"?>
#   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
#     "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
#   <plist version="1.0"><dict>
#     <key>Label</key><string>com.ios-mcp.tunnel</string>
#     <key>ProgramArguments</key>
#       <array><string>/opt/homebrew/bin/ios</string><string>tunnel</string><string>start</string></array>
#     <key>RunAtLoad</key><true/>
#     <key>KeepAlive</key><true/>
#   </dict></plist>
#   PLIST
#   sudo launchctl load /Library/LaunchDaemons/com.ios-mcp.tunnel.plist
