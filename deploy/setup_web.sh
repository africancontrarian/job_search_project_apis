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

echo "Pulling latest code"
git pull --ff-only

echo "Installing systemd service"
sed -e "s#/home/ubuntu/job_search_project_apis#$APP_DIR#g" \
    -e "s#^User=.*#User=$(whoami)#" \
    deploy/jobs.service | sudo tee /etc/systemd/system/jobs.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable jobs > /dev/null
sudo systemctl restart jobs
sleep 2

echo "Health check"
curl -s -i http://localhost:8000/api/health | grep -iE '^(HTTP|x-served-by)|status'
