#!/bin/sh
set -eu

DATA_DIR="${DATA_DIR:-/data}"
OUTPUT_DIR="${DATA_DIR}/discord"
TMP_OUTPUT_DIR="${OUTPUT_DIR}.tmp"
MEDIA_DIR="${DATA_DIR}/discord-media"
OFFLINE_MODE="${SCHEDULER_OFFLINE:-0}"

case "$OFFLINE_MODE" in
    1|true|TRUE|yes|YES)
        echo "discord-export: offline mode enabled; skipping"
        exit 0
        ;;
esac

if [ -z "${DISCORD_EXPORT_COMMAND:-}" ] && [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
    echo "discord-export: no DISCORD_EXPORT_COMMAND or DISCORD_BOT_TOKEN configured; skipping"
    exit 0
fi

rm -rf "$TMP_OUTPUT_DIR"
mkdir -p "$TMP_OUTPUT_DIR"
mkdir -p "$MEDIA_DIR"
export DISCORD_EXPORT_DIR="$TMP_OUTPUT_DIR"
export DISCORD_MEDIA_DIR="$MEDIA_DIR"

echo "discord-export: exporting Discord HTML to $TMP_OUTPUT_DIR (reusing media in $MEDIA_DIR)"
if [ -n "${DISCORD_EXPORT_COMMAND:-}" ]; then
    if ! sh -c "$DISCORD_EXPORT_COMMAND"; then
        echo "discord-export: custom export command failed" >&2
        rm -rf "$TMP_OUTPUT_DIR"
        exit 1
    fi
else
    if ! discord-chat-exporter-cli exportall \
        -t "$DISCORD_BOT_TOKEN" \
        --include-dm false \
        -f HtmlDark \
        -o "$TMP_OUTPUT_DIR/%g/%c.html" \
        --media \
        --media-dir "$MEDIA_DIR" \
        --reuse-media
    then
        echo "discord-export: default HTML export failed" >&2
        rm -rf "$TMP_OUTPUT_DIR"
        exit 1
    fi
fi

rm -rf "$OUTPUT_DIR"
mv "$TMP_OUTPUT_DIR" "$OUTPUT_DIR"

echo "discord-export: export complete at $OUTPUT_DIR (media kept at $MEDIA_DIR)"
