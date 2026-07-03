#!/usr/bin/env bash
# deploy.sh — pull latest from GitHub and rebuild nms_web
# Usage: bash /home/nmsserver/nms-clean/deploy.sh
set -e

REPO=/home/nmsserver/nms-clean
COMPOSE="$REPO/docker-compose.web_main.yml"
LOCAL_REPO=/home/nmsserver/nms/nms_final/device_monitoring_tactical

echo "[deploy] Pulling latest from GitHub..."
git -C "$REPO" pull

# requirements.server.txt is gitignored (*.txt rule) — always sync from local source
# so pinned versions (waitress, PyMuPDF, etc.) are never lost after a git pull.
echo "[deploy] Syncing requirements.server.txt from local source..."
cp "$LOCAL_REPO/requirements.server.txt" "$REPO/requirements.server.txt"

# file_transfer/ must exist for the Dockerfile COPY to succeed
mkdir -p "$REPO/file_transfer"

echo "[deploy] Building and restarting nms_web..."
sudo docker compose -f "$COMPOSE" up -d --build

echo "[deploy] Done. Container status:"
sudo docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "nms_web|NAME"
