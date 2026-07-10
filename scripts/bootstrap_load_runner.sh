#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MANIFEST="${MANIFEST:-${REPO_ROOT}/deploy/production/online.env}"
LOAD_RUNNER_HOST="${LOAD_RUNNER_HOST:-}"
LOAD_RUNNER_SSH_PORT="${LOAD_RUNNER_SSH_PORT:-22}"
LOAD_RUNNER_JUMP_HOST="${LOAD_RUNNER_JUMP_HOST:-}"
LOAD_RUNNER_JUMP_SSH_PORT="${LOAD_RUNNER_JUMP_SSH_PORT:-22}"
LOAD_RUNNER_PASSWORD="${LOAD_RUNNER_PASSWORD:-}"
LOAD_RUNNER_REMOTE_DIR="${LOAD_RUNNER_REMOTE_DIR:-/srv/trading-bot-loadtest}"
LOAD_RUNNER_K6_VERSION="${LOAD_RUNNER_K6_VERSION:-0.49.0}"
LOAD_RUNNER_SEED_K6_ARCHIVE="${LOAD_RUNNER_SEED_K6_ARCHIVE:-1}"
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
  --jump-host USER@HOST        SSH jump host used to reach the load-runner.
  --jump-port PORT             SSH jump host port. Default: 22
  --remote-dir PATH            Remote workspace. Default: /srv/trading-bot-loadtest
  --k6-version VERSION         Fallback k6 archive version. Default: 0.49.0
  --target-health-url URL      Health URL checked from the load-runner.
  --timestamp STAMP            Artifact timestamp. Default: current UTC stamp.
  -h, --help                   Show this help.

Environment:
  LOAD_RUNNER_HOST             Required unless --host is provided.
  LOAD_RUNNER_SSH_PORT         SSH port.
  LOAD_RUNNER_JUMP_HOST        Optional SSH jump host, e.g. root@65.109.220.59.
  LOAD_RUNNER_JUMP_SSH_PORT    Optional SSH jump host port. Default: 22.
  LOAD_RUNNER_PASSWORD         Optional final-host SSH password. Prefer env only.
  LOAD_RUNNER_REMOTE_DIR       Remote workspace.
  LOAD_RUNNER_K6_VERSION       Fallback k6 archive version.
  LOAD_RUNNER_SEED_K6_ARCHIVE  Download k6 locally and upload it to the load-runner. Default: 1.
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
    --jump-host)
      LOAD_RUNNER_JUMP_HOST="$2"
      shift 2
      ;;
    --jump-port)
      LOAD_RUNNER_JUMP_SSH_PORT="$2"
      shift 2
      ;;
    --remote-dir)
      LOAD_RUNNER_REMOTE_DIR="$2"
      shift 2
      ;;
    --k6-version)
      LOAD_RUNNER_K6_VERSION="$2"
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
  -o ConnectTimeout=20
  -o ConnectionAttempts=1
  -o NumberOfPasswordPrompts=1
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=accept-new
)
SCP_OPTS=(
  -P "${LOAD_RUNNER_SSH_PORT}"
  -o ConnectTimeout=20
  -o ConnectionAttempts=1
  -o NumberOfPasswordPrompts=1
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=accept-new
)

if [[ -n "${LOAD_RUNNER_JUMP_HOST}" ]]; then
  JUMP_SPEC="${LOAD_RUNNER_JUMP_HOST}"
  if [[ "${LOAD_RUNNER_JUMP_SSH_PORT}" != "22" ]]; then
    JUMP_SPEC="${JUMP_SPEC}:${LOAD_RUNNER_JUMP_SSH_PORT}"
  fi
  SSH_OPTS+=(-J "${JUMP_SPEC}")
  SCP_OPTS+=(-o "ProxyJump=${JUMP_SPEC}")
fi

SSH_CMD=(ssh)
SCP_CMD=(scp)
if [[ -n "${LOAD_RUNNER_PASSWORD}" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "ERROR: LOAD_RUNNER_PASSWORD was provided but local sshpass is not installed." >&2
    exit 2
  fi
  export SSHPASS="${LOAD_RUNNER_PASSWORD}"
  SSH_CMD=(sshpass -e ssh)
  SCP_CMD=(sshpass -e scp)
fi

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

remote_quote() {
  printf "%q" "$1"
}

map_k6_arch() {
  case "$1" in
    x86_64|amd64)
      printf 'amd64'
      ;;
    aarch64|arm64)
      printf 'arm64'
      ;;
    *)
      return 1
      ;;
  esac
}

REMOTE_DIR_QUOTED="$(remote_quote "${LOAD_RUNNER_REMOTE_DIR}")"

log "Bootstrapping load-runner host ${LOAD_RUNNER_HOST}:${LOAD_RUNNER_SSH_PORT}"
if [[ -n "${LOAD_RUNNER_JUMP_HOST}" ]]; then
  log "Using SSH jump host ${LOAD_RUNNER_JUMP_HOST}:${LOAD_RUNNER_JUMP_SSH_PORT}"
