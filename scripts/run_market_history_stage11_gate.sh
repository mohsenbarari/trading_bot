#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
candidate_image="${MARKET_STAGE11_IMAGE:-trading-bot-market-pipeline:stage11-worktree}"
suffix="$$"
container="market-stage11-rehearsal-${suffix}"
volume="market-stage11-rehearsal-${suffix}"
network="market-stage11-rehearsal-${suffix}"
gate_root="$(mktemp -d /tmp/market-stage11-gate.XXXXXX)"

cleanup() {
  docker rm -f "${container}" >/dev/null 2>&1 || true
  docker volume rm "${volume}" >/dev/null 2>&1 || true
  docker network rm "${network}" >/dev/null 2>&1 || true
  rm -rf -- "${gate_root}"
}
trap cleanup EXIT

docker container inspect "${container}" >/dev/null 2>&1 && exit 70
docker volume inspect "${volume}" >/dev/null 2>&1 && exit 70
docker network inspect "${network}" >/dev/null 2>&1 && exit 70

mkdir -p "${gate_root}/output"
chown 10001:10001 "${gate_root}/output"
chmod 0700 "${gate_root}/output"

docker network create --internal "${network}" >/dev/null
docker volume create "${volume}" >/dev/null
docker run -d --name "${container}" \
  --network "${network}" --network-alias market-stage11-db \
  --mount "type=volume,src=${volume},dst=/var/lib/postgresql/data" \
  --mount "type=bind,src=${repo_root}/deploy/market-data/migrations,dst=/migrations,readonly" \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres:15-alpine >/dev/null

ready=0
for _ in $(seq 1 120); do
  if docker exec "${container}" pg_isready -U postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done
if [[ "${ready}" != "1" ]]; then
  exit 72
fi
docker exec "${container}" createdb -U postgres market_archive_rehearsal
docker exec "${container}" createdb -U postgres market_archive_pre_restore
docker exec "${container}" createdb -U postgres market_archive_post_restore
docker exec "${container}" psql -v ON_ERROR_STOP=1 -U postgres -d market_archive_rehearsal -f /migrations/0001_market_archive.up.sql >/dev/null
docker exec "${container}" psql -v ON_ERROR_STOP=1 -U postgres -d market_archive_rehearsal -f /migrations/0002_history_backfill.up.sql >/dev/null

docker exec "${container}" pg_dump -U postgres -Fc -d market_archive_rehearsal -f /tmp/pre.dump
pre_sha="$(docker exec "${container}" sha256sum /tmp/pre.dump | awk '{print $1}')"

docker run --rm --read-only \
  --network "${network}" \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount "type=bind,src=${repo_root},dst=/workspace,readonly" \
  --mount "type=bind,src=${gate_root}/output,dst=/output" \
  --workdir /workspace \
  --entrypoint python \
  "${candidate_image}" scripts/rehearse_market_history_stage11.py \
  --dsn postgresql://postgres@market-stage11-db/market_archive_rehearsal \
  --output-root /output

docker exec "${container}" pg_dump -U postgres -Fc -d market_archive_rehearsal -f /tmp/post.dump
post_sha="$(docker exec "${container}" sha256sum /tmp/post.dump | awk '{print $1}')"
docker exec "${container}" pg_restore -U postgres -d market_archive_pre_restore /tmp/pre.dump
docker exec "${container}" pg_restore -U postgres -d market_archive_post_restore /tmp/post.dump

pre_count="$(docker exec "${container}" psql -U postgres -d market_archive_pre_restore -tAc 'SELECT COUNT(*) FROM market_data.market_facts')"
post_count="$(docker exec "${container}" psql -U postgres -d market_archive_post_restore -tAc 'SELECT COUNT(*) FROM market_data.market_facts')"
post_batches="$(docker exec "${container}" psql -U postgres -d market_archive_post_restore -tAc "SELECT COUNT(*) FROM market_data.history_import_batches WHERE status='RECONCILED'")"

if [[ "${pre_count}" != "0" || "${post_count}" != "995" || "${post_batches}" != "6" ]]; then
  exit 71
fi

printf '{"backup_after_sha256":"%s","backup_before_sha256":"%s","cleanup":"scheduled","post_restore_fact_count":%s,"pre_restore_fact_count":%s,"reconciled_batch_count":%s,"status":"pass"}\n' \
  "${post_sha}" "${pre_sha}" "${post_count}" "${pre_count}" "${post_batches}"
