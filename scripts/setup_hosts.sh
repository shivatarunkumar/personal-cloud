#!/usr/bin/env bash
# Map the hostname in WEB_BASE_URL to this machine in /etc/hosts.
#
#   scripts/setup_hosts.sh            add "127.0.0.1 <hostname>" if it isn't there
#   scripts/setup_hosts.sh --remove   take that line out again
#
# Idempotent. Asks for your password (sudo) only when /etc/hosts actually has to change.
# Nothing to do when WEB_BASE_URL is localhost or an IP address.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOSTS=/etc/hosts
MARKER="# personal-cloud"

# shellcheck disable=SC1091
source "$ROOT/scripts/localenv.sh"

url="${WEB_BASE_URL:-}"
host="${url#*://}"
host="${host%%/*}"
host="${host%%:*}"
host="$(echo "$host" | tr '[:upper:]' '[:lower:]')"

if [ -z "$host" ] || [ "$host" = "localhost" ] || [[ "$host" =~ ^[0-9.]+$ ]]; then
  echo "[hosts] WEB_BASE_URL=$url needs no hosts entry"
  exit 0
fi

if [[ ! "$host" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
  echo "[hosts] '$host' (from WEB_BASE_URL) is not a valid hostname" >&2
  exit 1
fi

# lines that map this exact name, whatever else is on them
has_entry() { grep -Eq "^[^#]*[[:space:]]${host//./\\.}([[:space:]]|$)" "$HOSTS"; }

if [ "${1:-}" = "--remove" ]; then
  if ! grep -q "[[:space:]]${host}[[:space:]]*${MARKER}\$" "$HOSTS"; then
    echo "[hosts] no line added by setup for $host; nothing to remove"
    exit 0
  fi
  echo "[hosts] removing $host from $HOSTS (sudo may ask for your password)"
  tmp="$(mktemp)"
  grep -v "[[:space:]]${host}[[:space:]]*${MARKER}\$" "$HOSTS" >"$tmp"
  sudo cp "$tmp" "$HOSTS"
  rm -f "$tmp"
  echo "[hosts] removed"
  exit 0
fi

if has_entry; then
  echo "[hosts] ok: $host is already in $HOSTS"
else
  echo "[hosts] adding '127.0.0.1 $host' to $HOSTS (sudo may ask for your password)"
  printf '127.0.0.1\t%s\t%s\n' "$host" "$MARKER" | sudo tee -a "$HOSTS" >/dev/null
fi

# macOS caches lookups; flush so the new name works straight away
if command -v dscacheutil >/dev/null 2>&1; then
  dscacheutil -flushcache 2>/dev/null || true
  resolved="$(dscacheutil -q host -a name "$host" 2>/dev/null | awk '/ip_address/ {print $2; exit}')"
else
  resolved="$(getent hosts "$host" 2>/dev/null | awk '{print $1; exit}')"
fi

if [ "$resolved" = "127.0.0.1" ]; then
  echo "[hosts] ok: $host → 127.0.0.1. Open $url"
else
  echo "[hosts] warning: $host resolves to '${resolved:-nothing}', not 127.0.0.1." >&2
  echo "        check $HOSTS for another line with that name" >&2
fi
