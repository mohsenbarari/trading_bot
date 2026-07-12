#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

coverage_args=()
if [[ "${1:-}" == "--coverage" ]]; then
  mkdir -p "$repo_root/tmp/stage9-postgres-coverage"
  find "$repo_root/tmp/stage9-postgres-coverage" -maxdepth 1 -type f -name '.coverage.*' -delete
  coverage_args+=(
    -e STAGE9_COVERAGE_FILE=/app/tmp/stage9-postgres-coverage/.coverage
    -e PYTHONPATH=/app/stage9-test-packages-py311
    -v "$repo_root/tmp:/app/tmp"
    -v "$repo_root/tmp/stage9-site-packages-py311:/app/stage9-test-packages-py311:ro"
  )
elif [[ -n "${1:-}" ]]; then
  printf 'unknown argument: %s\n' "$1" >&2
  exit 2
fi

exec docker compose run --rm --no-deps \
  "${coverage_args[@]}" \
  -e TRADING_BOT_EXPECTED_CHECKOUT=/app \
  bot python scripts/run_registration_scratch_suite.py
