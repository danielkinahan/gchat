#!/bin/sh
set -eu

if [ -z "${BASIC_AUTH_USERNAME:-}" ]; then
    BASIC_AUTH_USERNAME="gargboy"
fi

if [ -z "${BASIC_AUTH_PASSWORD:-}" ]; then
    echo "BASIC_AUTH_PASSWORD is required" >&2
    exit 1
fi

htpasswd -bc /etc/nginx/.htpasswd "$BASIC_AUTH_USERNAME" "$BASIC_AUTH_PASSWORD" >/dev/null
exec nginx -g "daemon off;"
