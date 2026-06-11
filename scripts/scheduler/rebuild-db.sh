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

API_HOST="${API_HOST:-api}"
API_PORT="${API_PORT:-8000}"
echo "scheduler: asking API at ${API_HOST}:${API_PORT} to restart so it picks up the new DB"
# The API restart endpoint exits the process; the container manager then
# brings it back up against the freshly built database. The web gateway
# blocks this endpoint externally, so this is only callable inside the
# docker network. We tolerate connection errors during the brief window
# while the process is exiting.
curl --silent --show-error --max-time 5 -X POST "http://${API_HOST}:${API_PORT}/api/restart" || true

echo "scheduler: rebuild complete"
