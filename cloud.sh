#!/usr/bin/env bash
# Personal Cloud: keep the API and the web app running on this machine.
#
#   ./cloud.sh start          check everything, then start whatever isn't running
#   ./cloud.sh stop           stop both
#   ./cloud.sh restart        stop, then start
#   ./cloud.sh status         what is running, where, and when cron last ran
#   ./cloud.sh logs           follow the logs (Ctrl+C to leave)
#   ./cloud.sh cron-install   run `start` at every boot and every 10 minutes
#   ./cloud.sh cron-remove    take those cron lines out again
#
# `start` only checks and starts: it never installs, builds or creates tables (that is
# `make setup`), and it is safe to run as often as you like. Anything already running is
# left alone, so the every-10-minutes cron run is a watchdog that brings back whatever
# stopped. Under cron it also runs the housekeeping (old trash, abandoned uploads).
#
# Everything goes to logs/: api.log, web.log, and cloud.log (what this script did, and
# why it refused to start if a check failed).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS="$ROOT/logs"
SELF="$ROOT/cloud.sh"
VENV="$ROOT/.venv"
LOG_MAX_BYTES=$((10 * 1024 * 1024))
CRON_TAG="# personal-cloud watchdog"

# cron starts with an almost empty PATH; add where Homebrew puts python, node and psql
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@17/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

mkdir -p "$LOGS"

# ------------------------------------------------------------------ output
say() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  echo "$line" >>"$LOGS/cloud.log"
  [ -t 1 ] && echo "$*"
  return 0
}

fail() {
  say "NOT STARTED: $1"
  [ -n "${2:-}" ] && say "  fix: $2"
  exit 1
}

# ------------------------------------------------------------------ settings
load_env() {
  [ -f "$ROOT/.env" ] || fail "there is no .env file in $ROOT" "cp .env.example .env, then fill it in (README step 4)"
  # shellcheck disable=SC1091
  source "$ROOT/scripts/localenv.sh" >/dev/null 2>&1 || fail "could not read .env" "check it for a line that is not KEY=value"
  API_PORT="${API_HOST_PORT:-8002}"
  WEB_PORT="${WEB_HOST_PORT:-3002}"
  WEB_URL="${WEB_BASE_URL:-http://localhost:$WEB_PORT}"
}

# ------------------------------------------------------------------ processes
pid_of() { cat "$LOGS/$1.pid" 2>/dev/null; }

# Running, and really ours: after a reboot the old pid in a .pid file can belong to some
# unrelated program, which must not count as "already running".
alive() {
  local pid command
  pid="$(pid_of "$1")"
  [ -n "$pid" ] || return 1
  command="$(ps -p "$pid" -o command= 2>/dev/null)" || return 1
  case "$1" in
    api) [[ "$command" == *"app.main:app"* ]] ;;
    web) [[ "$command" == *next* ]] ;;
    *) return 1 ;;
  esac
}

api_healthy() { curl -fsS -m 5 "http://127.0.0.1:$API_PORT/healthz" >/dev/null 2>&1; }
web_healthy() { curl -sS -m 10 -o /dev/null "http://127.0.0.1:$WEB_PORT/login" 2>/dev/null; }

port_owner() { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1 " (pid " $2 ")"}'; }

