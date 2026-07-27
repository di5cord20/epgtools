#!/usr/bin/env bash
set -e

# Start background cron service for automated nightly builds
cron

echo "[INFO] EPG Cron daemon started in background."
echo "[INFO] Starting EPG Web UI at http://0.0.0.0:7860"

# Start the Web UI in foreground to keep container running
exec python3 -u epg_gui.py
