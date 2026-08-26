#!/usr/bin/env bash
# Per-boot service startup for the Trading Bot dev environment.
#
# Long-running application processes (backend API, frontend dev server) are
# defined as `terminals` in .cursor/environment.json. This script only brings
# up the stateful system services they depend on, and must be idempotent.
set -euo pipefail

echo "==> Starting PostgreSQL"
sudo pg_ctlcluster "$(pg_lsclusters -h | awk 'NR==1{print $1}')" main start 2>/dev/null \
  || sudo service postgresql start || true

echo "==> Starting Redis"
sudo service redis-server start 2>/dev/null || sudo redis-server --daemonize yes || true

# Wait until PostgreSQL is ready so dependent terminals start cleanly.
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q; then
    echo "==> PostgreSQL is ready"
    break
  fi
  sleep 1
done

echo "==> Services started."