# Keep logs from filling the disk: copy then truncate, because the app keeps the file open.
rotate() {
  local file="$LOGS/$1"
  [ -f "$file" ] || return 0
  if [ "$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
    cp "$file" "$file.1" && : >"$file"
    say "rotated $1 (older lines are in $1.1)"
  fi
}

stop_one() {
  local name="$1" pid
  pid="$(pid_of "$name")"
  if [ -n "$pid" ] && alive "$name"; then
    pkill -TERM -P "$pid" 2>/dev/null
    kill -TERM "$pid" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      pkill -KILL -P "$pid" 2>/dev/null
      kill -KILL "$pid" 2>/dev/null
    fi
    say "stopped $name (pid $pid)"
  fi
  rm -f "$LOGS/$name.pid"
}

# ------------------------------------------------------------------ checks
build_is_stale() {
  [ -n "$(find "$ROOT/frontend/app" "$ROOT/frontend/components" "$ROOT/frontend/lib" \
    -newer "$ROOT/frontend/.next/BUILD_ID" -type f 2>/dev/null | head -1)" ]
}

preflight() {
  [ -x "$VENV/bin/uvicorn" ] || fail "the Python packages are not installed" "make install"
  [ -d "$ROOT/frontend/node_modules" ] || fail "the web app's packages are not installed" "make install"
  [ -f "$ROOT/frontend/.next/BUILD_ID" ] || fail "the web app has not been built" "make build-web"
  if build_is_stale; then
    say "warning: the web app changed since it was built; run 'make build-web' then './cloud.sh restart'"
  fi
  [ -n "${JWT_SECRET:-}" ] || fail "JWT_SECRET is empty in .env" "set it to the output of: openssl rand -hex 32"
  [ -n "${GCS_BUCKET:-}" ] || fail "GCS_BUCKET is empty in .env" "set it to your bucket's name"
  if [ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] &&
    [ ! -f "$HOME/.config/gcloud/application_default_credentials.json" ]; then
    fail "no Google credentials on this machine" "gcloud auth application-default login"
  fi
  if ! "$VENV/bin/python" "$ROOT/database/scripts/check_db.py" >/dev/null 2>&1; then
    fail "the database check failed" "run 'make check-db' to see why"
  fi
}

# ------------------------------------------------------------------ start / stop
start_api() {
  if alive api; then return 0; fi
  local owner
  owner="$(port_owner "$API_PORT")"
  [ -z "$owner" ] || fail "port $API_PORT (API_HOST_PORT) is taken by $owner" "stop that program or change API_HOST_PORT in .env"
  rotate api.log
  (
    cd "$ROOT/backend" || exit 1
    # started directly (not through `a && b &`), so $! is the server itself
    nohup "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$API_PORT" --proxy-headers \
      >>"$LOGS/api.log" 2>&1 &
    echo $! >"$LOGS/api.pid"
  )
  say "started API on port $API_PORT (pid $(pid_of api))"
}

start_web() {
  if alive web; then return 0; fi
  local owner
  owner="$(port_owner "$WEB_PORT")"
  [ -z "$owner" ] || fail "port $WEB_PORT (WEB_HOST_PORT) is taken by $owner" "stop that program or change WEB_HOST_PORT in .env"
  rotate web.log
  (
    cd "$ROOT/frontend" || exit 1
    export API_INTERNAL_URL="${API_BASE_URL:-http://localhost:$API_PORT}"
    nohup ./node_modules/.bin/next start --hostname 0.0.0.0 --port "$WEB_PORT" \
      >>"$LOGS/web.log" 2>&1 &
    echo $! >"$LOGS/web.pid"
  )
  say "started web app on port $WEB_PORT (pid $(pid_of web))"
}

wait_healthy() {
  for _ in $(seq 1 30); do
    if api_healthy && web_healthy; then return 0; fi
    sleep 1
  done
  return 1
}

cmd_start() {
  load_env
  rotate cloud.log
  if alive api && alive web; then
    if [ -t 1 ]; then echo "already running at $WEB_URL"; fi
  else
    preflight
    start_api
    start_web
    if wait_healthy; then
      say "running at $WEB_URL"
    else
      say "started, but not answering yet: see ./cloud.sh logs"
    fi
  fi
  # housekeeping only on the cron runs, so a manual start stays quick
  if [ ! -t 1 ]; then
    (cd "$ROOT/backend" && "$VENV/bin/python" scripts/maintenance.py >>"$LOGS/cloud.log" 2>&1) ||
      say "housekeeping failed (see above)"
  fi
  date '+%Y-%m-%d %H:%M:%S' >"$LOGS/last-run"
}

cmd_stop() {
  load_env
  stop_one web
  stop_one api
  if [ -t 1 ]; then echo "stopped"; fi
}

cmd_status() {
  load_env
  for name in api web; do
    if alive "$name"; then
      printf "  %-4s running  (pid %s)\n" "$name" "$(pid_of "$name")"
    else
      printf "  %-4s stopped\n" "$name"
    fi
  done
  if api_healthy; then
    local health
    health="$(curl -sS -m 10 "http://127.0.0.1:$API_PORT/api/v1/health" 2>/dev/null)"
    if [[ "$health" == *'"status":"ok"'* ]]; then
      echo "  health  ok (database and bucket reachable)"
    else
      echo "  health  PROBLEM: $health"
    fi
  fi
  echo "  address $WEB_URL"
  if crontab -l 2>/dev/null | grep -q "$CRON_TAG"; then
    echo "  cron    installed; last run $(cat "$LOGS/last-run" 2>/dev/null || echo never)"
  else
    echo "  cron    not installed (./cloud.sh cron-install)"
  fi
}

cmd_logs() {
  touch "$LOGS/api.log" "$LOGS/web.log" "$LOGS/cloud.log"
  tail -n 50 -F "$LOGS/api.log" "$LOGS/web.log" "$LOGS/cloud.log"
}

cmd_cron_install() {
  local current
  current="$(crontab -l 2>/dev/null | grep -v "$CRON_TAG")"
  {
    [ -n "$current" ] && echo "$current"
    echo "@reboot sleep 60 && \"$SELF\" start $CRON_TAG"
    echo "*/10 * * * * \"$SELF\" start $CRON_TAG"
  } | crontab -
  echo "cron installed: starts at boot and checks every 10 minutes"
  echo "(macOS may ask to allow 'cron' Full Disk Access if your folder is in Documents; see README)"
}

cmd_cron_remove() {
  crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
  echo "cron lines removed"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop && cmd_start ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  cron-install) cmd_cron_install ;;
  cron-remove) cmd_cron_remove ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
