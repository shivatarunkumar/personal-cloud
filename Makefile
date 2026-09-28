# Personal Cloud commands.
#
#   make setup      everything, once: install, database, bucket, hosts entry, web build
#   make check-all  verify the tools, the database and Google Cloud
#   make api        run the API (development)      make web    run the web app (development)
#   make            list every command
#
# For everyday use the app runs in the background with ./cloud.sh (see README.md).
# Python packages go into a local .venv, so nothing is installed system-wide.

SHELL  := /bin/bash
PYTHON ?= python3
VENV   ?= .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
ENV    := source scripts/localenv.sh
ARGS   ?=

.DEFAULT_GOAL := help
.PHONY: help check install install-python install-web setup setup-gcp setup-hosts build-web \
        db-init db-reset db-status db-shell check-db check-gcp check-all \
        api web reset-password reindex relayout maintenance test lint format clean

help:  ## list every command
	@echo "Personal Cloud commands:"
	@grep -hE '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-15s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup
check:  ## check the tools this project needs are installed
	@missing=0; \
	for tool in $(PYTHON) node npm psql gcloud; do \
	  if command -v $$tool >/dev/null 2>&1; then \
	    printf "  ok      %-8s %s\n" "$$tool" "$$($$tool --version 2>&1 | head -1)"; \
	  else \
	    printf "  MISSING %-8s\n" "$$tool"; missing=1; \
	  fi; \
	done; \
	if [ $$missing -eq 1 ]; then \
	  echo; echo "Install what's missing:"; \
	  echo "  python3  → brew install python"; \
	  echo "  node/npm → brew install node"; \
	  echo "  psql     → brew install postgresql@17 && brew services start postgresql@17"; \
	  echo "  gcloud   → brew install --cask gcloud-cli"; \
	  exit 1; \
	fi

install: check install-python install-web  ## install all dependencies (backend + tools + web app)

install-python: | $(VENV)  ## Python packages into .venv
	@echo "==> installing Python packages into $(VENV)"
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r backend/requirements-dev.txt -r database/requirements.txt
	@echo "    $$($(PY) -V), $$($(PIP) list --format=freeze | wc -l | tr -d ' ') packages"

install-web:  ## web app npm packages
	@echo "==> installing npm packages in frontend/"
	@cd frontend && npm install --no-audit --no-fund --silent
	@echo "    node $$(node -v)"

$(VENV):
	@echo "==> creating virtualenv $(VENV)"
	@$(PYTHON) -m venv $(VENV)

setup: install db-init setup-gcp setup-hosts build-web  ## one-time setup (every step is safe to re-run)
	@echo "✔ set up. Next: ./cloud.sh start"

setup-gcp: | $(VENV)  ## make the bucket private and let browsers on your addresses upload to it
	@$(ENV) && $(PY) infra/scripts/setup_gcp.py

setup-hosts:  ## map WEB_BASE_URL's name to this machine in /etc/hosts (REMOVE=1 undoes it)
	@bash scripts/setup_hosts.sh $(if $(REMOVE),--remove,)

build-web:  ## build the web app for ./cloud.sh; re-run after pulling new code
	@$(ENV) && cd frontend && API_INTERNAL_URL=$${API_BASE_URL:-http://localhost:8002} npm run build

# ---------------------------------------------------------------- database
db-init: | $(VENV)  ## create the role, database and tables (idempotent)
	@$(ENV) && $(PY) database/scripts/setup_db.py $(ARGS)

db-reset: | $(VENV)  ## DROP the database and rebuild it (files in the bucket are kept)
	@$(ENV) && $(PY) database/scripts/setup_db.py --reset --yes $(ARGS)

db-status: | $(VENV)  ## list applied / pending migrations
	@$(ENV) && cd database/scripts && ../../$(PY) migrate.py status

db-shell:  ## open psql on the database
	@$(ENV) && psql "$$PSQL_URL"

# ---------------------------------------------------------------- checks
check-db: | $(VENV)  ## verify the database: connection, migrations, tables
	@$(ENV) && $(PY) database/scripts/check_db.py $(ARGS)

check-gcp: | $(VENV)  ## verify Google Cloud: credentials, bucket, privacy, CORS, write access
	@$(ENV) && $(PY) infra/scripts/check_gcp.py $(ARGS)

check-all: check check-db check-gcp  ## run every check

# ---------------------------------------------------------------- run (development)
api: | $(VENV)  ## run the API at http://localhost:8002, reloading on change
	@$(ENV) && cd backend && \
	  ../$(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port $${API_HOST_PORT:-8002}

web:  ## run the web app at http://localhost:3002, reloading on change
	@$(ENV) && cd frontend && \
	  API_INTERNAL_URL=$${API_BASE_URL:-http://localhost:8002} npm run dev -- --hostname 0.0.0.0 --port $${WEB_HOST_PORT:-3002}

# ---------------------------------------------------------------- accounts and data
reset-password: | $(VENV)  ## temporary password: make reset-password LOGIN=<email or username>
	@test -n "$(LOGIN)" || { echo "usage: make reset-password LOGIN=<email or username>"; exit 1; }
	@$(ENV) && cd backend && ../$(PY) scripts/reset_password.py "$(LOGIN)"

reindex: | $(VENV)  ## rebuild the file index from the bucket (ARGS=--dry-run / --usage-only)
	@$(ENV) && cd backend && ../$(PY) scripts/reindex.py $(ARGS)

relayout: | $(VENV)  ## move bucket objects to their readable paths (ARGS=--prune-missing)
	@$(ENV) && cd backend && ../$(PY) scripts/relayout.py $(ARGS)

maintenance: | $(VENV)  ## empty old trash and forget abandoned uploads (cloud.sh runs this)
	@$(ENV) && cd backend && ../$(PY) scripts/maintenance.py

# ---------------------------------------------------------------- quality
test: | $(VENV)  ## backend tests and the web app type check
	@$(ENV) && cd backend && ../$(VENV)/bin/pytest -q $(ARGS)
	@cd frontend && npm run typecheck

lint: | $(VENV)  ## ruff lint + format check
	@$(VENV)/bin/ruff check backend database infra && $(VENV)/bin/ruff format --check backend database infra

format: | $(VENV)  ## apply ruff fixes and formatting
	@$(VENV)/bin/ruff check --fix backend database infra && $(VENV)/bin/ruff format backend database infra

clean:  ## remove .venv, node_modules and build output
	rm -rf $(VENV) frontend/node_modules frontend/.next
