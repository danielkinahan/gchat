#!/bin/sh
set -eu

LOCK_DIR="/tmp/gchat-rebuild-lock"
DATA_DIR="${DATA_DIR:-/data}"
CONFIG_DIR="${CONFIG_DIR:-/config}"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "scheduler: rebuild already in progress; skipping this run"
    exit 0
fi

trap 'rmdir "$LOCK_DIR"' EXIT

echo "scheduler: refreshing Signal export"
sh /app/scripts/scheduler/export-signal.sh

echo "scheduler: refreshing Discord export"
sh /app/scripts/scheduler/export-discord.sh

echo "scheduler: rebuilding DuckDB"
python -m gchat build --data-dir "$DATA_DIR" --output "$DATA_DIR/gchat-db/gchat.duckdb" --config-dir "$CONFIG_DIR"

echo "scheduler: rebuild complete"
