#!/bin/sh
set -eu

SIGNAL_DIR="${1:-/data/signal}"
ENCRYPTED_DB="$SIGNAL_DIR/sql/db.sqlite"
SIGNAL_CONFIG="$SIGNAL_DIR/config.json"
DECRYPTED_DIR="/data/signal_decrypted"
TEMP_DB="/tmp/signal-db-copy-$$.sqlite"

# Exit early if no encrypted db exists
if [ ! -f "$ENCRYPTED_DB" ]; then
    echo "decrypt-signal: no encrypted database at $ENCRYPTED_DB; skipping"
    exit 0
fi

# Read encryption key from Signal config
if [ ! -f "$SIGNAL_CONFIG" ]; then
    echo "decrypt-signal: no Signal config at $SIGNAL_CONFIG; cannot decrypt" >&2
    exit 1
fi

KEY=$(grep -o '"key"[[:space:]]*:[[:space:]]*"[^"]*"' "$SIGNAL_CONFIG" | cut -d'"' -f4)

if [ -z "$KEY" ]; then
    echo "decrypt-signal: encryption key not found in config" >&2
    exit 1
fi

# Copy encrypted db to temp location (avoids file locks if Signal is running)
echo "decrypt-signal: copying encrypted database to temp location"
cp "$ENCRYPTED_DB" "$TEMP_DB"

# Clean up temp copy on exit
trap 'rm -f "$TEMP_DB"' EXIT

# Remove old decrypted backup if present
if [ -d "$DECRYPTED_DIR" ]; then
    echo "decrypt-signal: removing old decrypted backup"
    rm -rf "$DECRYPTED_DIR"
fi

mkdir -p "$DECRYPTED_DIR"

echo "decrypt-signal: decrypting Signal backup (including all attachments)"
signalbackup-tools \
    --input "$TEMP_DB" \
    --passphrase "$KEY" \
    --output "$DECRYPTED_DIR" \
    --no-showprogress \
    2>&1 || {
    echo "decrypt-signal: decryption failed" >&2
    rm -rf "$DECRYPTED_DIR"
    exit 1
}

echo "decrypt-signal: decryption complete at $DECRYPTED_DIR"
