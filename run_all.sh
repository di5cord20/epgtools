#!/usr/bin/env bash
set -e

cd /app

echo "=== [$(date)] Starting Scheduled EPG Updates ==="

# Automatically run epg_merge.py for every .txt file in the config/ directory
for config_file in config/*.txt; do
    [ -e "$config_file" ] || continue
    echo "[$(date)] Processing $config_file..."
    python3 -u epg_merge.py -i "$config_file" -d
done

echo "=== [$(date)] Scheduled EPG Update Complete ==="
