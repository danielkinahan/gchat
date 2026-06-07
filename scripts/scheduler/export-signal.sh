#!/bin/sh
set -eu

DATA_DIR="${DATA_DIR:-/data}"
SIGNAL_DIR="${DATA_DIR}/signal"
FINAL_DIR="$DATA_DIR/signal_decrypted"
TMP_DIR="${FINAL_DIR}.tmp"

if [ ! -d "$SIGNAL_DIR" ]; then
    echo "decrypt-signal: no Signal directory at $SIGNAL_DIR; skipping"
    exit 1
fi

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

echo "decrypt-signal: exporting decrypted desktop HTML from $SIGNAL_DIR"
if ! signalbackup-tools \
    --exportdesktophtml "$TMP_DIR" \
    --desktopdir "$SIGNAL_DIR" \
    --ignorewal \
    --no-showprogress
then
    echo "decrypt-signal: desktop HTML export failed" >&2
    rm -rf "$TMP_DIR"
    exit 1
fi

rm -rf "$FINAL_DIR"
mv "$TMP_DIR" "$FINAL_DIR"

echo "decrypt-signal: desktop HTML export complete at $FINAL_DIR"
