#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MANIFEST="${MANIFEST:-${REPO_ROOT}/deploy/production/online.env}"
LOAD_RUNNER_HOST="${LOAD_RUNNER_HOST:-}"
LOAD_RUNNER_SSH_PORT="${LOAD_RUNNER_SSH_PORT:-22}"
LOAD_RUNNER_REMOTE_DIR="${LOAD_RUNNER_REMOTE_DIR:-/srv/trading-bot-loadtest}"
LOAD_RUNNER_ARTIFACT_ROOT="${LOAD_RUNNER_ARTIFACT_ROOT:-${REPO_ROOT}/tmp/production-benchmark}"
TARGET_HEALTH_URL="${TARGET_HEALTH_URL:-}"
TIMESTAMP="${TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"

usage() {
  cat <<'USAGE'
Usage:
  LOAD_RUNNER_HOST=user@host scripts/bootstrap_load_runner.sh [options]

Options:
  --manifest PATH              Production manifest path. Default: deploy/production/online.env
  --host USER@HOST             Load-runner SSH target. Same as LOAD_RUNNER_HOST.
  --port PORT                  SSH port. Default: 22
  --remote-dir PATH            Remote workspace. Default: /srv/trading-bot-loadtest
  --target-health-url URL      Health URL checked from the load-runner.
  --timestamp STAMP            Artifact timestamp. Default: current UTC stamp.
  -h, --help                   Show this help.

Environment:
  LOAD_RUNNER_HOST             Required unless --host is provided.
  LOAD_RUNNER_SSH_PORT         SSH port.
  LOAD_RUNNER_REMOTE_DIR       Remote workspace.
  TARGET_HEALTH_URL            Optional explicit URL to check from load-runner.

This script prepares the third load-runner host for Stage L1. It installs or
verifies curl, jq, and k6, creates a remote workspace, records a hardware/network
baseline, and verifies the load-runner can reach the Iran public health URL.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest)
      MANIFEST="$2"
      shift 2
      ;;
    --host)
      LOAD_RUNNER_HOST="$2"
      shift 2
      ;;
    --port)
      LOAD_RUNNER_SSH_PORT="$2"
      shift 2
      ;;
    --remote-dir)
      LOAD_RUNNER_REMOTE_DIR="$2"
      shift 2
      ;;
    --target-health-url)
      TARGET_HEALTH_URL="$2"
      shift 2
      ;;
    --timestamp)
      TIMESTAMP="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${LOAD_RUNNER_HOST}" ]]; then
  echo "ERROR: LOAD_RUNNER_HOST or --host is required, e.g. root@203.0.113.10" >&2
  exit 2
fi

if [[ -z "${TARGET_HEALTH_URL}" ]]; then
  TARGET_HEALTH_URL="$(
    python3 "${REPO_ROOT}/scripts/deploy_config.py" \
      --manifest "${MANIFEST}" \
      --format json \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("IRAN_HEALTHCHECK_URL") or (d.get("IRAN_SERVER_URL","").rstrip("/") + "/api/config"))'
  )"
fi

if [[ -z "${TARGET_HEALTH_URL}" || "${TARGET_HEALTH_URL}" == "/api/config" ]]; then
  echo "ERROR: could not resolve TARGET_HEALTH_URL from manifest; pass --target-health-url." >&2
  exit 2
fi

ARTIFACT_DIR="${LOAD_RUNNER_ARTIFACT_ROOT}/${TIMESTAMP}/load-runner-bootstrap"
mkdir -p "${ARTIFACT_DIR}"

SUMMARY_PATH="${ARTIFACT_DIR}/summary.md"
RESULTS_PATH="${ARTIFACT_DIR}/results.json"
STDOUT_PATH="${ARTIFACT_DIR}/bootstrap-stdout.log"
STDERR_PATH="${ARTIFACT_DIR}/bootstrap-stderr.log"

SSH_OPTS=(
  -p "${LOAD_RUNNER_SSH_PORT}"
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=accept-new
)

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

remote_quote() {
  printf "%q" "$1"
}

REMOTE_DIR_QUOTED="$(remote_quote "${LOAD_RUNNER_REMOTE_DIR}")"

log "Bootstrapping load-runner host ${LOAD_RUNNER_HOST}:${LOAD_RUNNER_SSH_PORT}"
log "Artifacts: ${ARTIFACT_DIR}"
log "Target health URL: ${TARGET_HEALTH_URL}"

