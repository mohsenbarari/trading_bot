#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

exec docker compose run --rm --no-deps \
  -e TRADING_BOT_EXPECTED_CHECKOUT=/app \
  bot python scripts/run_registration_scratch_suite.py
