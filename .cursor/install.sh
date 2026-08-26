#!/usr/bin/env bash
# Idempotent Cloud Agent environment bootstrap for the Trading Bot.
#
# Sets up a local, provider-less development instance:
#   - PostgreSQL 16 + Redis (system packages)
#   - Python venv with backend + test dependencies
#   - Frontend (Vite) node dependencies
#   - A dev .env, database role/db, and alembic migrations
#
# This mirrors the docker-compose stack but runs services natively because
# the Cloud Agent VM does not provide Docker. It must be safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DB_NAME="trading_bot"
DB_USER="trading_bot"
DB_PASS="trading_bot_dev"

echo "==> [1/7] Installing system packages (postgres, redis, build deps)"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -o Acquire::Retries=5
sudo apt-get install -y -o Acquire::Retries=5 \
  postgresql postgresql-contrib redis-server \
  libmagic1 libpq-dev build-essential \
  python3-venv python3-dev

echo "==> [2/7] Starting PostgreSQL and Redis"
sudo pg_ctlcluster "$(pg_lsclusters -h | awk 'NR==1{print $1}')" main start 2>/dev/null || sudo service postgresql start || true
sudo service redis-server start 2>/dev/null || sudo redis-server --daemonize yes || true

# Wait for postgres to accept connections.
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q; then break; fi
  sleep 1
done

echo "==> [3/7] Creating database role and database (idempotent)"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
ALTER ROLE ${DB_USER} CREATEDB;
SQL
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
  || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"

echo "==> [4/7] Creating .env (dev defaults) if missing"
if [ ! -f .env ]; then
  cp .cursor/dev.env.example .env
  echo "   Wrote .env from .cursor/dev.env.example"
else
  echo "   .env already exists; leaving it untouched"
fi

echo "==> [5/7] Python virtualenv and dependencies"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-test.txt

echo "==> [6/7] Running database migrations"
set -a
# shellcheck disable=SC1091
. ./.env
set +a
python manage.py
python scripts/align_trade_number_sequence.py || true

echo "==> [7/7] Installing frontend dependencies"
if [ -d frontend ]; then
  ( cd frontend && npm install )
fi

echo "==> Environment setup complete."
