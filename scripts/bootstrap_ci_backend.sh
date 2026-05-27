#!/usr/bin/env bash

set -euo pipefail

compose_args=()
if [[ -n "${CI_DOCKER_COMPOSE_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  compose_args=(${CI_DOCKER_COMPOSE_ARGS})
fi

compose() {
  docker compose "${compose_args[@]}" "$@"
}

dump_state_on_failure() {
  local exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    echo '== CI bootstrap failure: compose ps =='
    compose ps -a || true
    echo '== CI bootstrap failure: compose logs =='
    compose logs --no-color app migration db redis || true
  fi
  exit $exit_code
}

trap dump_state_on_failure EXIT

app_ready() {
  compose exec -T app python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=3) as response:
    if response.status != 200:
        raise SystemExit(f'unexpected_status={response.status}')
    payload = json.load(response)

if not isinstance(payload, dict):
    raise SystemExit('invalid_payload')
PY
}

wait_for_app_readiness() {
  local max_attempts="${CI_BOOTSTRAP_READINESS_RETRIES:-60}"
  local retry_delay="${CI_BOOTSTRAP_READINESS_DELAY_SECONDS:-1}"
  local attempt=1

  while (( attempt <= max_attempts )); do
    if app_ready >/dev/null 2>&1; then
      echo "== app ready after ${attempt}/${max_attempts} readiness checks =="
      return 0
    fi

    echo "== app readiness pending (${attempt}/${max_attempts}) =="
    sleep "$retry_delay"
    ((attempt++))
  done

  echo "App failed readiness check after ${max_attempts} attempts" >&2
  return 1
}

echo '== docker compose version =='
compose version

echo '== build shared base image =='
docker build -t trading_bot_base .

echo '== start db + redis =='
compose up -d db redis

echo '== run migration =='
compose up migration

echo '== start app =='
compose up -d app

echo '== wait for app readiness =='
wait_for_app_readiness

echo '== final compose ps =='
compose ps