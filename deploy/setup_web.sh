#!/usr/bin/env bash
# Run this ON web01 and ON web02 (not on lb01) to install or update the app.
# First time: create .env in the repo folder with your keys before running.
# Usage: bash deploy/setup_web.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "No .env file in $APP_DIR. Copy .env.example to .env and add your keys first."
  exit 1
fi

# Use sudo only when we are not root and sudo exists. Minimized server
# images often log you in as root and do not ship sudo at all.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo > /dev/null 2>&1; then
    SUDO="sudo"
  else
    SUDO="none"
  fi
fi

echo "Pulling latest code"
git pull --ff-only

PORT="$(grep -E '^PORT=' .env | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8000}"

if [ -d /run/systemd/system ] && [ "$SUDO" != "none" ]; then
  echo "Installing systemd service"
  sed -e "s#/home/ubuntu/job_search_project_apis#$APP_DIR#g" \
      -e "s#^User=.*#User=$(whoami)#" \
      deploy/jobs.service | $SUDO tee /etc/systemd/system/jobs.service > /dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable jobs > /dev/null
  $SUDO systemctl restart jobs
  echo "Running as systemd service 'jobs'. Logs: journalctl -u jobs -f"
else
  echo "systemd not available here, starting in the background with nohup instead"
  pkill -f "python3 $APP_DIR/server.py" 2> /dev/null || true
  sleep 1
  nohup python3 "$APP_DIR/server.py" > "$APP_DIR/server.log" 2>&1 &
  echo "Started with PID $!. Logs: tail -f $APP_DIR/server.log"
  echo "Note: rerun this script after a server reboot to start the app again."
fi

sleep 2
echo "Health check"
curl -s -i "http://localhost:$PORT/api/health" | grep -iE '^(HTTP|x-served-by)|status' \
  || { echo "Health check failed. Last log lines:"; tail -n 20 "$APP_DIR/server.log" 2> /dev/null || true; exit 1; }