fi
log "Artifacts: ${ARTIFACT_DIR}"
log "Target health URL: ${TARGET_HEALTH_URL}"

set +e
  "${SSH_CMD[@]}" "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" "echo connected && uname -a" \
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
- Jump host: \`${LOAD_RUNNER_JUMP_HOST:-none}\`
- Artifact dir: \`${ARTIFACT_DIR}\`
EOF
  cat >"${RESULTS_PATH}" <<EOF
{
  "status": "failed",
  "stage": "L1",
  "reason": "ssh_connection_failed",
  "host": "${LOAD_RUNNER_HOST}",
  "port": "${LOAD_RUNNER_SSH_PORT}",
  "jump_host": "${LOAD_RUNNER_JUMP_HOST}",
  "artifact_dir": "${ARTIFACT_DIR}"
}
EOF
  echo "ERROR: SSH connection failed. See ${ARTIFACT_DIR}/ssh-probe.err" >&2
  exit 1
fi

set +e
REMOTE_MACHINE="$("${SSH_CMD[@]}" "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" "uname -m" 2>"${ARTIFACT_DIR}/remote-arch.err")"
REMOTE_ARCH_STATUS=$?
set -e

if [[ ${REMOTE_ARCH_STATUS} -ne 0 ]]; then
  echo "ERROR: could not detect load-runner architecture. See ${ARTIFACT_DIR}/remote-arch.err" >&2
  exit 1
fi

K6_ARCH="$(map_k6_arch "${REMOTE_MACHINE}")" || {
  echo "ERROR: unsupported load-runner architecture for k6: ${REMOTE_MACHINE}" >&2
  exit 2
}

K6_ARCHIVE_NAME="k6-v${LOAD_RUNNER_K6_VERSION}-linux-${K6_ARCH}.tar.gz"
K6_ARCHIVE_URL="https://github.com/grafana/k6/releases/download/v${LOAD_RUNNER_K6_VERSION}/${K6_ARCHIVE_NAME}"
K6_LOCAL_ARCHIVE="${ARTIFACT_DIR}/${K6_ARCHIVE_NAME}"
K6_REMOTE_SEED_DIR="${LOAD_RUNNER_REMOTE_DIR}/artifacts/seed"
K6_REMOTE_SEED_DIR_QUOTED="$(remote_quote "${K6_REMOTE_SEED_DIR}")"
K6_REMOTE_SEED_PATH="${K6_REMOTE_SEED_DIR}/${K6_ARCHIVE_NAME}"

set +e
"${SSH_CMD[@]}" "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" "command -v k6 >/dev/null 2>&1" \
  >"${ARTIFACT_DIR}/remote-k6-check.txt" \
  2>"${ARTIFACT_DIR}/remote-k6-check.err"
REMOTE_HAS_K6=$?
set -e

if [[ "${LOAD_RUNNER_SEED_K6_ARCHIVE}" != "0" && ${REMOTE_HAS_K6} -ne 0 ]]; then
  log "Preparing local k6 archive seed ${K6_ARCHIVE_NAME}"
  if [[ ! -s "${K6_LOCAL_ARCHIVE}" ]]; then
    curl -fL \
      --retry 3 \
      --retry-delay 2 \
      --connect-timeout 20 \
      --max-time 300 \
      -o "${K6_LOCAL_ARCHIVE}" \
      "${K6_ARCHIVE_URL}"
  fi
  "${SSH_CMD[@]}" "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" "mkdir -p ${K6_REMOTE_SEED_DIR_QUOTED}"
  "${SCP_CMD[@]}" "${SCP_OPTS[@]}" "${K6_LOCAL_ARCHIVE}" "${LOAD_RUNNER_HOST}:${K6_REMOTE_SEED_DIR}/" \
    >"${ARTIFACT_DIR}/k6-seed-upload.txt" \
    2>"${ARTIFACT_DIR}/k6-seed-upload.err"
elif [[ ${REMOTE_HAS_K6} -eq 0 ]]; then
  log "Remote k6 already available; skipping k6 seed upload."
fi

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -euo pipefail

REMOTE_DIR="__REMOTE_DIR__"
TARGET_HEALTH_URL="__TARGET_HEALTH_URL__"
K6_VERSION="__K6_VERSION__"
K6_SEED_ARCHIVE="__K6_SEED_ARCHIVE__"

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

step() {
  printf '[remote %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

apt_update() {
  timeout 900 ${SUDO} apt-get update
}

apt_install() {
  timeout 900 ${SUDO} apt-get install -y "$@"
}

install_k6_from_apt() {
  ${SUDO} install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/k6-archive-keyring.gpg ]]; then
    curl -fsSL https://dl.k6.io/key.gpg | ${SUDO} gpg --dearmor -o /etc/apt/keyrings/k6-archive-keyring.gpg
    ${SUDO} chmod 0644 /etc/apt/keyrings/k6-archive-keyring.gpg
  fi
  echo "deb [signed-by=/etc/apt/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
    | ${SUDO} tee /etc/apt/sources.list.d/k6.list >/dev/null
  apt_update
  apt_install k6
}

install_k6_from_archive() {
  local machine
  local k6_arch
  local archive_url
  local tmp_dir
  local archive_path
  local k6_binary

  machine="$(uname -m)"
  case "${machine}" in
    x86_64|amd64)
      k6_arch="amd64"
      ;;
    aarch64|arm64)
      k6_arch="arm64"
      ;;
    *)
      echo "ERROR: unsupported k6 archive architecture: ${machine}" >&2
      exit 3
      ;;
  esac

  tmp_dir="$(mktemp -d)"
  if [[ -f "${K6_SEED_ARCHIVE}" ]]; then
    archive_path="${K6_SEED_ARCHIVE}"
    step "installing k6 from seeded archive ${K6_SEED_ARCHIVE}"
  else
    archive_path="${tmp_dir}/k6.tar.gz"
    archive_url="https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-linux-${k6_arch}.tar.gz"
    step "downloading k6 archive ${archive_url}"
    curl -fL \
      --retry 3 \
      --retry-delay 2 \
      --connect-timeout 20 \
      --max-time 300 \
      -o "${archive_path}" \
      "${archive_url}"
  fi
  k6_binary="$(tar -tzf "${archive_path}" | awk '/\/k6$/ {print; exit}')"
  if [[ -z "${k6_binary}" ]]; then
    echo "ERROR: k6 binary not found in archive ${archive_path}" >&2
    exit 3
  fi
  tar -xzf "${archive_path}" -C "${tmp_dir}" "${k6_binary}"
  ${SUDO} install -m 0755 "${tmp_dir}/${k6_binary}" /usr/local/bin/k6
  rm -rf "${tmp_dir}"
}

step "creating load-runner workspace"
${SUDO} mkdir -p "${REMOTE_DIR}/artifacts" "${REMOTE_DIR}/bin"

if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1 || ! command -v gpg >/dev/null 2>&1; then
  step "installing base packages"
  apt_update
  apt_install ca-certificates curl jq gnupg lsb-release
else
  step "base packages already available"
fi

if ! command -v k6 >/dev/null 2>&1; then
  step "installing k6"
  if [[ -f "${K6_SEED_ARCHIVE}" ]]; then
    install_k6_from_archive
  elif install_k6_from_apt; then
    step "k6 installed from apt"
  else
    step "apt k6 install failed; falling back to pinned archive v${K6_VERSION}"
    install_k6_from_archive
  fi
else
  step "k6 already available"
fi

if command -v timedatectl >/dev/null 2>&1; then
  step "setting timezone to UTC"
  ${SUDO} timedatectl set-timezone UTC || true
fi

step "recording baseline"
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

step "checking target health URL"
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
REMOTE_SCRIPT="${REMOTE_SCRIPT/__K6_VERSION__/${LOAD_RUNNER_K6_VERSION}}"
REMOTE_SCRIPT="${REMOTE_SCRIPT/__K6_SEED_ARCHIVE__/${K6_REMOTE_SEED_PATH}}"
REMOTE_SCRIPT_B64="$(printf '%s' "${REMOTE_SCRIPT}" | base64 -w 0)"
REMOTE_BOOTSTRAP_CMD="printf '%s' '${REMOTE_SCRIPT_B64}' | base64 -d | bash"

set +e
"${SSH_CMD[@]}" "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" \
  "bash -lc $(printf '%q' "${REMOTE_BOOTSTRAP_CMD}")" \
  >"${STDOUT_PATH}" \
  2>"${STDERR_PATH}"
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
  "jump_host": "${LOAD_RUNNER_JUMP_HOST}",
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
- Jump host: \`${LOAD_RUNNER_JUMP_HOST:-none}\`
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
"${SSH_CMD[@]}" "${SSH_OPTS[@]}" "${LOAD_RUNNER_HOST}" \
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
- Jump host: \`${LOAD_RUNNER_JUMP_HOST:-none}\`
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
- Jump host: \`${LOAD_RUNNER_JUMP_HOST:-none}\`
- Remote dir: \`${LOAD_RUNNER_REMOTE_DIR}\`
- Target health URL: \`${TARGET_HEALTH_URL}\`
- Artifact dir: \`${ARTIFACT_DIR}\`
- Remote artifacts: \`remote-artifacts.tar.gz\`

The load-runner can reach the Iran health endpoint and has \`k6\`, \`curl\`, and \`jq\` available.
EOF

log "Load-runner bootstrap passed."
log "Summary: ${SUMMARY_PATH}"
