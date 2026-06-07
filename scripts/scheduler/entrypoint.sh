#!/bin/sh
set -eu

DB_CRON="${DB_REBUILD_CRON:-0 3 */14 * *}"
EXPORT_CRON="${EXPORT_CRON:-0 2 * * 0}"
OFFLINE_MODE="${SCHEDULER_OFFLINE:-false}"

echo "scheduler: running initial full export + database rebuild"
sh /app/scripts/scheduler/rebuild-db.sh

cat > /tmp/crontab <<EOF
# Full refresh + rebuild
$DB_CRON sh /app/scripts/scheduler/rebuild-db.sh
EOF

if [ "$OFFLINE_MODE" = "true" ]; then
    echo "scheduler: offline mode enabled; Export cron disabled"
else
    cat >> /tmp/crontab <<EOF

# Extra full refresh + rebuild (typically for Discord cadence)
$EXPORT_CRON sh /app/scripts/scheduler/rebuild-db.sh
EOF
fi

exec /usr/sbin/supercronic -passthrough-logs /tmp/crontab
