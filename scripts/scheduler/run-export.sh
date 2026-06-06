#!/bin/sh
set -eu

if [ -z "${DISCORD_EXPORT_COMMAND:-}" ]; then
    echo "discord-exporter-scheduler: DISCORD_EXPORT_COMMAND is empty; skipping export job"
    exit 0
fi

echo "discord-exporter-scheduler: running export command"
sh -lc "$DISCORD_EXPORT_COMMAND"
echo "discord-exporter-scheduler: export command finished"