set +e
ssh "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" "echo connected && uname -a" \
  >"${ARTIFACT_DIR}/ssh-probe.txt" \
  2>"${ARTIFACT_DIR}/ssh-probe.err"
SSH_STATUS=$?
set -e

if [[ ${SSH_STATUS} -ne 0 ]]; then
  cat >"${SUMMARY_PATH}" <<EOF
# Load Runner Bootstrap

- Status: failed
- Stage: L1
- Reason: SSH connection failed
- Host: \`${LOAD_RUNNER_HOST}\`
- Port: \`${LOAD_RUNNER_SSH_PORT}\`
- Artifact dir: \`${ARTIFACT_DIR}\`
EOF
  cat >"${RESULTS_PATH}" <<EOF
{
  "status": "failed",
  "stage": "L1",
  "reason": "ssh_connection_failed",
  "host": "${LOAD_RUNNER_HOST}",
  "port": "${LOAD_RUNNER_SSH_PORT}",
  "artifact_dir": "${ARTIFACT_DIR}"
}
EOF
  echo "ERROR: SSH connection failed. See ${ARTIFACT_DIR}/ssh-probe.err" >&2
  exit 1
fi

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -euo pipefail

REMOTE_DIR="__REMOTE_DIR__"
TARGET_HEALTH_URL="__TARGET_HEALTH_URL__"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  echo "ERROR: non-root user requires sudo to install packages." >&2
  exit 3
fi

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
else
  echo "ERROR: /etc/os-release not found; unsupported load-runner OS." >&2
  exit 3
fi

case "${ID:-}" in
  ubuntu|debian)
    ;;
  *)
    echo "ERROR: unsupported load-runner OS: ${ID:-unknown}. Use Ubuntu/Debian for Stage L1." >&2
    exit 3
    ;;
esac

export DEBIAN_FRONTEND=noninteractive

${SUDO} mkdir -p "${REMOTE_DIR}/artifacts" "${REMOTE_DIR}/bin"

if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1 || ! command -v gpg >/dev/null 2>&1; then
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y ca-certificates curl jq gnupg lsb-release
fi

if ! command -v k6 >/dev/null 2>&1; then
  ${SUDO} install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/k6-archive-keyring.gpg ]]; then
    curl -fsSL https://dl.k6.io/key.gpg | ${SUDO} gpg --dearmor -o /etc/apt/keyrings/k6-archive-keyring.gpg
    ${SUDO} chmod 0644 /etc/apt/keyrings/k6-archive-keyring.gpg
  fi
  echo "deb [signed-by=/etc/apt/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
    | ${SUDO} tee /etc/apt/sources.list.d/k6.list >/dev/null
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y k6
fi

if command -v timedatectl >/dev/null 2>&1; then
  ${SUDO} timedatectl set-timezone UTC || true
fi

BASELINE_FILE="${REMOTE_DIR}/artifacts/load-runner-baseline.txt"
{
  echo "=== utc ==="
  date -u +"%Y-%m-%dT%H:%M:%SZ"
  echo
  echo "=== os-release ==="
  cat /etc/os-release || true
  echo
  echo "=== uname ==="
  uname -a || true
  echo
  echo "=== cpu ==="
  lscpu || true
  echo
  echo "=== memory ==="
  free -h || true
  echo
  echo "=== disk ==="
  df -hT || true
  echo
  echo "=== route ==="
  ip route || true
  echo
  echo "=== tools ==="
  curl --version | head -n 1 || true
  jq --version || true
  k6 version || true
} >"${BASELINE_FILE}"

HEALTH_FILE="${REMOTE_DIR}/artifacts/iran-healthcheck.txt"
HTTP_CODE="$(
  curl -fsS \
    -o "${REMOTE_DIR}/artifacts/iran-healthcheck-body.json" \
    -w "%{http_code}" \
    --connect-timeout 10 \
    --max-time 30 \
    "${TARGET_HEALTH_URL}" \
    2>"${REMOTE_DIR}/artifacts/iran-healthcheck.err"
)"

{
  echo "target=${TARGET_HEALTH_URL}"
  echo "http_code=${HTTP_CODE}"
  echo "checked_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
} >"${HEALTH_FILE}"

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "ERROR: healthcheck returned HTTP ${HTTP_CODE}" >&2
  exit 4
fi

cat <<EOF
{
  "status": "passed",
  "stage": "L1",
  "remote_dir": "${REMOTE_DIR}",
  "target_health_url": "${TARGET_HEALTH_URL}",
  "http_code": ${HTTP_CODE},
  "checked_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "k6_version": "$(k6 version | sed 's/"/\\"/g')",
  "jq_version": "$(jq --version | sed 's/"/\\"/g')",
  "curl_version": "$(curl --version | head -n 1 | sed 's/"/\\"/g')"
}
EOF
REMOTE
)

REMOTE_SCRIPT="${REMOTE_SCRIPT/__REMOTE_DIR__/${LOAD_RUNNER_REMOTE_DIR}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT/__TARGET_HEALTH_URL__/${TARGET_HEALTH_URL}}"

set +e
ssh "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" "bash -s" \
  >"${STDOUT_PATH}" \
  2>"${STDERR_PATH}" <<<"${REMOTE_SCRIPT}"
BOOTSTRAP_STATUS=$?
set -e

REMOTE_RESULT="$(tail -n 40 "${STDOUT_PATH}" | sed -n '/^{/,$p' || true)"
if [[ -n "${REMOTE_RESULT}" ]]; then
  printf '%s\n' "${REMOTE_RESULT}" >"${RESULTS_PATH}"
else
  cat >"${RESULTS_PATH}" <<EOF
{
  "status": "failed",
  "stage": "L1",
  "reason": "missing_remote_result",
  "host": "${LOAD_RUNNER_HOST}",
  "port": "${LOAD_RUNNER_SSH_PORT}",
  "artifact_dir": "${ARTIFACT_DIR}"
}
EOF
fi

if [[ ${BOOTSTRAP_STATUS} -ne 0 ]]; then
  cat >"${SUMMARY_PATH}" <<EOF
# Load Runner Bootstrap

- Status: failed
- Stage: L1
- Host: \`${LOAD_RUNNER_HOST}\`
- Port: \`${LOAD_RUNNER_SSH_PORT}\`
- Remote dir: \`${LOAD_RUNNER_REMOTE_DIR}\`
- Target health URL: \`${TARGET_HEALTH_URL}\`
- Artifact dir: \`${ARTIFACT_DIR}\`

See:

- \`bootstrap-stdout.log\`
- \`bootstrap-stderr.log\`
- \`ssh-probe.txt\`
- \`ssh-probe.err\`
EOF
  echo "ERROR: load-runner bootstrap failed. See ${ARTIFACT_DIR}" >&2
  exit "${BOOTSTRAP_STATUS}"
fi

set +e
ssh "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" \
  "cd ${REMOTE_DIR_QUOTED}/artifacts && tar -czf - ." \
  >"${ARTIFACT_DIR}/remote-artifacts.tar.gz" \
  2>"${ARTIFACT_DIR}/remote-artifacts.err"
REMOTE_ARTIFACT_STATUS=$?
set -e

if [[ ${REMOTE_ARTIFACT_STATUS} -ne 0 ]]; then
  cat >"${SUMMARY_PATH}" <<EOF
# Load Runner Bootstrap

- Status: failed
- Stage: L1
- Host: \`${LOAD_RUNNER_HOST}\`
- Port: \`${LOAD_RUNNER_SSH_PORT}\`
- Remote dir: \`${LOAD_RUNNER_REMOTE_DIR}\`
- Target health URL: \`${TARGET_HEALTH_URL}\`
- Artifact dir: \`${ARTIFACT_DIR}\`
- Reason: failed to copy remote artifacts

The remote bootstrap completed, but local artifact collection failed. See
\`remote-artifacts.err\`.
EOF
  echo "ERROR: failed to collect remote artifacts. See ${ARTIFACT_DIR}/remote-artifacts.err" >&2
  exit 1
fi

cat >"${SUMMARY_PATH}" <<EOF
# Load Runner Bootstrap

- Status: passed
- Stage: L1
- Host: \`${LOAD_RUNNER_HOST}\`
- Port: \`${LOAD_RUNNER_SSH_PORT}\`
- Remote dir: \`${LOAD_RUNNER_REMOTE_DIR}\`
- Target health URL: \`${TARGET_HEALTH_URL}\`
- Artifact dir: \`${ARTIFACT_DIR}\`
- Remote artifacts: \`remote-artifacts.tar.gz\`

The load-runner can reach the Iran health endpoint and has \`k6\`, \`curl\`, and \`jq\` available.
EOF

log "Load-runner bootstrap passed."
log "Summary: ${SUMMARY_PATH}"
