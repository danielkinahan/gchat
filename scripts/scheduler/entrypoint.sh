#!/bin/sh
set -eu

EXPORT_CRON="${DISCORD_EXPORT_CRON:-0 2 * * 0}"

cat > /tmp/crontab <<EOF
# Export Discord data
$EXPORT_CRON sh /app/scripts/discord-exporter-scheduler/run-export.sh
EOF

echo "discord-exporter-scheduler: export cron: $EXPORT_CRON"
exec /usr/local/bin/supercronic -passthrough-logs /tmp/crontab
