#!/bin/sh
set -eu

LOCK_DIR="/tmp/gchat-rebuild-lock"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "scheduler: rebuild already in progress; skipping this run"
    exit 0
fi

trap 'rmdir "$LOCK_DIR"' EXIT

echo "scheduler: decrypting Signal backup if needed"
sh /app/scripts/scheduler/decrypt-signal.sh /data/signal || true

echo "scheduler: rebuilding DuckDB"
gchat-build --data-dir /data --output /db/gchat.duckdb --config-dir /config
echo "scheduler: rebuild complete"

