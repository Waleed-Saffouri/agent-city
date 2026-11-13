#!/bin/bash
# Makes Agent City start by itself whenever you log in to your Mac, restart it if it ever stops,
# and pick up new versions from GitHub automatically. Run it once:
#     ./install-autostart.sh            install
#     ./install-autostart.sh remove     uninstall
# The city is then always at http://localhost:8000 (bookmark it).
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="com.waleed.agentcity"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Auto-start removed. Use ./start.sh to run Agent City by hand."
  exit 0
fi

if [ ! -d .venv ]; then
  echo "Setting up (about a minute)..."
  python3 -m venv .venv
fi
echo "Installing/updating what Agent City needs..."
./.venv/bin/pip install -q -r requirements.txt
./.venv/bin/python -m playwright install chromium >/dev/null 2>&1 || true   # browser for the Applier
git pull -q --ff-only 2>/dev/null || true
mkdir -p data "$HOME/Library/LaunchAgents"
pkill -f "uvicorn server:app" 2>/dev/null || true      # stop a copy started with ./start.sh
pkill -f "run.py schedule" 2>/dev/null || true

# PATH includes Homebrew and the usual places, so the agents can find claude, gitleaks and git.
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>$DIR/run.py</string><string>serve</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.claude/local:$HOME/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/data/agentcity.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/agentcity.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 3
open http://localhost:8000 2>/dev/null || true
echo "Done. Agent City now runs by itself (even after a restart) at http://localhost:8000"
echo "It updates itself from GitHub. To turn this off: ./install-autostart.sh remove"
