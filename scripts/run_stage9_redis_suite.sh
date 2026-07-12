#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

container_name="trading_bot_stage9_redis_$$"
redis_url="redis://${container_name}:6379/0"
coverage_args=()
test_command=(python -m unittest tests.test_stage5_event_isolation_redis tests.test_stage6_otp_delivery_redis)

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [[ "${1:-}" == "--coverage" ]]; then
  mkdir -p "$repo_root/tmp/stage9-redis-coverage"
  find "$repo_root/tmp/stage9-redis-coverage" -maxdepth 1 -type f -name '.coverage.*' -delete
  coverage_args+=(
    -e COVERAGE_FILE=/app/tmp/stage9-redis-coverage/.coverage
    -e PYTHONPATH=/app/stage9-test-packages-py311
    -v "$repo_root/tmp:/app/tmp"
    -v "$repo_root/tmp/stage9-site-packages-py311:/app/stage9-test-packages-py311:ro"
  )
  test_command=(python -m coverage run --branch --parallel-mode -m unittest tests.test_stage5_event_isolation_redis tests.test_stage6_otp_delivery_redis)
elif [[ -n "${1:-}" ]]; then
  printf 'unknown argument: %s\n' "$1" >&2
  exit 2
fi

docker compose run --rm --no-deps --name "$container_name" -d \
  redis redis-server --save "" --appendonly no >/dev/null

ready=false
for _attempt in $(seq 1 20); do
  if docker exec "$container_name" redis-cli ping 2>/dev/null | grep -qx PONG; then
    ready=true
    break
  fi
  sleep 0.25
done
if [[ "$ready" != true ]]; then
  printf 'Stage 9 disposable Redis did not become ready\n' >&2
  exit 1
fi

docker compose run --rm --no-deps \
  "${coverage_args[@]}" \
  -e STAGE5_TEST_REDIS_URL="$redis_url" \
  -e STAGE6_TEST_REDIS_URL="$redis_url" \
  bot "${test_command[@]}"

printf 'Stage 9 Redis suite complete: disposable_container=%s\n' "$container_name" >&2
