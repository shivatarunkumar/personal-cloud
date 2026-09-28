#!/usr/bin/env bash
# Source this to load .env for commands running on this machine:
#   source scripts/localenv.sh
# Every make target and cloud.sh does it for you.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "localenv.sh needs bash: run 'bash -c \"source scripts/localenv.sh && ...\"' or use the make targets" >&2
  return 1 2>/dev/null || exit 1
fi

_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$_root/.env" ]; then
  cat >&2 <<'EOF'
No .env file yet. On a new machine:

  cp .env.example .env

then open .env and fill in the values marked REQUIRED:
  DATABASE_URL        choose the app's database password
  POSTGRES_ADMIN_URL  a superuser on your Postgres (Homebrew: your macOS username)
  JWT_SECRET          openssl rand -hex 32
  GCP_PROJECT_ID      the project that owns your bucket
  GCS_BUCKET          your bucket's name

See step 4 of README.md.
EOF
  return 1 2>/dev/null || exit 1
fi

set -a
# shellcheck disable=SC1091
. "$_root/.env"
set +a
unset _root

# An empty GOOGLE_APPLICATION_CREDENTIALS would make the Google libraries look for a file
# called "", so drop it and let them fall back to the gcloud login.
[ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && unset GOOGLE_APPLICATION_CREDENTIALS

# psql and other libpq tools don't understand SQLAlchemy driver prefixes
# (postgresql+asyncpg://); PSQL_URL is the same connection without them.
export PSQL_URL="${DATABASE_URL/+asyncpg/}"
