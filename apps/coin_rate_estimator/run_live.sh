#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${COIN_RATE_ESTIMATOR_PORT:?COIN_RATE_ESTIMATOR_PORT is required}"
: "${COIN_RATE_ESTIMATOR_PATH:?COIN_RATE_ESTIMATOR_PATH is required}"

exec python3 "$script_dir/live_server.py" \
  --host "${COIN_RATE_ESTIMATOR_HOST:-127.0.0.1}" \
  --port "$COIN_RATE_ESTIMATOR_PORT" \
  --path "$COIN_RATE_ESTIMATOR_PATH" \
  --wallex-interval "${COIN_RATE_ESTIMATOR_WALLEX_INTERVAL:-10}" \
  --ime-interval "${COIN_RATE_ESTIMATOR_IME_INTERVAL:-0}" \
  --backfill-minutes "${COIN_RATE_ESTIMATOR_BACKFILL_MINUTES:-15}"
