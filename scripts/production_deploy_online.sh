#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_ENV_RENDERER="$PROJECT_DIR/scripts/render_runtime_envs.py"
RELEASE_ARTIFACT_RENDERER="$PROJECT_DIR/scripts/render_release_artifacts.py"
DEPLOYMENT_SURFACE_GUARD="$PROJECT_DIR/scripts/check_deployment_surface_guard.py"
PRODUCTION_DATA_HYGIENE_SCRIPT="$PROJECT_DIR/scripts/check_production_data_hygiene.py"
CHANGE_LOG_SOURCE_SEQUENCE_ALIGNER="$PROJECT_DIR/scripts/align_change_log_source_sequence.py"
TRADE_NUMBER_SEQUENCE_ALIGNER="$PROJECT_DIR/scripts/align_trade_number_sequence.py"
PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT="$PROJECT_DIR/scripts/relay_production_coin_inference_snapshot.py"
PRODUCTION_COIN_SNAPSHOT_RELAY_INSTALLER="$PROJECT_DIR/scripts/install_production_coin_inference_snapshot_relay.sh"
PRODUCTION_COIN_INPUT_TIMER_INSTALLER="$PROJECT_DIR/scripts/install_coin_intelligence_input_timers.sh"
PRODUCTION_COIN_READINESS_SCRIPT="$PROJECT_DIR/scripts/check_production_coin_inference_readiness.py"
TELEGRAM_QUEUE_PRODUCTION_CUTOVER_SCRIPT="$PROJECT_DIR/scripts/cutover_telegram_delivery_queue_production.py"
PRODUCTION_RELEASE_LOCK_DIR="/root/secure-envs/trading-bot/queue-cutover-artifacts"
PRODUCTION_RELEASE_LOCK_PATH="$PRODUCTION_RELEASE_LOCK_DIR/production-release.lock"
DEFAULT_MANIFEST="$PROJECT_DIR/deploy/production/online.env"
MANIFEST_PATH="${DEPLOY_MANIFEST:-$DEFAULT_MANIFEST}"
COMMAND=""
IRAN_BOOTSTRAP_APT_PACKAGES="ca-certificates curl gnupg lsb-release rsync jq pigz nginx certbot python3-certbot-nginx docker.io python3-pip python3-setuptools python3-wheel"
IRAN_BOOTSTRAP_COMPOSE_PACKAGES="docker-compose-v2 docker-compose"
SHARED_SYNC_TABLES_SQL="users, accountant_relations, customer_relations, telegram_link_tokens, invitations, admin_market_messages, admin_broadcast_messages, notifications, user_notification_preferences, user_blocks, commodities, commodity_aliases, trading_settings, market_schedule_overrides, market_runtime_state, offers, offer_publication_states, offer_requests, trades, trade_delivery_receipts, telegram_admin_broadcasts, telegram_admin_broadcast_receipts, telegram_notification_outbox"
IRAN_SHARED_RESET_CONFIRM_TEXT="RESET_IRAN_SHARED_DATA"
LOCAL_HOST_ARCH=""
LOCAL_DPKG_ARCH=""
LOCAL_OS_CODENAME=""
IRAN_HOST_ARCH=""
IRAN_DPKG_ARCH=""
IRAN_OS_CODENAME=""
IRAN_IMAGE_PLATFORM=""
LOCAL_COMPOSE_CMD=""
IRAN_COMPOSE_CMD=""
IRAN_APT_BUNDLE_MODE="same-arch"
PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME="trading_bot"
FOREIGN_COMPOSE_PROJECT_NAME=""
COMPOSE_PROJECT_NAME=""
PRODUCTION_COIN_SNAPSHOT_RELAY_CONFIRM_TEXT="publish-production-coin-inference-snapshot"
PRODUCTION_COIN_SNAPSHOT_RELAY_DISABLE_CONFIRM_TEXT="disable-production-coin-inference-snapshot"
PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE="coin-intelligence-production-snapshot-relay.service"
PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER="coin-intelligence-production-snapshot-relay.timer"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="/var/lib/trading-bot/production-release/coin-snapshot-relay-state.json"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL"
PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR_CANONICAL="/var/lib/trading-bot/production-release/coin-input-timer-recovery"
PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR="$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR_CANONICAL"
PRODUCTION_COIN_INPUT_SYSTEMD_DIR="/etc/systemd/system"
PRODUCTION_TWO_HOST_RELEASE_STATE_FILE_CANONICAL="/var/lib/trading-bot/production-release/two-host-release-state.json"
PRODUCTION_TWO_HOST_RELEASE_STATE_FILE="$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE_CANONICAL"
PRODUCTION_DEPLOY_SH_AUTHORITY_PATH="/var/lib/trading-bot/production-release/deploy-sh-authority.json"
PRODUCTION_WRITER_QUIESCE_STATE_FILE="/var/lib/trading-bot/production-release/writer-quiesce-state.json"
PRODUCTION_COIN_SNAPSHOT_RELAY_HAD_UNIT=0
PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED=0
PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE=0
PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE_WAS_ACTIVE=0
PRODUCTION_COIN_SNAPSHOT_RELAY_RECOVERY_PENDING=0
PRODUCTION_COIN_SNAPSHOT_RELAY_GUARD_ARMED=0
PRODUCTION_COIN_INPUT_TIMER_GUARD_ARMED=0
PRODUCTION_RUNTIME_ENV_PAIR_LOCKED=0
PRODUCTION_RUNTIME_ENV_SOURCE_SHA256=""
PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256=""
PRODUCTION_RUNTIME_ENV_IRAN_SHA256=""
PRODUCTION_RUNTIME_ENV_FOREIGN_INSTALLED=0
PRODUCTION_RELEASE_LOCK_OWNED=0
PRODUCTION_SOURCE_LOCK_FD=""
PRODUCTION_SOURCE_LOCK_PATH=""
PRODUCTION_SOURCE_LOCK_OWNED=0
PRODUCTION_SOURCE_LOCK_INHERITED_OBSERVED=0
PRODUCTION_QUEUE_CUTOVER_REBUILD_EVIDENCE=0
PRODUCTION_TWO_HOST_RELEASE_GUARD_ARMED=0
PRODUCTION_TWO_HOST_RELEASE_PHASE=""
PRODUCTION_TWO_HOST_RELEASE_RESUMING=0
PRODUCTION_TWO_HOST_WRITERS_QUIESCED=0
PRODUCTION_TWO_HOST_SCHEMAS_VERIFIED=0
PRODUCTION_TWO_HOST_WRITER_RESTART_GUARD_ARMED=0
PRODUCTION_RELEASE_TREE=""
PRODUCTION_FOREIGN_IMAGE_ID=""
PRODUCTION_FOREIGN_IMAGE_SIGNATURE=""
PRODUCTION_FOREIGN_IMAGE_RECEIPT=""
PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256=""
PRODUCTION_IRAN_IMAGE_ID=""
PRODUCTION_IRAN_REMOTE_IMAGE_ID=""
PRODUCTION_IRAN_IMAGE_SIGNATURE=""
PRODUCTION_IRAN_IMAGE_BUNDLE_SHA256=""
PRODUCTION_IRAN_IMAGE_RECEIPT=""
PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256=""
PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256=""
PRODUCTION_RELEASE_EVIDENCE_MAXIMUM_AGE_SECONDS=3600
PRODUCTION_RELEASE_EVIDENCE_VERIFIED=0
PRODUCTION_PRE_RELEASE_SHA=""
PRODUCTION_BACKUP_ARTIFACT_SET_SHA256=""
PRODUCTION_RELEASE_SCHEMA_HEAD=""
PRODUCTION_FOREIGN_TARGET_BINDING_SHA256=""
PRODUCTION_IRAN_TARGET_BINDING_SHA256=""
PRODUCTION_COIN_INFERENCE_REQUESTED=0

usage() {
    cat <<'EOF'
Production release script driven from the foreign server.

Usage:
  scripts/production_deploy_online.sh [--manifest /path/to/online.env] [command]

Commands:
  help                 Show this help.
  release              Run the full production flow. This is the default.
  check-local          Validate local tooling and manifest.
  deploy-foreign       Internal release phase; direct execution is refused.
  bootstrap-iran       Install Docker/Nginx/Certbot prerequisites on the Iran host.
  configure-nginx      Render and install the Iran Nginx config.
  issue-cert           Request/renew the SSL certificate on the Iran host.
  build-release        Build frontend locally, prepare wheel cache, and build/loadable Docker artifacts.
  prepare-release-evidence
                       Build both exact production images and their receipts without
                       touching services or databases; run this before backup/rehearsal.
  sync-project         Internal release phase; direct execution is refused.
  ship-images          Internal release phase; direct execution is refused.
  load-images          Internal release phase; direct execution is refused.
  verify-release-evidence
                       Validate the exact backup, restore-smoke, migration rehearsal,
                       source, image, schema, and target receipts without quiescing writers.
  deploy-iran          Internal release phase; direct execution is refused.
  inspect-shared-data  Inspect Iran shared-table state and print the fresh/existing classification.
  seed-shared-data     Internal release phase; direct execution is refused.
  healthcheck          Validate local and public health endpoints.

Notes:
  - The script determines Iran connectivity before touching either host.
  - If Iran is online, it runs the guarded two-host flow using shipped images/artifacts.
  - If Iran is offline, it stops before either host is changed; a one-host release is forbidden.
  - For SSH, prefer key-based auth. Password auth is supported only when sshpass is installed.
  - Release healthcheck runs a read-only production data hygiene guard on both hosts.
  - The full release refuses to quiesce either writer plane until fresh successful
    two-host backup/restore-smoke and migration-rehearsal receipts are verified.
    Their paths and SHA-256 digests must be supplied by the production manifest as
    PRODUCTION_BACKUP_RECEIPT_PATH / PRODUCTION_BACKUP_RECEIPT_SHA256 and
    PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH /
    PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256.
  - The production coin Snapshot relay remains off unless the manifest contains both
    PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1 and the exact confirmation value.
  - Setting the relay flag to 0 is an explicit disabled state. If the timer is
    active, the separate exact disable confirmation is required before it is
    left stopped and disabled.
EOF
}

log() {
    printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

is_truthy() {
    local value="${1:-}"
    value="${value,,}"
    case "$value" in
        1|true|yes|y|on) return 0 ;;
        *) return 1 ;;
    esac
}

canonical_path() {
    python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

normalize_arch() {
    local raw="${1:-}"
    case "$raw" in
        x86_64|amd64) printf 'amd64\n' ;;
        aarch64|arm64) printf 'arm64\n' ;;
        *) die "Unsupported architecture: $raw" ;;
    esac
}

docker_platform_for_arch() {
    local arch
    arch="$(normalize_arch "$1")"
    case "$arch" in
        amd64) printf 'linux/amd64\n' ;;
        arm64) printf 'linux/arm64\n' ;;
    esac
}

append_pip_platform_args() {
    local arch
    arch="$(normalize_arch "$1")"
    case "$arch" in
        amd64)
            printf '%s\n' \
                "--platform" "manylinux2014_x86_64" \
                "--platform" "manylinux_2_17_x86_64" \
                "--platform" "manylinux_2_28_x86_64" \
                "--platform" "linux_x86_64" \
                "--platform" "any"
            ;;
        arm64)
            printf '%s\n' \
                "--platform" "manylinux2014_aarch64" \
                "--platform" "manylinux_2_17_aarch64" \
                "--platform" "manylinux_2_28_aarch64" \
                "--platform" "linux_aarch64" \
                "--platform" "any"
            ;;
    esac
}

resolve_local_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        LOCAL_COMPOSE_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        LOCAL_COMPOSE_CMD="docker-compose"
    else
        die "Neither 'docker compose' nor 'docker-compose' is available on the foreign host."
    fi
}

detect_runtime_metadata() {
    LOCAL_HOST_ARCH="$(normalize_arch "$(uname -m)")"
    LOCAL_DPKG_ARCH="$(normalize_arch "$(dpkg --print-architecture)")"
    LOCAL_OS_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-unknown}")"
    resolve_local_compose_cmd

    local remote_info
    remote_info="$(ssh_iran "set -euo pipefail
printf '%s %s %s\n' \"\$(uname -m)\" \"\$(dpkg --print-architecture)\" \"\$(. /etc/os-release && printf '%s' \"\$VERSION_CODENAME\")\"
if docker compose version >/dev/null 2>&1; then
  printf 'docker compose\n'
elif command -v docker-compose >/dev/null 2>&1; then
  printf 'docker-compose\n'
else
  printf 'missing\n'
fi")"

    IRAN_HOST_ARCH="$(printf '%s\n' "$remote_info" | sed -n '1p' | awk '{print $1}')"
    IRAN_DPKG_ARCH="$(printf '%s\n' "$remote_info" | sed -n '1p' | awk '{print $2}')"
    IRAN_OS_CODENAME="$(printf '%s\n' "$remote_info" | sed -n '1p' | awk '{print $3}')"
    IRAN_COMPOSE_CMD="$(printf '%s\n' "$remote_info" | sed -n '2p')"

    IRAN_HOST_ARCH="$(normalize_arch "$IRAN_HOST_ARCH")"
    IRAN_DPKG_ARCH="$(normalize_arch "$IRAN_DPKG_ARCH")"
    [[ "$IRAN_COMPOSE_CMD" != "missing" ]] || IRAN_COMPOSE_CMD=""
    IRAN_IMAGE_PLATFORM="$(docker_platform_for_arch "$IRAN_HOST_ARCH")"
    if [[ "$LOCAL_DPKG_ARCH" != "$IRAN_DPKG_ARCH" || "$LOCAL_OS_CODENAME" != "$IRAN_OS_CODENAME" ]]; then
        IRAN_APT_BUNDLE_MODE="remote-install"
    else
        IRAN_APT_BUNDLE_MODE="same-arch"
    fi

    log "Foreign arch=$LOCAL_HOST_ARCH dpkg=$LOCAL_DPKG_ARCH codename=${LOCAL_OS_CODENAME:-unknown} compose='$LOCAL_COMPOSE_CMD'"
    log "Iran arch=$IRAN_HOST_ARCH dpkg=$IRAN_DPKG_ARCH codename=${IRAN_OS_CODENAME:-unknown} compose='${IRAN_COMPOSE_CMD:-missing}' apt_bundle_mode=$IRAN_APT_BUNDLE_MODE"
}

ensure_buildx_for_target() {
    if [[ "$LOCAL_HOST_ARCH" == "$IRAN_HOST_ARCH" ]]; then
        return 0
    fi

    if ! docker buildx version >/dev/null 2>&1; then
        die "Cross-arch image build requires docker buildx on the foreign host."
    fi

    log "Preparing buildx for cross-arch image build ($IRAN_IMAGE_PLATFORM)"
    docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null
    if ! docker buildx inspect trading-bot-builder >/dev/null 2>&1; then
        docker buildx create --name trading-bot-builder --use >/dev/null
    else
        docker buildx use trading-bot-builder >/dev/null
    fi
    docker buildx inspect --bootstrap >/dev/null
}

remote_compose_resolver() {
    cat <<'EOF'
if docker compose version >/dev/null 2>&1; then
  compose_cmd='docker compose'
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd='docker-compose'
else
  echo "No Docker Compose command is available on the Iran host." >&2
  exit 1
fi
EOF
}

remote_post_bootstrap_guard() {
    cat <<'EOF'
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  apt-get -o Acquire::Retries=5 update
  compose_package=''
  for candidate in docker-compose-v2 docker-compose; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
      compose_package="$candidate"
      break
    fi
  done
  if [ -n "$compose_package" ]; then
    apt-get -o Acquire::Retries=5 install -y --fix-missing "$compose_package" || true
  fi
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  echo "No Docker Compose command is available on the Iran host after bootstrap." >&2
  exit 1
fi
EOF
}

remote_docker_service_guard() {
    cat <<'EOF'
systemctl daemon-reload || true
systemctl reset-failed docker.service docker.socket || true
systemctl enable --now containerd.service || true
if systemctl list-unit-files docker.socket >/dev/null 2>&1; then
  systemctl enable --now docker.socket || true
fi
systemctl enable docker.service || true
if ! systemctl start docker.service; then
  systemctl restart docker.socket || true
  systemctl start docker.service
fi
docker info >/dev/null
EOF
}

remote_bootstrap_ready_guard() {
    cat <<'EOF'
set -euo pipefail
for cmd in curl gpg rsync jq pigz nginx certbot python3 docker; do
  command -v "$cmd" >/dev/null 2>&1 || exit 1
done
python3 -m pip --version >/dev/null 2>&1 || exit 1
python3 - <<'PY' >/dev/null 2>&1 || exit 1
import setuptools
import wheel
PY
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  exit 1
fi
docker info >/dev/null 2>&1 || exit 1
systemctl is-active --quiet nginx || exit 1
timezone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
[ "$timezone" = "UTC" ] || exit 1
EOF
}

remote_docker_cleanup_guard() {
    cat <<'EOF'
docker_cleanup_packages=""
for pkg in containerd.io docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin; do
  if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
    docker_cleanup_packages="$docker_cleanup_packages $pkg"
  fi
done
if [ -n "$docker_cleanup_packages" ]; then
  apt-get -y purge $docker_cleanup_packages || true
fi
apt-get -y autoremove || true
EOF
}

remote_cert_renewal_guard() {
    cat <<'EOF'
if systemctl list-unit-files certbot.timer >/dev/null 2>&1; then
  systemctl enable --now certbot.timer
else
  printf '%s\n' '0 3,15 * * * root certbot renew --quiet --deploy-hook "systemctl reload nginx"' > /etc/cron.d/certbot-renew
  chmod 644 /etc/cron.d/certbot-renew
fi
EOF
}

prompt_value() {
    local __name="$1"
    local __label="$2"
    local __default="${3:-}"
    local __secret="${4:-0}"
    local __input=""

    if [[ -n "$__default" ]]; then
        if [[ "$__secret" == "1" ]]; then
            read -r -s -p "$__label [$__default]: " __input
            echo
        else
            read -r -p "$__label [$__default]: " __input
        fi
        __input="${__input:-$__default}"
    else
        while [[ -z "$__input" ]]; do
            if [[ "$__secret" == "1" ]]; then
                read -r -s -p "$__label: " __input
                echo
            else
                read -r -p "$__label: " __input
            fi
        done
    fi

    printf -v "$__name" '%s' "$__input"
}

ensure_manifest_file() {
    if [[ -f "$MANIFEST_PATH" ]]; then
        return 0
    fi

    log "Manifest not found. Creating it at $MANIFEST_PATH"
    mkdir -p "$(dirname "$MANIFEST_PATH")"

    local local_project_dir="$PROJECT_DIR"
    local local_frontend_dir="$PROJECT_DIR/frontend"
    local local_dist_dir="$PROJECT_DIR/mini_app_dist"
    local foreign_public_ip=""
    local foreign_public_domain=""
    local foreign_server_url=""
    local foreign_server_domain=""
    local foreign_timezone="UTC"
    local iran_host=""
    local iran_ssh_user="root"
    local iran_ssh_port="37067"
    local iran_ssh_auth_method="key"
    local iran_ssh_private_key_path="$HOME/.ssh/id_ed25519"
    local iran_ssh_password=""
    local iran_project_dir="/srv/trading-bot/current"
    local iran_deploy_base_dir="/srv/trading-bot"
    local iran_timezone="UTC"
    local iran_public_ip=""
    local iran_public_domain=""
    local iran_app_domain=""
    local iran_server_url=""
    local iran_server_domain=""
    local iran_certbot_email=""
    local runtime_env_source_path="/root/secure-envs/trading-bot/.env.foreign.production"
    local foreign_runtime_env_path="/root/secure-envs/trading-bot/runtime/.env.foreign.production"
    local iran_runtime_env_path="/root/secure-envs/trading-bot/runtime/.env.iran.production"
    local foreign_frontend_url=""
    local iran_frontend_url=""

    prompt_value local_project_dir "Local project dir" "$local_project_dir"
    prompt_value local_frontend_dir "Local frontend dir" "$local_frontend_dir"
    prompt_value local_dist_dir "Local dist dir" "$local_dist_dir"
    prompt_value foreign_public_ip "Foreign public IP"
    prompt_value foreign_public_domain "Foreign public domain"
    prompt_value foreign_server_url "Foreign server URL" "https://$foreign_public_domain"
    prompt_value foreign_server_domain "Foreign server domain" "$foreign_public_domain"
    prompt_value foreign_timezone "Foreign timezone" "$foreign_timezone"
    prompt_value iran_host "Iran SSH host/IP"
    prompt_value iran_ssh_user "Iran SSH user" "$iran_ssh_user"
    prompt_value iran_ssh_port "Iran SSH port" "$iran_ssh_port"
    prompt_value iran_ssh_auth_method "Iran SSH auth method (key/password)" "$iran_ssh_auth_method"
    if [[ "${iran_ssh_auth_method,,}" == "key" ]]; then
        prompt_value iran_ssh_private_key_path "Iran SSH private key path" "$iran_ssh_private_key_path"
    else
        prompt_value iran_ssh_password "Iran SSH password" "" 1
    fi
    prompt_value iran_project_dir "Iran project dir" "$iran_project_dir"
    prompt_value iran_deploy_base_dir "Iran deploy base dir" "$iran_deploy_base_dir"
    prompt_value iran_timezone "Iran timezone" "$iran_timezone"
    prompt_value iran_public_ip "Iran public IP"
    prompt_value iran_public_domain "Iran public domain"
    prompt_value iran_app_domain "Iran app domain" "$iran_public_domain"
    prompt_value iran_server_url "Iran server URL" "https://$iran_app_domain"
    prompt_value iran_server_domain "Iran server domain" "$iran_app_domain"
    prompt_value iran_certbot_email "Certbot email"
    prompt_value runtime_env_source_path "Immutable production secret source path" "$runtime_env_source_path"
    prompt_value foreign_runtime_env_path "Foreign rendered runtime env path" "$foreign_runtime_env_path"
    prompt_value iran_runtime_env_path "Iran rendered runtime env path" "$iran_runtime_env_path"
    prompt_value foreign_frontend_url "Foreign FRONTEND_URL" "https://$foreign_public_domain"
    prompt_value iran_frontend_url "Iran FRONTEND_URL" "https://$iran_app_domain"

    cat > "$MANIFEST_PATH" <<EOF
# Production deployment manifest for the foreign-controlled release flow.

# --- Local / foreign control plane ---
LOCAL_PROJECT_DIR=$local_project_dir
LOCAL_FRONTEND_DIR=$local_frontend_dir
LOCAL_DIST_DIR=$local_dist_dir
FOREIGN_PUBLIC_IP=$foreign_public_ip
FOREIGN_PUBLIC_DOMAIN=$foreign_public_domain
FOREIGN_SERVER_URL=$foreign_server_url
FOREIGN_SERVER_DOMAIN=$foreign_server_domain
FOREIGN_TIMEZONE=$foreign_timezone

# --- Iran SSH access ---
IRAN_HOST=$iran_host
IRAN_SSH_USER=$iran_ssh_user
IRAN_SSH_PORT=$iran_ssh_port
IRAN_SSH_AUTH_METHOD=${iran_ssh_auth_method,,}
IRAN_SSH_PRIVATE_KEY_PATH=$iran_ssh_private_key_path
IRAN_SSH_PASSWORD=$iran_ssh_password
IRAN_PROJECT_DIR=$iran_project_dir
IRAN_DEPLOY_BASE_DIR=$iran_deploy_base_dir
IRAN_TIMEZONE=$iran_timezone

# --- Iran public app ---
IRAN_PUBLIC_IP=$iran_public_ip
IRAN_APP_DOMAIN=$iran_app_domain
IRAN_PUBLIC_DOMAIN=$iran_public_domain
IRAN_SERVER_URL=$iran_server_url
IRAN_SERVER_DOMAIN=$iran_server_domain
IRAN_SERVER_ALIASES=$iran_server_domain
IRAN_CERTBOT_EMAIL=$iran_certbot_email

# --- Local / remote env files ---
RUNTIME_ENV_SOURCE_PATH=$runtime_env_source_path
FOREIGN_RUNTIME_ENV_PATH=$foreign_runtime_env_path
IRAN_RUNTIME_ENV_PATH=$iran_runtime_env_path
FOREIGN_FRONTEND_URL=$foreign_frontend_url
IRAN_FRONTEND_URL=$iran_frontend_url
PUBLIC_WEBAPP_URL=$iran_frontend_url
FOREIGN_SERVER_ALIASES=$foreign_public_domain
REQUIRE_WEB_PUSH=1
REQUIRE_OFFER_EXPIRY_COMMAND_RECEIPTS=1
ENV_BACKUP_DIR=/root/secure-envs/trading-bot/backups
ALLOW_PROJECT_ENV_SOURCE=0

# --- Optional runtime toggles ---
IRAN_SKIP_CERTBOT=0
IRAN_SKIP_FRONTEND_BUILD=0
IRAN_DEPLOY_WITH_WAIT=1
IRAN_RUN_POST_DEPLOY_HEALTHCHECK=1
IRAN_ENABLE_UFW=0
IRAN_CONNECTIVITY_MODE=ask
IRAN_SKIP_FOREIGN_DEPLOY=0
IRAN_HOSTS_SYNC_ENABLED=1
IRAN_FORCE_RELEASE_REFRESH=0
IRAN_ALLOW_DIRTY_RELEASE=0
PRODUCTION_RELEASE_BRANCH=main
IRAN_ALLOW_NON_MAIN_RELEASE=0
IRAN_ALLOW_RELEASE_BRANCH_DRIFT=0
IRAN_SHARED_DATA_MODE=auto
IRAN_SHARED_SEED_BATCH_SIZE=50
IRAN_SHARED_RESET_CONFIRM=
OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED=1
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=
PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=
COIN_GROUP_EVENT_CHANNEL_ID=
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID=
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID=
COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID=

# --- Healthcheck ---
IRAN_HEALTHCHECK_URL=https://$iran_app_domain/api/config
IRAN_LOCAL_API_URL=http://127.0.0.1:8000/api/config
EOF

    chmod 600 "$MANIFEST_PATH" || true
    log "Created manifest at $MANIFEST_PATH"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --manifest)
                [[ $# -ge 2 ]] || die "--manifest requires a path"
                MANIFEST_PATH="$2"
                shift 2
                ;;
            -h|--help|help)
                COMMAND="help"
                shift
                ;;
            *)
                if [[ -z "$COMMAND" ]]; then
                    COMMAND="$1"
                    shift
                else
                    die "Unexpected argument: $1"
                fi
                ;;
        esac
    done

    [[ -n "$COMMAND" ]] || COMMAND="release"
}

lock_production_compose_project_identity() {
    : "${FOREIGN_COMPOSE_PROJECT_NAME:=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME}"
    [[ "$FOREIGN_COMPOSE_PROJECT_NAME" == "$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" ]] \
        || die "FOREIGN_COMPOSE_PROJECT_NAME must be exactly $PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME for production."
    COMPOSE_PROJECT_NAME="$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME"
    export FOREIGN_COMPOSE_PROJECT_NAME COMPOSE_PROJECT_NAME
}

load_manifest() {
    local env_iran_connectivity_mode="${IRAN_CONNECTIVITY_MODE-}"

    [[ -f "$MANIFEST_PATH" ]] || die "Manifest not found: $MANIFEST_PATH"
    # These identities are release-control values, not ambient process input.
    # Clear inherited values before loading the approved manifest so a caller
    # cannot redirect Compose to a different production project.
    unset COMPOSE_PROJECT_NAME FOREIGN_COMPOSE_PROJECT_NAME \
        PRODUCTION_BACKUP_RECEIPT_PATH PRODUCTION_BACKUP_RECEIPT_SHA256 \
        PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH \
        PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256
    # shellcheck disable=SC1090
    source "$MANIFEST_PATH"

    if [[ -n "$env_iran_connectivity_mode" ]]; then
        IRAN_CONNECTIVITY_MODE="$env_iran_connectivity_mode"
    fi

    : "${LOCAL_PROJECT_DIR:?LOCAL_PROJECT_DIR is required}"
    : "${LOCAL_FRONTEND_DIR:?LOCAL_FRONTEND_DIR is required}"
    : "${LOCAL_DIST_DIR:?LOCAL_DIST_DIR is required}"
    : "${IRAN_HOST:?IRAN_HOST is required}"
    : "${IRAN_SSH_USER:?IRAN_SSH_USER is required}"
    : "${IRAN_SSH_PORT:?IRAN_SSH_PORT is required}"
    : "${IRAN_SSH_AUTH_METHOD:=key}"
    : "${IRAN_SSH_CONNECT_TIMEOUT_SECONDS:=10}"
    : "${IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS:=15}"
    : "${IRAN_SSH_SERVER_ALIVE_COUNT_MAX:=3}"
    : "${IRAN_SSH_COMMAND_TIMEOUT_SECONDS:=900}"
    : "${IRAN_TRANSFER_TIMEOUT_SECONDS:=900}"
    : "${IRAN_PROJECT_DIR:?IRAN_PROJECT_DIR is required}"
    : "${IRAN_DEPLOY_BASE_DIR:?IRAN_DEPLOY_BASE_DIR is required}"
    : "${FOREIGN_TIMEZONE:=UTC}"
    : "${IRAN_TIMEZONE:=UTC}"
    : "${IRAN_APP_DOMAIN:?IRAN_APP_DOMAIN is required}"
    : "${IRAN_CERTBOT_EMAIL:?IRAN_CERTBOT_EMAIL is required}"
    : "${RUNTIME_ENV_SOURCE_PATH:?RUNTIME_ENV_SOURCE_PATH is required}"
    : "${FOREIGN_RUNTIME_ENV_PATH:?FOREIGN_RUNTIME_ENV_PATH is required}"
    : "${IRAN_RUNTIME_ENV_PATH:?IRAN_RUNTIME_ENV_PATH is required}"
    : "${IRAN_PUBLIC_IP:?IRAN_PUBLIC_IP is required}"
    : "${IRAN_PUBLIC_DOMAIN:?IRAN_PUBLIC_DOMAIN is required}"
    : "${FOREIGN_PUBLIC_IP:?FOREIGN_PUBLIC_IP is required}"
    : "${FOREIGN_PUBLIC_DOMAIN:?FOREIGN_PUBLIC_DOMAIN is required}"
    lock_production_compose_project_identity
    FOREIGN_SERVER_URL="${FOREIGN_SERVER_URL:-https://$FOREIGN_PUBLIC_DOMAIN}"
    FOREIGN_SERVER_DOMAIN="${FOREIGN_SERVER_DOMAIN:-$FOREIGN_PUBLIC_DOMAIN}"
    IRAN_SERVER_URL="${IRAN_SERVER_URL:-https://$IRAN_APP_DOMAIN}"
    IRAN_SERVER_DOMAIN="${IRAN_SERVER_DOMAIN:-$IRAN_APP_DOMAIN}"
    FOREIGN_FRONTEND_URL="${FOREIGN_FRONTEND_URL:-https://$FOREIGN_PUBLIC_DOMAIN}"
    IRAN_FRONTEND_URL="${IRAN_FRONTEND_URL:-https://$IRAN_APP_DOMAIN}"
    PUBLIC_WEBAPP_URL="${PUBLIC_WEBAPP_URL:-$IRAN_FRONTEND_URL}"
    FOREIGN_SERVER_ALIASES="${FOREIGN_SERVER_ALIASES:-$FOREIGN_SERVER_DOMAIN}"
    IRAN_SERVER_ALIASES="${IRAN_SERVER_ALIASES:-$IRAN_SERVER_DOMAIN}"

    IRAN_SKIP_CERTBOT="${IRAN_SKIP_CERTBOT:-0}"
    IRAN_SKIP_FRONTEND_BUILD="${IRAN_SKIP_FRONTEND_BUILD:-0}"
    IRAN_DEPLOY_WITH_WAIT="${IRAN_DEPLOY_WITH_WAIT:-1}"
    IRAN_RUN_POST_DEPLOY_HEALTHCHECK="${IRAN_RUN_POST_DEPLOY_HEALTHCHECK:-1}"
    IRAN_ENABLE_UFW="${IRAN_ENABLE_UFW:-0}"
    IRAN_CONNECTIVITY_MODE="${IRAN_CONNECTIVITY_MODE:-ask}"
    IRAN_SKIP_FOREIGN_DEPLOY="${IRAN_SKIP_FOREIGN_DEPLOY:-0}"
    IRAN_HEALTHCHECK_URL="${IRAN_HEALTHCHECK_URL:-https://$IRAN_APP_DOMAIN/api/config}"
    IRAN_LOCAL_API_URL="${IRAN_LOCAL_API_URL:-http://127.0.0.1:8000/api/config}"
    IRAN_SSH_AUTH_METHOD="${IRAN_SSH_AUTH_METHOD,,}"
    IRAN_HOSTS_SYNC_ENABLED="${IRAN_HOSTS_SYNC_ENABLED:-1}"
    IRAN_FORCE_RELEASE_REFRESH="${IRAN_FORCE_RELEASE_REFRESH:-0}"
    IRAN_ALLOW_DIRTY_RELEASE="${IRAN_ALLOW_DIRTY_RELEASE:-0}"
    PRODUCTION_RELEASE_BRANCH="${PRODUCTION_RELEASE_BRANCH:-main}"
    IRAN_ALLOW_NON_MAIN_RELEASE="${IRAN_ALLOW_NON_MAIN_RELEASE:-0}"
    IRAN_ALLOW_RELEASE_BRANCH_DRIFT="${IRAN_ALLOW_RELEASE_BRANCH_DRIFT:-0}"
    ALLOW_PROJECT_ENV_SOURCE="${ALLOW_PROJECT_ENV_SOURCE:-0}"
    REQUIRE_WEB_PUSH="${REQUIRE_WEB_PUSH:-0}"
    REQUIRE_OFFER_EXPIRY_COMMAND_RECEIPTS="${REQUIRE_OFFER_EXPIRY_COMMAND_RECEIPTS:-1}"
    ENV_BACKUP_DIR="${ENV_BACKUP_DIR:-/root/secure-envs/trading-bot/backups}"
    IRAN_SHARED_DATA_MODE="${IRAN_SHARED_DATA_MODE:-auto}"
    IRAN_SHARED_SEED_BATCH_SIZE="${IRAN_SHARED_SEED_BATCH_SIZE:-50}"
    IRAN_SHARED_RESET_CONFIRM="${IRAN_SHARED_RESET_CONFIRM:-}"
    PRODUCTION_COIN_INFERENCE_RELAY_ENABLED="${PRODUCTION_COIN_INFERENCE_RELAY_ENABLED:-0}"
    PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM="${PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM:-}"
    PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM="${PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM:-}"
    PRODUCTION_COIN_INFERENCE_SOURCE_ROOT="${PRODUCTION_COIN_INFERENCE_SOURCE_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-gold-live}"
    PRODUCTION_COIN_INFERENCE_SOURCE_STORE="${PRODUCTION_COIN_INFERENCE_SOURCE_STORE:-$PRODUCTION_COIN_INFERENCE_SOURCE_ROOT/market/market.sqlite3}"
    PRODUCTION_COIN_INFERENCE_ESTIMATOR_ROOT="${PRODUCTION_COIN_INFERENCE_ESTIMATOR_ROOT:-/srv/trading-bot/production-data/coin-intelligence/estimator-live}"
    COIN_GROUP_EVENT_CHANNEL_ID="${COIN_GROUP_EVENT_CHANNEL_ID:-}"
    COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID="${COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID:-}"
    COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID="${COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID:-}"
    COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID="${COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID:-}"
    PRODUCTION_BACKUP_RECEIPT_PATH="${PRODUCTION_BACKUP_RECEIPT_PATH:-}"
    PRODUCTION_BACKUP_RECEIPT_SHA256="${PRODUCTION_BACKUP_RECEIPT_SHA256:-}"
    PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH="${PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH:-}"
    PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256="${PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256:-}"

    # Docker Compose interpolation needs these manifest values exported, but
    # they must not be copied into the application runtime env.
    export IRAN_PUBLIC_IP IRAN_PUBLIC_DOMAIN

    if [[ "$FOREIGN_TIMEZONE" != "UTC" || "$IRAN_TIMEZONE" != "UTC" ]]; then
        log "Overriding configured timezones to UTC for production release."
        FOREIGN_TIMEZONE="UTC"
        IRAN_TIMEZONE="UTC"
    fi

    [[ -d "$LOCAL_PROJECT_DIR" ]] || die "LOCAL_PROJECT_DIR does not exist: $LOCAL_PROJECT_DIR"
    [[ -d "$LOCAL_FRONTEND_DIR" ]] || die "LOCAL_FRONTEND_DIR does not exist: $LOCAL_FRONTEND_DIR"

    configure_iran_transport
    RELEASE_TMP_DIR="$LOCAL_PROJECT_DIR/tmp/production-release"
    RELEASE_ARTIFACT_DIR="$RELEASE_TMP_DIR/artifacts"
    REMOTE_IMAGE_BUNDLE="$IRAN_DEPLOY_BASE_DIR/releases/trading-bot-images.tar"
    REMOTE_IMAGE_BUNDLE_SHA="$REMOTE_IMAGE_BUNDLE.sha256"
    REMOTE_RELEASE_STATE_DIR="$IRAN_DEPLOY_BASE_DIR/releases/state"
    REMOTE_IMAGE_LOADED_SIGNATURE="$REMOTE_RELEASE_STATE_DIR/docker-images.loaded.signature"
    LOCAL_IMAGE_BUNDLE="$RELEASE_TMP_DIR/docker-images.tar"
    LOCAL_IMAGE_SIGNATURE_FILE="$RELEASE_TMP_DIR/docker-images.signature"
    LOCAL_FRONTEND_SIGNATURE_FILE="$RELEASE_TMP_DIR/frontend-build.signature"
    LOCAL_IRAN_SOURCE_PAYLOAD_DIR="$RELEASE_TMP_DIR/iran-source-payload"
    LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST="$RELEASE_TMP_DIR/iran-source-payload.sha256"
    REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST="$REMOTE_RELEASE_STATE_DIR/iran-source-payload.sha256"
}

ssh_iran() {
    "${SSH_IRAN_CMD[@]}" "$IRAN_SSH_TARGET" "$@"
}

scp_iran() {
    "${SCP_IRAN_CMD[@]}" "$@"
}

run_iran_transfer() {
    timeout --signal=TERM --kill-after=15s "${IRAN_TRANSFER_TIMEOUT_SECONDS}s" "$@"
}

read_env_value() {
    local env_path="$1"
    local key="$2"
    local line
    line="$(grep -E "^${key}=" "$env_path" | tail -n 1 || true)"
    printf '%s' "${line#*=}"
}

production_runtime_source_profile() {
    PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
        'import sys; from pathlib import Path; from scripts.deploy_config import parse_env_file; from scripts.plan_telegram_delivery_queue_production import source_profile; print(source_profile(parse_env_file(Path(sys.argv[1]))))' \
        "$RUNTIME_ENV_SOURCE_PATH"
}

ensure_production_release_lock_directory() {
    if [[ ! -e "$PRODUCTION_RELEASE_LOCK_DIR" ]]; then
        install -d -m 700 "$PRODUCTION_RELEASE_LOCK_DIR"
    fi
    [[ -d "$PRODUCTION_RELEASE_LOCK_DIR" && ! -L "$PRODUCTION_RELEASE_LOCK_DIR" ]] \
        || die "Production release lock directory is not a secure directory."
    [[ "$(stat -c '%u' "$PRODUCTION_RELEASE_LOCK_DIR")" == "$(id -u)" ]] \
        || die "Production release lock directory has the wrong owner."
    [[ "$(stat -c '%a' "$PRODUCTION_RELEASE_LOCK_DIR")" == "700" ]] \
        || die "Production release lock directory must have mode 0700."
}

release_production_operation_lock() {
    if [[ "$PRODUCTION_RELEASE_LOCK_OWNED" == "1" ]]; then
        [[ -f "$PRODUCTION_RELEASE_LOCK_PATH" && ! -L "$PRODUCTION_RELEASE_LOCK_PATH" ]] \
            || die "Production release lock disappeared during the operation."
        unlink "$PRODUCTION_RELEASE_LOCK_PATH"
        PRODUCTION_RELEASE_LOCK_OWNED=0
    fi
}

prepare_production_source_lock() {
    validate_secure_runtime_env_source_file \
        || die "Immutable production runtime env source is not secure enough to lock."
    PRODUCTION_SOURCE_LOCK_PATH="$(dirname "$RUNTIME_ENV_SOURCE_PATH")/.production-runtime-source.lock"
    if [[ ! -e "$PRODUCTION_SOURCE_LOCK_PATH" ]]; then
        (set -o noclobber; umask 077; : >"$PRODUCTION_SOURCE_LOCK_PATH") 2>/dev/null || true
    fi
    [[ -f "$PRODUCTION_SOURCE_LOCK_PATH" && ! -L "$PRODUCTION_SOURCE_LOCK_PATH" ]] \
        || die "Immutable production source lock is not a regular non-symlink file."
    [[ "$(stat -c '%u' "$PRODUCTION_SOURCE_LOCK_PATH")" == "$(id -u)" \
        && "$(stat -c '%a' "$PRODUCTION_SOURCE_LOCK_PATH")" == "600" \
        && "$(stat -c '%h' "$PRODUCTION_SOURCE_LOCK_PATH")" == "1" ]] \
        || die "Immutable production source lock has unsafe ownership, mode, or link count."
}

acquire_production_source_lock() {
    command -v flock >/dev/null 2>&1 || die "Missing required command: flock"
    prepare_production_source_lock
    exec {PRODUCTION_SOURCE_LOCK_FD}<>"$PRODUCTION_SOURCE_LOCK_PATH"
    if ! flock -n "$PRODUCTION_SOURCE_LOCK_FD"; then
        exec {PRODUCTION_SOURCE_LOCK_FD}>&-
        PRODUCTION_SOURCE_LOCK_FD=""
        die "Another approved operation is updating the immutable production source."
    fi
    PRODUCTION_SOURCE_LOCK_OWNED=1
}

verify_inherited_production_source_lock() {
    command -v flock >/dev/null 2>&1 || die "Missing required command: flock"
    prepare_production_source_lock
    local probe_fd
    exec {probe_fd}<>"$PRODUCTION_SOURCE_LOCK_PATH"
    if flock -n "$probe_fd"; then
        flock -u "$probe_fd" >/dev/null 2>&1 || true
        exec {probe_fd}>&-
        die "Queue deploy authority is not backed by the cutover-held immutable source lock."
    fi
    exec {probe_fd}>&-
    PRODUCTION_SOURCE_LOCK_INHERITED_OBSERVED=1
}

release_production_source_lock() {
    if [[ "$PRODUCTION_SOURCE_LOCK_OWNED" == "1" && -n "$PRODUCTION_SOURCE_LOCK_FD" ]]; then
        flock -u "$PRODUCTION_SOURCE_LOCK_FD"
        exec {PRODUCTION_SOURCE_LOCK_FD}>&-
        PRODUCTION_SOURCE_LOCK_FD=""
        PRODUCTION_SOURCE_LOCK_OWNED=0
    fi
    PRODUCTION_SOURCE_LOCK_INHERITED_OBSERVED=0
}

release_production_locks() {
    release_production_source_lock || true
    release_production_operation_lock || true
}

acquire_production_operation_lock() {
    ensure_production_release_lock_directory
    if (set -o noclobber; umask 077; printf '{"environment":"production","owner":"production-deploy"}\n' > "$PRODUCTION_RELEASE_LOCK_PATH") 2>/dev/null; then
        chmod 600 "$PRODUCTION_RELEASE_LOCK_PATH"
        PRODUCTION_RELEASE_LOCK_OWNED=1
        return 0
    fi
    die "Another production release or Queue cutover is active, or requires manual recovery review."
}

verify_queue_cutover_deploy_authority() {
    local authority_path="${TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT:-}"
    local authority_digest="${TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT_SHA256:-}"
    [[ -n "$authority_path" && -n "$authority_digest" ]] \
        || die "A guarded Queue cutover authority receipt is required."
    [[ -f "$PRODUCTION_RELEASE_LOCK_PATH" && ! -L "$PRODUCTION_RELEASE_LOCK_PATH" ]] \
        || die "The guarded production release lock is not active."
    python3 "$TELEGRAM_QUEUE_PRODUCTION_CUTOVER_SCRIPT" verify-deploy-authority \
        --manifest "$MANIFEST_PATH" \
        --deploy-authority "$authority_path" \
        --deploy-authority-sha256 "$authority_digest" >/dev/null \
        || die "The guarded Queue cutover authority receipt is invalid."
    # A Queue cutover changes the immutable runtime source profile while the
    # Git release stays fixed.  The ordinary release deliberately reuses
    # evidence prepared for the current profile, but that evidence is stale
    # across this Legacy <-> Queue ownership transition.  Only a consumed,
    # source-lock-bound cutover authority may opt the full release into a
    # same-source rebuild before any production writer transaction starts.
    PRODUCTION_QUEUE_CUTOVER_REBUILD_EVIDENCE=1
}

guard_production_release_command() {
    local mutating=0 profile=""
    case "$COMMAND" in
        release|prepare-release-evidence|verify-release-evidence|deploy-foreign|bootstrap-iran|configure-nginx|issue-cert|build-release|sync-project|ship-images|load-images|deploy-iran|seed-shared-data)
            mutating=1
            ;;
    esac
    [[ "$mutating" == "1" ]] || return 0
    case "$COMMAND" in
        deploy-foreign|deploy-iran|sync-project|ship-images|load-images|seed-shared-data)
            die "This production mutation is internal to the full two-host release choreography and cannot run directly."
            ;;
    esac
    if [[ "$COMMAND" != "release" \
        && ( -e "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" \
            || -L "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" ) ]]; then
        die "An incomplete two-host production release must be reconciled by rerunning the full release command."
    fi
    if [[ -n "${TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT:-}" \
        || -n "${TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT_SHA256:-}" ]]; then
        [[ "$COMMAND" == "release" ]] \
            || die "A guarded Queue authority may only execute the full production release."
        [[ "${PRODUCTION_SOURCE_LOCK_INHERITED_CONFIRM:-}" == "verified-cutover-held-lock" ]] \
            || die "Queue deploy authority must explicitly declare the cutover-held immutable source lock."
        verify_queue_cutover_deploy_authority
        verify_inherited_production_source_lock
        profile="$(production_runtime_source_profile)" \
            || die "Immutable production source has an invalid Telegram execution profile."
        return 0
    fi
    acquire_production_operation_lock
    acquire_production_source_lock
    profile="$(production_runtime_source_profile)" \
        || die "Immutable production source has an invalid Telegram execution profile."
    if [[ "$profile" == "queue-v1" ]]; then
        case "$COMMAND" in
            prepare-release-evidence|verify-release-evidence)
                # These commands build or validate immutable artifacts only.
                # They never quiesce writers, replace containers, mutate a
                # database, or call Telegram.  A target release must be able
                # to create its exact image/rehearsal evidence before the
                # cutover-owned full release can authorize Queue redeploy.
                return 0
                ;;
            *)
                die "Queue-v1 production deploys require the guarded cutover authority and full release command."
                ;;
        esac
    fi
}

file_sha256() {
    sha256sum "$1" | awk '{print $1}'
}

directory_sha256() {
    local directory="$1"
    [[ -d "$directory" && ! -L "$directory" ]] \
        || die "Release directory is missing or is a symlink: $directory"
    (
        cd "$directory"
        find . -type f -print0 | LC_ALL=C sort -z | xargs -r -0 sha256sum
    ) | sha256sum | awk '{print $1}'
}

validate_remote_shell_path() {
    local value="$1"
    local label="$2"
    [[ "$value" == /* ]] || die "$label must be an absolute path."
    [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "$label contains unsupported shell characters."
}

render_shell_command() {
    local rendered=""
    printf -v rendered '%q ' "$@"
    printf '%s' "${rendered% }"
}

configure_iran_transport() {
    : "${IRAN_SSH_CONNECT_TIMEOUT_SECONDS:=10}"
    : "${IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS:=15}"
    : "${IRAN_SSH_SERVER_ALIVE_COUNT_MAX:=3}"
    : "${IRAN_SSH_COMMAND_TIMEOUT_SECONDS:=900}"
    : "${IRAN_TRANSFER_TIMEOUT_SECONDS:=900}"
    [[ "$IRAN_SSH_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( IRAN_SSH_PORT <= 65535 )) \
        || die "IRAN_SSH_PORT must be between 1 and 65535."
    [[ "$IRAN_SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,31}$ ]] \
        || die "IRAN_SSH_USER contains unsupported characters."
    [[ "$IRAN_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ \
        && "$IRAN_HOST" != *".."* && "$IRAN_HOST" != *"%"* ]] \
        || die "IRAN_HOST contains unsupported characters."
    local setting
    for setting in \
        IRAN_SSH_CONNECT_TIMEOUT_SECONDS \
        IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS \
        IRAN_SSH_SERVER_ALIVE_COUNT_MAX \
        IRAN_SSH_COMMAND_TIMEOUT_SECONDS \
        IRAN_TRANSFER_TIMEOUT_SECONDS
    do
        [[ "${!setting}" =~ ^[1-9][0-9]*$ ]] \
            || die "$setting must be a positive integer."
    done
    (( IRAN_SSH_CONNECT_TIMEOUT_SECONDS <= 60 \
        && IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS <= 60 \
        && IRAN_SSH_SERVER_ALIVE_COUNT_MAX <= 10 \
        && IRAN_SSH_COMMAND_TIMEOUT_SECONDS >= 60 \
        && IRAN_SSH_COMMAND_TIMEOUT_SECONDS <= 3600 \
        && IRAN_TRANSFER_TIMEOUT_SECONDS >= 60 \
        && IRAN_TRANSFER_TIMEOUT_SECONDS <= 3600 )) \
        || die "Iran SSH timeout settings are outside the supported production bounds."
    need_cmd timeout
    local -a ssh_common_options=(
        -o StrictHostKeyChecking=accept-new
        -o "ConnectTimeout=$IRAN_SSH_CONNECT_TIMEOUT_SECONDS"
        -o "ServerAliveInterval=$IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS"
        -o "ServerAliveCountMax=$IRAN_SSH_SERVER_ALIVE_COUNT_MAX"
        -o ConnectionAttempts=1
    )
    IRAN_SSH_TARGET="$IRAN_SSH_USER@$IRAN_HOST"
    case "$IRAN_SSH_AUTH_METHOD" in
        key)
            SSH_IRAN_CMD=(
                timeout --signal=TERM --kill-after=15s "${IRAN_SSH_COMMAND_TIMEOUT_SECONDS}s"
                ssh -p "$IRAN_SSH_PORT" "${ssh_common_options[@]}"
                -o BatchMode=yes -o PasswordAuthentication=no
                -o KbdInteractiveAuthentication=no -o IdentitiesOnly=yes
            )
            SCP_IRAN_CMD=(
                timeout --signal=TERM --kill-after=15s "${IRAN_TRANSFER_TIMEOUT_SECONDS}s"
                scp -P "$IRAN_SSH_PORT" "${ssh_common_options[@]}"
                -o BatchMode=yes -o PasswordAuthentication=no
                -o KbdInteractiveAuthentication=no -o IdentitiesOnly=yes
            )
            RSYNC_RSH_CMD=(
                ssh -p "$IRAN_SSH_PORT" "${ssh_common_options[@]}"
                -o BatchMode=yes -o PasswordAuthentication=no
                -o KbdInteractiveAuthentication=no -o IdentitiesOnly=yes
            )
            if [[ -n "${IRAN_SSH_PRIVATE_KEY_PATH:-}" ]]; then
                validate_remote_shell_path "$IRAN_SSH_PRIVATE_KEY_PATH" "IRAN_SSH_PRIVATE_KEY_PATH"
                [[ -f "$IRAN_SSH_PRIVATE_KEY_PATH" && ! -L "$IRAN_SSH_PRIVATE_KEY_PATH" ]] \
                    || die "IRAN_SSH_PRIVATE_KEY_PATH must be a regular non-symlink file."
                [[ "$(stat -c '%u' "$IRAN_SSH_PRIVATE_KEY_PATH")" == "$(id -u)" ]] \
                    || die "IRAN_SSH_PRIVATE_KEY_PATH must be owned by the release user."
                case "$(stat -c '%a' "$IRAN_SSH_PRIVATE_KEY_PATH")" in
                    400|600) ;;
                    *) die "IRAN_SSH_PRIVATE_KEY_PATH permissions must be 0400 or 0600." ;;
                esac
                SSH_IRAN_CMD+=(-i "$IRAN_SSH_PRIVATE_KEY_PATH")
                SCP_IRAN_CMD+=(-i "$IRAN_SSH_PRIVATE_KEY_PATH")
                RSYNC_RSH_CMD+=(-i "$IRAN_SSH_PRIVATE_KEY_PATH")
            else
                log "IRAN_SSH_PRIVATE_KEY_PATH is empty; using SSH agent/default keys for Iran access."
            fi
            unset SSHPASS || true
            ;;
        password)
            : "${IRAN_SSH_PASSWORD:?IRAN_SSH_PASSWORD is required for password auth}"
            need_cmd sshpass
            SSHPASS="$IRAN_SSH_PASSWORD"
            export SSHPASS
            SSH_IRAN_CMD=(
                timeout --signal=TERM --kill-after=15s "${IRAN_SSH_COMMAND_TIMEOUT_SECONDS}s"
                sshpass -e ssh -p "$IRAN_SSH_PORT" "${ssh_common_options[@]}"
            )
            SCP_IRAN_CMD=(
                timeout --signal=TERM --kill-after=15s "${IRAN_TRANSFER_TIMEOUT_SECONDS}s"
                sshpass -e scp -P "$IRAN_SSH_PORT" "${ssh_common_options[@]}"
            )
            RSYNC_RSH_CMD=(sshpass -e ssh -p "$IRAN_SSH_PORT" "${ssh_common_options[@]}")
            ;;
        *)
            die "Unsupported IRAN_SSH_AUTH_METHOD: $IRAN_SSH_AUTH_METHOD"
            ;;
    esac
    RSYNC_SSH="$(render_shell_command "${RSYNC_RSH_CMD[@]}")"
}

write_runtime_env_install_receipt() {
    local role="$1"
    local expected_sha256="$2"
    local installed_sha256="$3"
    local backup_sha256="$4"
    local backup_path="$5"
    local receipt_path="$RELEASE_ARTIFACT_DIR/runtime-env-install-${role}.json"

    install -d -m 0700 -- "$RELEASE_ARTIFACT_DIR"
    python3 - "$receipt_path" "$role" "$expected_sha256" "$installed_sha256" "$backup_sha256" "$backup_path" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

receipt_path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "role": sys.argv[2],
    "installed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "expected_sha256": sys.argv[3],
    "installed_sha256": sys.argv[4],
    "previous_backup_sha256": None if sys.argv[5] == "none" else sys.argv[5],
    "previous_backup_path": None if sys.argv[6] == "none" else sys.argv[6],
    "secret_values_retained": False,
}
descriptor, temporary_name = tempfile.mkstemp(
    prefix=f".{receipt_path.name}.", suffix=".tmp", dir=receipt_path.parent
)
temporary_path = Path(temporary_name)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, receipt_path)
    directory_descriptor = os.open(receipt_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
finally:
    temporary_path.unlink(missing_ok=True)
PY
    log "Runtime env install receipt role=$role sha256=$installed_sha256 receipt=$receipt_path"
}

fsync_file_and_parent() {
    local path="$1"
    python3 - "$path" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
descriptor = os.open(path, os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
directory_descriptor = os.open(path.parent, os.O_RDONLY)
try:
    os.fsync(directory_descriptor)
finally:
    os.close(directory_descriptor)
PY
}

atomic_promote_local_file() {
    local candidate="$1"
    local destination="$2"
    python3 - "$candidate" "$destination" <<'PY'
import os
from pathlib import Path
import sys

candidate = Path(sys.argv[1])
destination = Path(sys.argv[2])
if candidate.parent.resolve() != destination.parent.resolve():
    raise SystemExit("atomic install candidate must be beside destination")
descriptor = os.open(candidate, os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.chmod(candidate, 0o600)
os.replace(candidate, destination)
directory_descriptor = os.open(destination.parent, os.O_RDONLY)
try:
    os.fsync(directory_descriptor)
finally:
    os.close(directory_descriptor)
PY
}

atomic_install_local_runtime_env() (
    set -euo pipefail
    local source_path="$1"
    local destination_path="$2"
    local role="$3"
    local destination_dir destination_name candidate expected_sha256 candidate_sha256
    local backup_dir backup_path="none" backup_sha256="none" installed_sha256

    [[ -f "$source_path" && ! -L "$source_path" ]] || die "Rendered $role runtime env is not a regular file."
    destination_dir="$(dirname "$destination_path")"
    destination_name="$(basename "$destination_path")"
    if [[ ! -d "$destination_dir" ]]; then
        install -d -m 0700 -- "$destination_dir"
    fi
    validate_secure_env_directory "$destination_dir" \
        || die "Live runtime env destination directory is not secure."
    expected_sha256="$(file_sha256 "$source_path")"

    [[ "$(canonical_path "$source_path")" != "$(canonical_path "$destination_path")" ]] \
        || die "Rendered $role runtime env must be separate from its live destination."

    candidate="$(mktemp "$destination_dir/.${destination_name}.install.XXXXXX")"
    trap 'rm -f -- "$candidate"' EXIT
    install -m 0600 -- "$source_path" "$candidate"
    candidate_sha256="$(file_sha256 "$candidate")"
    [[ "$candidate_sha256" == "$expected_sha256" ]] || die "$role runtime env candidate digest mismatch."
    fsync_file_and_parent "$candidate"

    if [[ -e "$destination_path" ]]; then
        [[ -f "$destination_path" && ! -L "$destination_path" ]] || die "Existing $role runtime env destination is not a regular file."
        backup_dir="$ENV_BACKUP_DIR/live-runtime-env"
        install -d -m 0700 -- "$backup_dir"
        backup_path="$(mktemp "$backup_dir/${role}-before-install.XXXXXX")"
        install -m 0600 -- "$destination_path" "$backup_path"
        fsync_file_and_parent "$backup_path"
        backup_sha256="$(file_sha256 "$backup_path")"
        [[ "$backup_sha256" == "$(file_sha256 "$destination_path")" ]] || die "$role runtime env backup digest mismatch."
    fi

    atomic_promote_local_file "$candidate" "$destination_path"
    installed_sha256="$(file_sha256 "$destination_path")"
    [[ "$installed_sha256" == "$expected_sha256" ]] || die "$role runtime env digest mismatch after atomic install."
    write_runtime_env_install_receipt "$role" "$expected_sha256" "$installed_sha256" "$backup_sha256" "$backup_path"
)

atomic_install_iran_runtime_env() {
    local source_path="$IRAN_RUNTIME_ENV_PATH"
    local destination_path="$IRAN_PROJECT_DIR/.env"
    local destination_dir="$IRAN_PROJECT_DIR"
    local remote_backup_dir="$IRAN_DEPLOY_BASE_DIR/secure-env-backups/runtime-env"
    local expected_sha256 remote_candidate remote_result installed_sha256 backup_sha256 backup_path

    [[ -f "$source_path" && ! -L "$source_path" ]] || die "Rendered Iran runtime env is not a regular file."
    validate_remote_shell_path "$destination_dir" "IRAN_PROJECT_DIR"
    validate_remote_shell_path "$remote_backup_dir" "Iran runtime env backup directory"
    expected_sha256="$(file_sha256 "$source_path")"
    remote_candidate="$(ssh_iran "set -euo pipefail
if [ ! -d '$destination_dir' ]; then install -d -m 0700 -- '$destination_dir'; fi
[ -d '$destination_dir' ] && [ ! -L '$destination_dir' ] || exit 27
[ \"\$(stat -c '%u' '$destination_dir')\" = '0' ] || exit 28
destination_mode=\"\$(stat -c '%a' '$destination_dir')\"
[ \$((8#\$destination_mode & 022)) -eq 0 ] || exit 29
umask 077
mktemp '$destination_dir/.env.install.XXXXXX'")"
    [[ "$remote_candidate" =~ ^${destination_dir//./\.}/\.env\.install\.[A-Za-z0-9]+$ ]] \
        || die "Iran runtime env candidate path was not safely allocated beside the destination."

    if ! scp_iran "$source_path" "$IRAN_SSH_TARGET:$remote_candidate"; then
        ssh_iran "rm -f -- '$remote_candidate'" >/dev/null 2>&1 || true
        die "Failed to transfer the Iran runtime env candidate."
    fi

    if ! remote_result="$(ssh_iran "set -euo pipefail
candidate='$remote_candidate'
destination='$destination_path'
backup_dir='$remote_backup_dir'
expected_sha256='$expected_sha256'
actual_sha256=\"\$(sha256sum \"\$candidate\" | awk '{print \$1}')\"
[ \"\$actual_sha256\" = \"\$expected_sha256\" ] || exit 31
chmod 600 -- \"\$candidate\"
backup_path='none'
backup_sha256='none'
if [ -e \"\$destination\" ]; then
  [ -f \"\$destination\" ] && [ ! -L \"\$destination\" ] || exit 32
  install -d -m 0700 -- \"\$backup_dir\"
  backup_path=\"\$(mktemp \"\$backup_dir/iran-before-install.XXXXXX\")\"
  install -m 0600 -- \"\$destination\" \"\$backup_path\"
  backup_sha256=\"\$(sha256sum \"\$backup_path\" | awk '{print \$1}')\"
  [ \"\$backup_sha256\" = \"\$(sha256sum \"\$destination\" | awk '{print \$1}')\" ] || exit 33
  sync -f \"\$backup_path\"
fi
python3 -c 'import os,sys; candidate,destination=sys.argv[1:3]; descriptor=os.open(candidate,os.O_RDONLY); os.fsync(descriptor); os.close(descriptor); os.chmod(candidate,0o600); os.replace(candidate,destination); directory=os.open(os.path.dirname(destination),os.O_RDONLY); os.fsync(directory); os.close(directory)' \"\$candidate\" \"\$destination\"
installed_sha256=\"\$(sha256sum \"\$destination\" | awk '{print \$1}')\"
[ \"\$installed_sha256\" = \"\$expected_sha256\" ] || exit 34
printf 'installed_sha256=%s\\nbackup_sha256=%s\\nbackup_path=%s\\n' \"\$installed_sha256\" \"\$backup_sha256\" \"\$backup_path\"")"; then
        ssh_iran "rm -f -- '$remote_candidate'" >/dev/null 2>&1 || true
        die "Iran runtime env atomic install failed."
    fi

    installed_sha256="$(printf '%s\n' "$remote_result" | sed -n 's/^installed_sha256=//p')"
    backup_sha256="$(printf '%s\n' "$remote_result" | sed -n 's/^backup_sha256=//p')"
    backup_path="$(printf '%s\n' "$remote_result" | sed -n 's/^backup_path=//p')"
    [[ "$installed_sha256" == "$expected_sha256" ]] || die "Iran runtime env installed digest did not match the rendered source."
    [[ "$backup_sha256" == "none" || "$backup_sha256" =~ ^[0-9a-f]{64}$ ]] || die "Iran runtime env backup receipt was invalid."
    [[ "$backup_path" == "none" || "$backup_path" =~ ^${remote_backup_dir//./\.}/iran-before-install\.[A-Za-z0-9]+$ ]] \
        || die "Iran runtime env backup path was invalid."
    write_runtime_env_install_receipt "iran" "$expected_sha256" "$installed_sha256" "$backup_sha256" "$backup_path"
}

validate_production_coin_runtime_dir() {
    local supplied_path="$1"
    local role_label="$2"
    local canonical

    [[ "$supplied_path" == /* ]] || die "$role_label coin Snapshot runtime directory must be absolute."
    [[ "$supplied_path" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "$role_label coin Snapshot runtime directory contains unsupported characters."
    if ! canonical="$(python3 - "$supplied_path" <<'PY'
from pathlib import Path
import sys

supplied = Path(sys.argv[1])
resolved = supplied.resolve(strict=False)
if supplied != resolved:
    raise SystemExit(2)
parts = [part.lower() for part in resolved.parts]
if resolved == Path("/") or not any("production" in part for part in parts):
    raise SystemExit(3)
if any("staging" in part for part in parts):
    raise SystemExit(4)
print(resolved)
PY
)"; then
        die "$role_label coin Snapshot runtime directory must be canonical, production-scoped, and outside every staging path."
    fi
    printf '%s\n' "$canonical"
}

resolve_production_coin_runtime_contract() {
    local foreign_raw iran_raw foreign_age iran_age
    local foreign_container_dir iran_container_dir foreign_snapshot_path iran_snapshot_path
    foreign_raw="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR")"
    iran_raw="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR")"
    [[ -n "$foreign_raw" && -n "$iran_raw" ]] || die "Production coin Snapshot host directory is missing from a rendered runtime env."
    FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR="$(validate_production_coin_runtime_dir "$foreign_raw" "Foreign")"
    IRAN_COIN_SNAPSHOT_RUNTIME_DIR="$(validate_production_coin_runtime_dir "$iran_raw" "Iran")"

    foreign_age="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS")"
    iran_age="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS")"
    [[ "$foreign_age" == "120" && "$iran_age" == "$foreign_age" ]] \
        || die "Production coin Snapshot maximum age must be exactly 120 seconds on both roles."
    PRODUCTION_COIN_SNAPSHOT_MAXIMUM_AGE_SECONDS="$foreign_age"
    foreign_container_dir="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR")"
    iran_container_dir="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR")"
    foreign_snapshot_path="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH")"
    iran_snapshot_path="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH")"
    [[ "$foreign_container_dir" == "/app/runtime/coin-inference" \
        && "$iran_container_dir" == "$foreign_container_dir" ]] \
        || die "Production coin Snapshot container directory must be the exact canonical path on both roles."
    [[ "$foreign_snapshot_path" == "/app/runtime/coin-inference/coin-rates.json" \
        && "$iran_snapshot_path" == "$foreign_snapshot_path" ]] \
        || die "Production coin Snapshot container file path must be the exact canonical path on both roles."
    FOREIGN_COIN_SNAPSHOT_PATH="$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR/coin-rates.json"
    IRAN_COIN_SNAPSHOT_PATH="$IRAN_COIN_SNAPSHOT_RUNTIME_DIR/coin-rates.json"
}

ensure_local_production_coin_runtime_dir() {
    resolve_production_coin_runtime_contract
    install -d -m 0755 -- "$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR"
    [[ -d "$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR" && ! -L "$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR" ]] \
        || die "Foreign production coin Snapshot runtime directory is not a regular directory."
    [[ "$(canonical_path "$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR")" == "$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR" ]] \
        || die "Foreign production coin Snapshot runtime directory changed identity during installation."
}

ensure_remote_production_coin_runtime_dir() {
    local require_installed_env_match="${1:-0}"
    local installed_runtime_dir=""
    resolve_production_coin_runtime_contract
    validate_remote_shell_path "$IRAN_COIN_SNAPSHOT_RUNTIME_DIR" "Iran production coin Snapshot runtime directory"
    if [[ "$require_installed_env_match" == "1" ]]; then
        validate_remote_shell_path "$IRAN_PROJECT_DIR" "IRAN_PROJECT_DIR"
        installed_runtime_dir="$(ssh_iran "test -f '$IRAN_PROJECT_DIR/.env' && grep -E '^PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR=' '$IRAN_PROJECT_DIR/.env' | tail -n 1 | cut -d= -f2-")"
        [[ "$installed_runtime_dir" == "$IRAN_COIN_SNAPSHOT_RUNTIME_DIR" ]] \
            || die "Installed Iran runtime env does not match the validated production coin Snapshot bind directory. Run sync-project before deploy-iran."
    fi
    ssh_iran "set -euo pipefail
runtime_dir='$IRAN_COIN_SNAPSHOT_RUNTIME_DIR'
resolved=\"\$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' \"\$runtime_dir\")\"
[ \"\$resolved\" = \"\$runtime_dir\" ] || exit 41
install -d -m 0755 -- \"\$runtime_dir\"
[ -d \"\$runtime_dir\" ] && [ ! -L \"\$runtime_dir\" ] || exit 42
resolved=\"\$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' \"\$runtime_dir\")\"
[ \"\$resolved\" = \"\$runtime_dir\" ] || exit 43"
}

validate_production_coin_relay_manifest() {
    case "$PRODUCTION_COIN_INFERENCE_RELAY_ENABLED" in
        0) return 0 ;;
        1) ;;
        *) die "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED must be exactly 0 or 1." ;;
    esac
    [[ "$PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_CONFIRM_TEXT" ]] \
        || die "Production coin Snapshot relay enablement requires the exact manifest confirmation."
    [[ "$IRAN_SSH_AUTH_METHOD" == "key" ]] \
        || die "Production coin Snapshot relay requires non-interactive key authentication; password auth is not supported by systemd."
    [[ -n "${IRAN_SSH_PRIVATE_KEY_PATH:-}" ]] \
        || die "Production coin Snapshot relay requires an explicit identity file for unattended systemd execution."
    validate_remote_shell_path "$IRAN_SSH_PRIVATE_KEY_PATH" "IRAN_SSH_PRIVATE_KEY_PATH"
    [[ "$(canonical_path "$IRAN_SSH_PRIVATE_KEY_PATH")" == "$IRAN_SSH_PRIVATE_KEY_PATH" ]] \
        || die "Production coin Snapshot relay identity file must be canonical."
    [[ -f "$IRAN_SSH_PRIVATE_KEY_PATH" && ! -L "$IRAN_SSH_PRIVATE_KEY_PATH" ]] \
        || die "Production coin Snapshot relay identity file must be a regular non-symlink file."
    [[ "$(stat -c '%u' "$IRAN_SSH_PRIVATE_KEY_PATH")" == "$(id -u)" ]] \
        || die "Production coin Snapshot relay identity file must be owned by the release user."
    case "$(stat -c '%a' "$IRAN_SSH_PRIVATE_KEY_PATH")" in
        400|600) ;;
        *) die "Production coin Snapshot relay identity file permissions must be 0400 or 0600." ;;
    esac
    [[ -f "$PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT" ]] || die "Production coin Snapshot relay script is missing."
    [[ -f "$PRODUCTION_COIN_SNAPSHOT_RELAY_INSTALLER" ]] || die "Production coin Snapshot relay installer is missing."
}

validate_production_coin_inference_activation_contract() {
    local preview selection guard auto_selection value
    preview="$(read_env_value "$RUNTIME_ENV_SOURCE_PATH" "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED")"
    selection="$(read_env_value "$RUNTIME_ENV_SOURCE_PATH" "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED")"
    guard="$(read_env_value "$RUNTIME_ENV_SOURCE_PATH" "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED")"
    auto_selection="$(read_env_value "$RUNTIME_ENV_SOURCE_PATH" "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED")"
    for value in "$preview" "$selection" "$guard" "$auto_selection"; do
        [[ "$value" == "true" || "$value" == "false" ]] \
            || die "Production coin inference flags must be explicit true/false values in the immutable source."
    done
    [[ "$auto_selection" == "false" ]] \
        || die "Production coin inference automatic commodity selection remains forbidden."
    PRODUCTION_COIN_INFERENCE_REQUESTED=0
    if [[ "$preview" == "true" || "$selection" == "true" || "$guard" == "true" ]]; then
        PRODUCTION_COIN_INFERENCE_REQUESTED=1
        [[ "$preview" == "true" && "$selection" == "true" && "$guard" == "true" ]] \
            || die "Live production coin inference requires preview, selection, and price guard to transition together."
        [[ "$PRODUCTION_COIN_INFERENCE_RELAY_ENABLED" == "1" \
            && "$PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_CONFIRM_TEXT" ]] \
            || die "Live production coin inference requires the confirmed production Snapshot relay."
        [[ -f "$PRODUCTION_COIN_INPUT_TIMER_INSTALLER" \
            && -f "$PRODUCTION_COIN_READINESS_SCRIPT" ]] \
            || die "Production coin inference input/readiness tooling is missing."
        [[ "$COIN_GROUP_EVENT_CHANNEL_ID" =~ ^-100[0-9]{8,16}$ \
            && "$COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID" =~ ^-100[0-9]{8,16}$ \
            && "$COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID" =~ ^-100[0-9]{8,16}$ \
            && "$COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID" != "$COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID" \
            && "$COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID" =~ ^[1-9][0-9]{0,15}$ ]] \
            || die "Production coin inference collector identities must be explicitly bound in the manifest."
    fi
}

validate_production_coin_relay_state_file() {
    [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL" ]] \
        || die "Production coin Snapshot relay state file must use the canonical production path."
    validate_remote_shell_path "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" \
        "Production coin Snapshot relay state file"
    local parent canonical_parent
    parent="$(dirname "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE")"
    canonical_parent="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "$parent")"
    [[ "$canonical_parent" == "$parent" ]] \
        || die "Production coin Snapshot relay state directory must be canonical."
    if [[ -e "$parent" ]]; then
        [[ -d "$parent" && ! -L "$parent" \
            && "$(stat -c '%u' "$parent")" == "$(id -u)" \
            && "$(stat -c '%a' "$parent")" == "700" ]] \
            || die "Production coin Snapshot relay state directory must be owner-controlled mode 0700."
    fi
    if [[ -e "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" || -L "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" ]]; then
        [[ -f "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" \
            && ! -L "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" \
            && "$(stat -c '%u' "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE")" == "$(id -u)" \
            && "$(stat -c '%a' "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE")" == "600" \
            && "$(stat -c '%h' "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE")" == "1" ]] \
            || die "Production coin Snapshot relay state file is not a private regular file."
    fi
    local protected
    for protected in \
        "${RUNTIME_ENV_SOURCE_PATH:-}" \
        "${FOREIGN_RUNTIME_ENV_PATH:-}" \
        "${IRAN_RUNTIME_ENV_PATH:-}" \
        "${LOCAL_PROJECT_DIR:-}/.env" \
        "${PRODUCTION_RELEASE_LOCK_PATH:-}" \
        "${PRODUCTION_SOURCE_LOCK_PATH:-}" \
        "${PRODUCTION_TWO_HOST_RELEASE_STATE_FILE:-}"; do
        [[ -z "$protected" || "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" != "$protected" ]] \
            || die "Production coin Snapshot relay state file aliases a protected release path."
    done
}

validate_two_host_release_state_file() {
    [[ "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" == "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE_CANONICAL" ]] \
        || die "Two-host production release state must use the canonical production path."
    validate_remote_shell_path "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" \
        "Two-host production release state file"
    local parent canonical_parent protected
    parent="$(dirname "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE")"
    canonical_parent="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "$parent")"
    [[ "$canonical_parent" == "$parent" ]] \
        || die "Two-host production release state directory must be canonical."
    if [[ -e "$parent" ]]; then
        [[ -d "$parent" && ! -L "$parent" \
            && "$(stat -c '%u' "$parent")" == "$(id -u)" \
            && "$(stat -c '%a' "$parent")" == "700" ]] \
            || die "Two-host production release state directory must be owner-controlled mode 0700."
    fi
    if [[ -e "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" || -L "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" ]]; then
        [[ -f "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" \
            && ! -L "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" \
            && "$(stat -c '%u' "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE")" == "$(id -u)" \
            && "$(stat -c '%a' "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE")" == "600" \
            && "$(stat -c '%h' "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE")" == "1" ]] \
            || die "Two-host production release state file is not a private regular file."
    fi
    for protected in \
        "${RUNTIME_ENV_SOURCE_PATH:-}" \
        "${FOREIGN_RUNTIME_ENV_PATH:-}" \
        "${IRAN_RUNTIME_ENV_PATH:-}" \
        "${LOCAL_PROJECT_DIR:-}/.env" \
        "${PRODUCTION_RELEASE_LOCK_PATH:-}" \
        "${PRODUCTION_SOURCE_LOCK_PATH:-}" \
        "${PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE:-}"; do
        [[ -z "$protected" || "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" != "$protected" ]] \
            || die "Two-host production release state aliases a protected release path."
    done
}

write_two_host_release_state() {
    local phase="$1" state_dir
    case "$phase" in
        prepared|foreign_committed|iran_payload_installed|iran_committed) ;;
        *) die "Invalid two-host production release phase." ;;
    esac
    [[ "$PRODUCTION_RELEASE_EVIDENCE_VERIFIED" == "1" \
        && "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ \
        && "$PRODUCTION_RELEASE_TREE" =~ ^[0-9a-f]{40}$ \
        && "$PRODUCTION_PRE_RELEASE_SHA" =~ ^[0-9a-f]{40}$ \
        && "$PRODUCTION_RUNTIME_ENV_SOURCE_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_RUNTIME_ENV_IRAN_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_BACKUP_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_BACKUP_ARTIFACT_SET_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_FOREIGN_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ \
        && "$PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_IRAN_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ \
        && "$PRODUCTION_IRAN_REMOTE_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ \
        && "$PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] \
        || die "Two-host production release identity is incomplete."
    state_dir="$(dirname "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE")"
    install -d -m 0700 -- "$state_dir"
    validate_two_host_release_state_file
    python3 - "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" "$phase" "$RELEASE_SHA" \
        "$PRODUCTION_RELEASE_TREE" "$PRODUCTION_PRE_RELEASE_SHA" \
        "$PRODUCTION_RUNTIME_ENV_SOURCE_SHA256" \
        "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" \
        "$PRODUCTION_RUNTIME_ENV_IRAN_SHA256" \
        "$PRODUCTION_BACKUP_RECEIPT_PATH" "$PRODUCTION_BACKUP_RECEIPT_SHA256" \
        "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH" \
        "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256" \
        "$PRODUCTION_BACKUP_ARTIFACT_SET_SHA256" "$PRODUCTION_RELEASE_SCHEMA_HEAD" \
        "$PRODUCTION_FOREIGN_IMAGE_ID" "$PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256" \
        "$PRODUCTION_IRAN_IMAGE_ID" "$PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256" \
        "$PRODUCTION_FOREIGN_TARGET_BINDING_SHA256" \
        "$PRODUCTION_IRAN_TARGET_BINDING_SHA256" \
        "$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256" <<'PY'
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

destination = Path(sys.argv[1])
payload = {
    "schema_version": 3,
    "status": "release_incomplete",
    "phase": sys.argv[2],
    "release_sha": sys.argv[3],
    "release_tree": sys.argv[4],
    "pre_release_sha": sys.argv[5],
    "source_sha256": sys.argv[6],
    "foreign_runtime_sha256": sys.argv[7],
    "iran_runtime_sha256": sys.argv[8],
    "backup_receipt_path": sys.argv[9],
    "backup_receipt_sha256": sys.argv[10],
    "migration_rehearsal_receipt_path": sys.argv[11],
    "migration_rehearsal_receipt_sha256": sys.argv[12],
    "backup_artifact_set_sha256": sys.argv[13],
    "release_schema_head": sys.argv[14],
    "foreign_image_id": sys.argv[15],
    "foreign_image_receipt_sha256": sys.argv[16],
    "iran_image_id": sys.argv[17],
    "iran_image_receipt_sha256": sys.argv[18],
    "foreign_target_binding_sha256": sys.argv[19],
    "iran_target_binding_sha256": sys.argv[20],
    "iran_source_payload_manifest_sha256": sys.argv[21],
    "recovery_action": "rerun_exact_same_release_for_forward_reconcile",
    "secrets_disclosed": False,
}
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(candidate, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
    chmod 0600 "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE"
    PRODUCTION_TWO_HOST_RELEASE_PHASE="$phase"
}

load_two_host_release_state() {
    validate_two_host_release_state_file
    PRODUCTION_TWO_HOST_RELEASE_RESUMING=0
    [[ -f "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" ]] || return 0
    local loaded marker_phase marker_release marker_tree marker_pre_release marker_source marker_foreign marker_iran
    local marker_backup_path marker_backup_digest marker_rehearsal_path marker_rehearsal_digest
    local marker_artifact_set marker_schema marker_foreign_image marker_foreign_image_receipt
    local marker_iran_image marker_iran_image_receipt marker_foreign_target marker_iran_target marker_iran_payload
    loaded="$(python3 - "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {
    "schema_version", "status", "phase", "release_sha", "release_tree", "pre_release_sha", "source_sha256",
    "foreign_runtime_sha256", "iran_runtime_sha256", "recovery_action",
    "backup_receipt_path", "backup_receipt_sha256",
    "migration_rehearsal_receipt_path", "migration_rehearsal_receipt_sha256",
    "backup_artifact_set_sha256", "release_schema_head", "foreign_image_id",
    "foreign_image_receipt_sha256", "iran_image_id", "iran_image_receipt_sha256",
    "foreign_target_binding_sha256", "iran_target_binding_sha256",
    "iran_source_payload_manifest_sha256", "secrets_disclosed",
}
if set(payload) != required or payload["schema_version"] != 3:
    raise SystemExit(2)
if payload["status"] != "release_incomplete" or payload["secrets_disclosed"] is not False:
    raise SystemExit(2)
if payload["phase"] not in {"prepared", "foreign_committed", "iran_payload_installed", "iran_committed"}:
    raise SystemExit(2)
if not re.fullmatch(r"[0-9a-f]{40}", str(payload["release_sha"])) \
        or not re.fullmatch(r"[0-9a-f]{40}", str(payload["release_tree"])) \
        or not re.fullmatch(r"[0-9a-f]{40}", str(payload["pre_release_sha"])):
    raise SystemExit(2)
for field in (
    "source_sha256", "foreign_runtime_sha256", "iran_runtime_sha256",
    "backup_receipt_sha256", "migration_rehearsal_receipt_sha256",
    "backup_artifact_set_sha256", "foreign_image_receipt_sha256",
    "iran_image_receipt_sha256", "foreign_target_binding_sha256",
    "iran_target_binding_sha256", "iran_source_payload_manifest_sha256",
):
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload[field])):
        raise SystemExit(2)
for field in ("foreign_image_id", "iran_image_id"):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(payload[field])):
        raise SystemExit(2)
if not re.fullmatch(r"[0-9a-z]{12}", str(payload["release_schema_head"])):
    raise SystemExit(2)
for field in ("backup_receipt_path", "migration_rehearsal_receipt_path"):
    value = str(payload[field])
    if not value.startswith("/") or any(character in value for character in "\t\n\r"):
        raise SystemExit(2)
if payload["recovery_action"] != "rerun_exact_same_release_for_forward_reconcile":
    raise SystemExit(2)
print("\t".join(str(payload[field]) for field in (
    "phase", "release_sha", "release_tree", "pre_release_sha", "source_sha256",
    "foreign_runtime_sha256", "iran_runtime_sha256", "backup_receipt_path",
    "backup_receipt_sha256", "migration_rehearsal_receipt_path",
    "migration_rehearsal_receipt_sha256", "backup_artifact_set_sha256",
    "release_schema_head", "foreign_image_id", "foreign_image_receipt_sha256",
    "iran_image_id", "iran_image_receipt_sha256", "foreign_target_binding_sha256",
    "iran_target_binding_sha256", "iran_source_payload_manifest_sha256",
)))
PY
)" || die "Two-host production release marker is invalid; manual recovery review is required."
    IFS=$'\t' read -r marker_phase marker_release marker_tree marker_pre_release marker_source marker_foreign marker_iran \
        marker_backup_path marker_backup_digest marker_rehearsal_path marker_rehearsal_digest \
        marker_artifact_set marker_schema marker_foreign_image marker_foreign_image_receipt \
        marker_iran_image marker_iran_image_receipt marker_foreign_target marker_iran_target marker_iran_payload \
        <<<"$loaded"
    [[ "$marker_release" == "$RELEASE_SHA" \
        && "$marker_tree" == "$PRODUCTION_RELEASE_TREE" \
        && "$marker_source" == "$PRODUCTION_RUNTIME_ENV_SOURCE_SHA256" \
        && "$marker_foreign" == "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" \
        && "$marker_iran" == "$PRODUCTION_RUNTIME_ENV_IRAN_SHA256" \
        && "$marker_backup_path" == "$PRODUCTION_BACKUP_RECEIPT_PATH" \
        && "$marker_backup_digest" == "$PRODUCTION_BACKUP_RECEIPT_SHA256" \
        && "$marker_rehearsal_path" == "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH" \
        && "$marker_rehearsal_digest" == "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256" \
        && "$marker_iran_payload" == "$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256" ]] \
        || die "An incomplete two-host release is bound to different code or runtime env bytes; rerun the exact recorded release."
    if [[ "$PRODUCTION_RELEASE_EVIDENCE_VERIFIED" == "1" ]]; then
        [[ "$marker_pre_release" == "$PRODUCTION_PRE_RELEASE_SHA" \
            && "$marker_artifact_set" == "$PRODUCTION_BACKUP_ARTIFACT_SET_SHA256" \
            && "$marker_schema" == "$PRODUCTION_RELEASE_SCHEMA_HEAD" \
            && "$marker_foreign_image" == "$PRODUCTION_FOREIGN_IMAGE_ID" \
            && "$marker_foreign_image_receipt" == "$PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256" \
            && "$marker_iran_image" == "$PRODUCTION_IRAN_IMAGE_ID" \
            && "$marker_iran_image_receipt" == "$PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256" \
            && "$marker_foreign_target" == "$PRODUCTION_FOREIGN_TARGET_BINDING_SHA256" \
            && "$marker_iran_target" == "$PRODUCTION_IRAN_TARGET_BINDING_SHA256" ]] \
            || die "Incomplete release evidence/image/target bindings drifted; exact forward recovery is required."
    fi
    PRODUCTION_TWO_HOST_RELEASE_PHASE="$marker_phase"
    PRODUCTION_TWO_HOST_RELEASE_RESUMING=1
}

begin_two_host_release_transaction() {
    [[ "$PRODUCTION_RELEASE_EVIDENCE_VERIFIED" == "1" ]] \
        || die "Production release evidence must pass before the durable transaction begins."
    verify_runtime_env_pair_lock
    load_two_host_release_state
    if [[ "$PRODUCTION_TWO_HOST_RELEASE_RESUMING" == "1" ]]; then
        log "Resuming the exact incomplete two-host release from its durable recovery marker."
    else
        write_two_host_release_state prepared
    fi
    PRODUCTION_TWO_HOST_RELEASE_GUARD_ARMED=1
}

clear_two_host_release_state() {
    validate_two_host_release_state_file
    if [[ -f "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" ]]; then
        python3 - "$PRODUCTION_TWO_HOST_RELEASE_STATE_FILE" <<'PY'
import os
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.unlink()
directory = os.open(path.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
    fi
    PRODUCTION_TWO_HOST_RELEASE_GUARD_ARMED=0
    PRODUCTION_TWO_HOST_RELEASE_PHASE=""
    PRODUCTION_TWO_HOST_RELEASE_RESUMING=0
}

two_host_release_exit_guard() {
    local status="${1:-$?}"
    if [[ "$status" != "0" && "$PRODUCTION_TWO_HOST_RELEASE_GUARD_ARMED" == "1" ]]; then
        if [[ "$PRODUCTION_TWO_HOST_WRITER_RESTART_GUARD_ARMED" == "1" ]]; then
            emergency_disable_all_foreign_writers || true
            emergency_disable_all_iran_writers || true
        fi
        write_two_host_release_state "${PRODUCTION_TWO_HOST_RELEASE_PHASE:-prepared}" || true
        printf 'production_release_status=release_incomplete two_host_reconcile_required=true recovery_action=rerun_exact_same_release_for_forward_reconcile\n' >&2
        if [[ "$PRODUCTION_TWO_HOST_WRITERS_QUIESCED" == "1" ]]; then
            printf 'production_writer_planes=foreign_and_iran_intentionally_stopped old_code_restart=forbidden_until_both_schemas_match_release_head\n' >&2
        fi
    fi
    return "$status"
}

load_production_coin_relay_recovery_marker() {
    PRODUCTION_COIN_SNAPSHOT_RELAY_RECOVERY_PENDING=0
    [[ -f "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" ]] || return 0
    local state
    state="$(python3 - "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" <<'PY'
import json
import re
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
    raise SystemExit(2)
payload = json.loads(path.read_text(encoding="utf-8"))
required = {
    "status",
    "relay_intentionally_stopped",
    "previous_timer_enabled",
    "previous_timer_active",
    "release_sha",
    "relay_script_sha256",
    "recovery_action",
}
if set(payload) != required:
    raise SystemExit(2)
if payload["status"] != "release_incomplete" or payload["relay_intentionally_stopped"] is not True:
    raise SystemExit(2)
if not isinstance(payload["previous_timer_enabled"], bool) or not isinstance(payload["previous_timer_active"], bool):
    raise SystemExit(2)
if not re.fullmatch(r"[0-9a-f]{40,64}", str(payload["release_sha"])):
    raise SystemExit(2)
if not re.fullmatch(r"[0-9a-f]{64}", str(payload["relay_script_sha256"])):
    raise SystemExit(2)
if payload["recovery_action"] != "rerun_release_for_verified_reconcile":
    raise SystemExit(2)
print(int(payload["previous_timer_enabled"]), int(payload["previous_timer_active"]))
PY
)" || die "Production coin Snapshot relay recovery marker is invalid; manual review is required."
    read -r PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED \
        PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE <<<"$state"
    PRODUCTION_COIN_SNAPSHOT_RELAY_RECOVERY_PENDING=1
}

write_production_coin_relay_recovery_marker() {
    local relay_digest state_dir
    relay_digest="$(file_sha256 "$PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT")"
    [[ "$relay_digest" =~ ^[0-9a-f]{64}$ && "$RELEASE_SHA" =~ ^[0-9a-f]{40,64}$ ]] \
        || return 1
    state_dir="$(dirname "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE")"
    install -d -m 0700 -- "$state_dir"
    validate_production_coin_relay_state_file
    python3 - "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" \
        "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED" \
        "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE" \
        "$RELEASE_SHA" "$relay_digest" <<'PY'
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

destination = Path(sys.argv[1])
payload = {
    "status": "release_incomplete",
    "relay_intentionally_stopped": True,
    "previous_timer_enabled": sys.argv[2] == "1",
    "previous_timer_active": sys.argv[3] == "1",
    "release_sha": sys.argv[4],
    "relay_script_sha256": sys.argv[5],
    "recovery_action": "rerun_release_for_verified_reconcile",
}
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
    chmod 0600 "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE"
}

clear_production_coin_relay_recovery_marker() {
    if [[ -f "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" ]]; then
        rm -f -- "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE"
    fi
    PRODUCTION_COIN_SNAPSHOT_RELAY_RECOVERY_PENDING=0
}

suspend_production_coin_snapshot_relay() {
    validate_production_coin_relay_state_file
    load_production_coin_relay_recovery_marker
    PRODUCTION_COIN_SNAPSHOT_RELAY_HAD_UNIT=0
    if command -v systemctl >/dev/null 2>&1 \
        && systemctl cat "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1; then
        PRODUCTION_COIN_SNAPSHOT_RELAY_HAD_UNIT=1
    fi
    if [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_RECOVERY_PENDING" != "1" ]]; then
        PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED=0
        PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE=0
        PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE_WAS_ACTIVE=0
        if [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_HAD_UNIT" == "1" ]]; then
            if systemctl is-enabled --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER"; then
                PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED=1
            fi
            if systemctl is-active --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER"; then
                PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE=1
            fi
            if systemctl is-active --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE"; then
                PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE_WAS_ACTIVE=1
            fi
        fi
    fi
    if [[ "$PRODUCTION_COIN_INFERENCE_RELAY_ENABLED" != "1" \
        && "$PRODUCTION_COIN_SNAPSHOT_RELAY_RECOVERY_PENDING" != "1" \
        && "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE" != "1" \
        && "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED" != "1" \
        && "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE_WAS_ACTIVE" != "1" ]]; then
        return 0
    fi
    write_production_coin_relay_recovery_marker \
        || die "Could not write the production coin Snapshot relay recovery marker."
    PRODUCTION_COIN_SNAPSHOT_RELAY_GUARD_ARMED=1
    command -v systemctl >/dev/null 2>&1 || return 0
    systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1 || true
    systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" || true
    ! systemctl is-active --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" \
        || die "Production coin Snapshot relay timer did not stop before release."
    ! systemctl is-active --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" \
        || die "Production coin Snapshot relay service did not quiesce before release."
    if [[ "$PRODUCTION_COIN_INFERENCE_RELAY_ENABLED" == "0" \
        && ( "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE" == "1" \
            || "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED" == "1" ) \
        && "$PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM" != "$PRODUCTION_COIN_SNAPSHOT_RELAY_DISABLE_CONFIRM_TEXT" ]]; then
        die "Disabling an enabled or active production coin Snapshot relay requires the exact disable confirmation; the relay was left stopped."
    fi
    log "Suspended the production coin Snapshot relay during cross-host code synchronization."
}

production_release_relay_exit_guard() {
    local status="${1:-$?}"
    if [[ "$status" != "0" && "$PRODUCTION_COIN_SNAPSHOT_RELAY_GUARD_ARMED" == "1" ]]; then
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1 || true
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" >/dev/null 2>&1 || true
        if write_production_coin_relay_recovery_marker; then
            printf 'production_release_status=release_incomplete relay_intentionally_stopped=true recovery_action=rerun_release_for_verified_reconcile\n' >&2
        else
            printf 'production_release_status=release_incomplete relay_intentionally_stopped=true recovery_marker=write_failed\n' >&2
        fi
    fi
    return "$status"
}

validate_production_coin_input_timer_recovery_path() {
    [[ "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" == "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR_CANONICAL" ]] \
        || die "Production coin input timer recovery must use the canonical production path."
    validate_remote_shell_path "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" \
        "Production coin input timer recovery directory"
    local parent canonical_parent protected
    parent="$(dirname "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR")"
    canonical_parent="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "$parent")"
    [[ "$canonical_parent" == "$parent" ]] \
        || die "Production coin input timer recovery parent must be canonical."
    if [[ -e "$parent" ]]; then
        [[ -d "$parent" && ! -L "$parent" \
            && "$(stat -c '%u' "$parent")" == "$(id -u)" \
            && "$(stat -c '%a' "$parent")" == "700" ]] \
            || die "Production coin input timer recovery parent must be owner-controlled mode 0700."
    fi
    for protected in \
        "${RUNTIME_ENV_SOURCE_PATH:-}" \
        "${FOREIGN_RUNTIME_ENV_PATH:-}" \
        "${IRAN_RUNTIME_ENV_PATH:-}" \
        "${LOCAL_PROJECT_DIR:-}/.env" \
        "${PRODUCTION_RELEASE_LOCK_PATH:-}" \
        "${PRODUCTION_SOURCE_LOCK_PATH:-}" \
        "${PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE:-}" \
        "${PRODUCTION_TWO_HOST_RELEASE_STATE_FILE:-}"; do
        [[ -z "$protected" || "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" != "$protected" ]] \
            || die "Production coin input timer recovery aliases a protected release path."
    done
}

read_production_coin_input_timer_recovery_state() {
    validate_production_coin_input_timer_recovery_path
    python3 - "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
state_path = root / "state.json"
units = (
    "coin-group-event-telegram.service",
    "coin-group-event-telegram.timer",
    "trading-bot-private-gold-collector.service",
    "trading-bot-private-gold-collector.timer",
)
timers = (
    "coin-group-event-telegram.timer",
    "trading-bot-private-gold-collector.timer",
)
if (
    root.is_symlink()
    or not root.is_dir()
    or stat.S_IMODE(root.stat().st_mode) != 0o700
    or root.stat().st_uid != os.getuid()
):
    raise SystemExit(2)
if state_path.is_symlink() or not state_path.is_file():
    raise SystemExit(2)
state_stat = state_path.stat()
if stat.S_IMODE(state_stat.st_mode) != 0o600 or state_stat.st_nlink != 1 or state_stat.st_uid != os.getuid():
    raise SystemExit(2)
payload = json.loads(state_path.read_text(encoding="utf-8"))
if set(payload) != {"schema_version", "status", "release_sha", "units", "timers", "recovery_action"}:
    raise SystemExit(2)
if payload["schema_version"] != 1 or payload["status"] != "prepared":
    raise SystemExit(2)
if payload["recovery_action"] != "restore_prior_units_and_timer_state_on_release_failure":
    raise SystemExit(2)
if not re.fullmatch(r"[0-9a-f]{40}", str(payload["release_sha"])):
    raise SystemExit(2)
if set(payload["units"]) != set(units) or set(payload["timers"]) != set(timers):
    raise SystemExit(2)
print(f"release\t{payload['release_sha']}")
expected_entries = {"state.json"}
for unit in units:
    record = payload["units"][unit]
    if set(record) != {"existed", "sha256"} or not isinstance(record["existed"], bool):
        raise SystemExit(2)
    digest = record["sha256"]
    if record["existed"]:
        backup = root / unit
        expected_entries.add(unit)
        if backup.is_symlink() or not backup.is_file():
            raise SystemExit(2)
        backup_stat = backup.stat()
        if stat.S_IMODE(backup_stat.st_mode) != 0o600 or backup_stat.st_nlink != 1 or backup_stat.st_uid != os.getuid():
            raise SystemExit(2)
        actual = hashlib.sha256(backup.read_bytes()).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or actual != digest:
            raise SystemExit(2)
    elif digest is not None or (root / unit).exists() or (root / unit).is_symlink():
        raise SystemExit(2)
    print(f"unit\t{unit}\t{int(record['existed'])}\t{digest or '-'}")
if {entry.name for entry in root.iterdir()} != expected_entries:
    raise SystemExit(2)
for timer in timers:
    record = payload["timers"][timer]
    if set(record) != {"enabled", "active"} or not all(
        isinstance(record[key], bool) for key in ("enabled", "active")
    ):
        raise SystemExit(2)
    print(f"timer\t{timer}\t{int(record['enabled'])}\t{int(record['active'])}")
PY
}

capture_production_coin_input_timer_recovery_state() {
    if [[ "$PRODUCTION_COIN_INFERENCE_REQUESTED" != "1" ]]; then
        [[ ! -e "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" \
            && ! -L "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" ]] \
            || die "A production coin input timer recovery state exists while inference is disabled; manual recovery is required."
        return 0
    fi
    validate_production_coin_input_timer_recovery_path
    local recovery_parent candidate state_path unit timer status
    local -a units=(
        coin-group-event-telegram.service
        coin-group-event-telegram.timer
        trading-bot-private-gold-collector.service
        trading-bot-private-gold-collector.timer
    )
    local -a timers=(
        coin-group-event-telegram.timer
        trading-bot-private-gold-collector.timer
    )
    local -A existed=() digests=() enabled=() active=()
    if [[ -e "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" \
        || -L "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" ]]; then
        local recovered_release
        recovered_release="$(read_production_coin_input_timer_recovery_state | sed -n 's/^release\t//p')" \
            || die "Production coin input timer recovery state is invalid; manual review is required."
        [[ "$recovered_release" == "$RELEASE_SHA" ]] \
            || die "Production coin input timer recovery belongs to a different release; manual recovery is required."
        PRODUCTION_COIN_INPUT_TIMER_GUARD_ARMED=1
        log "Reusing the exact prior production coin input timer recovery state for this release."
        return 0
    fi
    recovery_parent="$(dirname "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR")"
    install -d -m 0700 -- "$recovery_parent"
    validate_production_coin_input_timer_recovery_path
    [[ -d "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR" \
        && ! -L "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR" ]] \
        || die "Production coin input systemd directory is invalid."
    candidate="$(mktemp -d "$recovery_parent/.coin-input-timer-recovery.XXXXXXXX")"
    chmod 0700 "$candidate"
    for unit in "${units[@]}"; do
        if [[ -L "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" ]]; then
            rm -rf -- "$candidate"
            die "Production coin input unit is a symlink: $unit"
        elif [[ -f "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" ]]; then
            existed["$unit"]=1
            install -m 0600 -- "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" "$candidate/$unit"
            digests["$unit"]="$(file_sha256 "$candidate/$unit")"
        elif [[ -e "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" ]]; then
            rm -rf -- "$candidate"
            die "Production coin input unit has an unsupported file type: $unit"
        else
            existed["$unit"]=0
            digests["$unit"]=-
        fi
    done
    for timer in "${timers[@]}"; do
        enabled["$timer"]=0
        active["$timer"]=0
        if [[ "${existed[$timer]}" == "1" ]]; then
            if systemctl is-enabled --quiet "$timer" >/dev/null 2>&1; then
                enabled["$timer"]=1
            else
                status=$?
                [[ "$status" == "1" ]] || {
                    rm -rf -- "$candidate"
                    die "Production coin input timer enabled state is unavailable: $timer"
                }
            fi
            if systemctl is-active --quiet "$timer" >/dev/null 2>&1; then
                active["$timer"]=1
            else
                status=$?
                [[ "$status" == "3" ]] || {
                    rm -rf -- "$candidate"
                    die "Production coin input timer active state is unavailable: $timer"
                }
            fi
        fi
    done
    state_path="$candidate/state.json"
    python3 - "$state_path" "$RELEASE_SHA" \
        "${existed[${units[0]}]}" "${digests[${units[0]}]}" \
        "${existed[${units[1]}]}" "${digests[${units[1]}]}" \
        "${existed[${units[2]}]}" "${digests[${units[2]}]}" \
        "${existed[${units[3]}]}" "${digests[${units[3]}]}" \
        "${enabled[${timers[0]}]}" "${active[${timers[0]}]}" \
        "${enabled[${timers[1]}]}" "${active[${timers[1]}]}" <<'PY'
import json
import os
import sys
from pathlib import Path

destination = Path(sys.argv[1])
release_sha = sys.argv[2]
units = (
    "coin-group-event-telegram.service",
    "coin-group-event-telegram.timer",
    "trading-bot-private-gold-collector.service",
    "trading-bot-private-gold-collector.timer",
)
timers = (
    "coin-group-event-telegram.timer",
    "trading-bot-private-gold-collector.timer",
)
values = sys.argv[3:]
unit_values = values[:8]
timer_values = values[8:]
payload = {
    "schema_version": 1,
    "status": "prepared",
    "release_sha": release_sha,
    "units": {
        unit: {
            "existed": unit_values[index * 2] == "1",
            "sha256": None if unit_values[index * 2 + 1] == "-" else unit_values[index * 2 + 1],
        }
        for index, unit in enumerate(units)
    },
    "timers": {
        timer: {
            "enabled": timer_values[index * 2] == "1",
            "active": timer_values[index * 2 + 1] == "1",
        }
        for index, timer in enumerate(timers)
    },
    "recovery_action": "restore_prior_units_and_timer_state_on_release_failure",
}
descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
PY
    chmod 0600 "$state_path"
    python3 - "$candidate" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in root.iterdir():
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
descriptor = os.open(root, os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
    mv -T -- "$candidate" "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR"
    python3 - "$recovery_parent" <<'PY'
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
    read_production_coin_input_timer_recovery_state >/dev/null \
        || die "Production coin input timer recovery state failed post-write validation."
    PRODUCTION_COIN_INPUT_TIMER_GUARD_ARMED=1
}

clear_production_coin_input_timer_recovery_state() {
    local recovery_parent
    [[ -e "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" \
        || -L "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" ]] || {
        PRODUCTION_COIN_INPUT_TIMER_GUARD_ARMED=0
        return 0
    }
    ( read_production_coin_input_timer_recovery_state >/dev/null ) \
        || return 1
    recovery_parent="$(dirname "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR")"
    rm -rf -- "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" \
        || return 1
    [[ ! -e "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" \
        && ! -L "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" ]] \
        || return 1
    python3 - "$recovery_parent" <<'PY' || return 1
import os
import sys
descriptor = os.open(sys.argv[1], os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
    PRODUCTION_COIN_INPUT_TIMER_GUARD_ARMED=0
}

restore_production_coin_input_timer_recovery_state() {
    local serialized recovered unit existed digest timer enabled active candidate status
    local -a records=()
    local -A prior_unit_existed=()
    local failed=0
    [[ -d "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR" \
        && ! -L "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR" ]] \
        || return 1
    serialized="$(read_production_coin_input_timer_recovery_state)" \
        || return 1
    mapfile -t records <<<"$serialized"
    [[ "${records[0]:-}" == $'release\t'"$RELEASE_SHA" ]] \
        || return 1
    for recovered in "${records[@]:1:4}"; do
        IFS=$'\t' read -r _ unit existed digest <<<"$recovered"
        prior_unit_existed["$unit"]="$existed"
    done
    set +e
    for timer in coin-group-event-telegram.timer trading-bot-private-gold-collector.timer; do
        if [[ -e "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$timer" \
            || -L "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$timer" ]]; then
            systemctl stop "$timer" >/dev/null 2>&1 || failed=1
        else
            systemctl stop "$timer" >/dev/null 2>&1 || true
        fi
        systemctl disable "$timer" >/dev/null 2>&1 || true
    done
    for recovered in "${records[@]:1:4}"; do
        IFS=$'\t' read -r _ unit existed digest <<<"$recovered"
        if [[ "$existed" == "1" ]]; then
            candidate="$(mktemp "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/.${unit}.rollback.XXXXXXXX")" || {
                failed=1
                continue
            }
            install -m 0644 -- "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR/$unit" "$candidate" \
                || failed=1
            [[ "$(file_sha256 "$candidate" 2>/dev/null)" == "$digest" ]] || failed=1
            mv -fT -- "$candidate" "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" || failed=1
            rm -f -- "$candidate"
            [[ -f "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" \
                && ! -L "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" \
                && "$(file_sha256 "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" 2>/dev/null)" == "$digest" ]] \
                || failed=1
        else
            rm -f -- "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" || failed=1
            [[ ! -e "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" \
                && ! -L "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit" ]] \
                || failed=1
        fi
    done
    python3 - "$PRODUCTION_COIN_INPUT_SYSTEMD_DIR" \
        coin-group-event-telegram.service \
        coin-group-event-telegram.timer \
        trading-bot-private-gold-collector.service \
        trading-bot-private-gold-collector.timer <<'PY' || failed=1
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
for name in sys.argv[2:]:
    path = root / name
    if not path.exists():
        continue
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
descriptor = os.open(root, os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
    systemctl daemon-reload >/dev/null 2>&1 || failed=1
    for recovered in "${records[@]:5:2}"; do
        IFS=$'\t' read -r _ timer enabled active <<<"$recovered"
        if [[ "${prior_unit_existed[$timer]:-0}" != "1" ]]; then
            continue
        fi
        if [[ "$enabled" == "1" ]]; then
            systemctl enable "$timer" >/dev/null 2>&1 || failed=1
        else
            systemctl disable "$timer" >/dev/null 2>&1 || failed=1
        fi
        if [[ "$active" == "1" ]]; then
            systemctl restart "$timer" >/dev/null 2>&1 || failed=1
        else
            systemctl stop "$timer" >/dev/null 2>&1 || failed=1
        fi
        if systemctl is-enabled --quiet "$timer" >/dev/null 2>&1; then
            status=0
        else
            status=$?
        fi
        if [[ "$enabled" == "1" ]]; then
            [[ "$status" == "0" ]] || failed=1
        else
            [[ "$status" == "1" ]] || failed=1
        fi
        if systemctl is-active --quiet "$timer" >/dev/null 2>&1; then
            status=0
        else
            status=$?
        fi
        if [[ "$active" == "1" ]]; then
            [[ "$status" == "0" ]] || failed=1
        else
            [[ "$status" == "3" ]] || failed=1
        fi
    done
    set -e
    [[ "$failed" == "0" ]] || return 1
    clear_production_coin_input_timer_recovery_state
}

production_coin_input_timer_exit_guard() {
    local status="${1:-$?}"
    if [[ "$status" != "0" && "$PRODUCTION_COIN_INPUT_TIMER_GUARD_ARMED" == "1" ]]; then
        if restore_production_coin_input_timer_recovery_state; then
            printf 'production_coin_input_timers=prior_units_and_state_restored\n' >&2
        else
            printf 'production_coin_input_timers=rollback_incomplete recovery_state_retained=true manual_recovery_required=true\n' >&2
        fi
    fi
    return "$status"
}

production_release_exit_guard() {
    local status=$?
    trap - EXIT
    two_host_release_exit_guard "$status" || true
    production_release_relay_exit_guard "$status" || true
    production_coin_input_timer_exit_guard "$status" || true
    release_production_locks
    exit "$status"
}

verify_production_coin_relay_script_parity() {
    local local_digest remote_digest remote_script="$IRAN_PROJECT_DIR/scripts/relay_production_coin_inference_snapshot.py"
    validate_remote_shell_path "$remote_script" "Iran production coin Snapshot relay script"
    [[ -f "$PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT" ]] || die "Production coin Snapshot relay script is missing locally."
    local_digest="$(file_sha256 "$PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT")"
    remote_digest="$(ssh_iran "test -f '$remote_script' && sha256sum '$remote_script' | awk '{print \$1}'")"
    [[ "$remote_digest" == "$local_digest" ]] || die "Production coin Snapshot relay script differs between Foreign and Iran after sync."
    PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT_SHA256="$local_digest"
}

verify_production_coin_snapshot_relay() {
    local local_digest remote_digest remote_output
    resolve_production_coin_runtime_contract
    [[ -f "$FOREIGN_COIN_SNAPSHOT_PATH" ]] || die "Foreign production coin Snapshot was not published."
    local_digest="$(file_sha256 "$FOREIGN_COIN_SNAPSHOT_PATH")"
    python3 "$PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT" check \
        --environment production \
        --production-confirmation "$PRODUCTION_COIN_SNAPSHOT_RELAY_CONFIRM_TEXT" \
        --runtime-root "$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR" \
        --snapshot "$FOREIGN_COIN_SNAPSHOT_PATH" \
        --expected-sha256 "$local_digest" \
        --maximum-age-seconds "$PRODUCTION_COIN_SNAPSHOT_MAXIMUM_AGE_SECONDS" >/dev/null
    remote_output="$(ssh_iran "python3 '$IRAN_PROJECT_DIR/scripts/relay_production_coin_inference_snapshot.py' check --environment production --production-confirmation '$PRODUCTION_COIN_SNAPSHOT_RELAY_CONFIRM_TEXT' --runtime-root '$IRAN_COIN_SNAPSHOT_RUNTIME_DIR' --snapshot '$IRAN_COIN_SNAPSHOT_PATH' --expected-sha256 '$local_digest' --maximum-age-seconds '$PRODUCTION_COIN_SNAPSHOT_MAXIMUM_AGE_SECONDS' >/dev/null && sha256sum '$IRAN_COIN_SNAPSHOT_PATH' | awk '{print \$1}'")"
    remote_digest="$(printf '%s' "$remote_output" | tail -n 1)"
    [[ "$remote_digest" == "$local_digest" ]] || die "Production coin Snapshot digests differ between Foreign and Iran."
    if [[ "$PRODUCTION_COIN_INFERENCE_REQUESTED" == "1" ]]; then
        python3 "$PRODUCTION_COIN_READINESS_SCRIPT" \
            --environment production \
            --production-confirmation check-production-coin-inference-readiness \
            snapshot --snapshot "$FOREIGN_COIN_SNAPSHOT_PATH" \
            --expected-sha256 "$local_digest" \
            || die "Foreign production coin Snapshot failed semantic readiness."
        ssh_iran "python3 '$IRAN_PROJECT_DIR/scripts/check_production_coin_inference_readiness.py' --environment production --production-confirmation check-production-coin-inference-readiness snapshot --snapshot '$IRAN_COIN_SNAPSHOT_PATH' --expected-sha256 '$local_digest'" \
            || die "Iran production coin Snapshot failed semantic readiness."
    fi
    if [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED" == "1" ]]; then
        systemctl is-enabled --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" \
            || die "Production coin Snapshot relay timer did not restore its enabled state."
    else
        ! systemctl is-enabled --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" \
            || die "Production coin Snapshot relay timer did not restore its disabled state."
    fi
    if [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE" == "1" ]]; then
        systemctl is-active --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" \
            || die "Production coin Snapshot relay timer did not restore its active state."
    else
        ! systemctl is-active --quiet "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" \
            || die "Production coin Snapshot relay timer did not restore its inactive state."
    fi
    log "Production coin Snapshot relay verified script_sha256=$PRODUCTION_COIN_SNAPSHOT_RELAY_SCRIPT_SHA256 snapshot_sha256=$local_digest"
}

run_production_coin_input_timer_installer() {
    local check_only="$1"
    COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM=install-coin-intelligence-input-timers \
    COIN_INTELLIGENCE_INPUT_TIMERS_CHECK_ONLY="$check_only" \
    COIN_INTELLIGENCE_INPUT_TIMERS_FORCE_ACTIVE=1 \
    PROJECT_DIR="$LOCAL_PROJECT_DIR" \
    COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT="$PRODUCTION_COIN_INFERENCE_SOURCE_ROOT" \
    COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT="$PRODUCTION_COIN_INFERENCE_ESTIMATOR_ROOT" \
    COIN_GROUP_EVENT_CHANNEL_ID="$COIN_GROUP_EVENT_CHANNEL_ID" \
    COIN_INTELLIGENCE_EXPECTED_GROUP_EVENT_CHANNEL_ID="$COIN_GROUP_EVENT_CHANNEL_ID" \
    COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID="$COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID" \
    COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID="$COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID" \
    COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID="$COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID" \
    COIN_INTELLIGENCE_EXPECTED_GROUP_SESSION_FILE="$PRODUCTION_COIN_INFERENCE_SOURCE_ROOT/session/coin-group-event-reader.session" \
    COIN_INTELLIGENCE_EXPECTED_PRIVATE_SESSION_FILE="$PRODUCTION_COIN_INFERENCE_SOURCE_ROOT/session/telegram-reader.session" \
    PRODUCTION_INSTALL_LOCK_INHERITED=verified-release-held-lock \
        bash "$PRODUCTION_COIN_INPUT_TIMER_INSTALLER"
}

install_and_verify_production_coin_inputs() {
    [[ "$PRODUCTION_COIN_INFERENCE_REQUESTED" == "1" ]] || return 0
    run_production_coin_input_timer_installer 0
    local attempt
    for attempt in $(seq 1 30); do
        if run_production_coin_input_timer_installer 1 >/dev/null 2>&1 \
            && python3 "$PRODUCTION_COIN_READINESS_SCRIPT" \
                --environment production \
                --production-confirmation check-production-coin-inference-readiness \
                source --market-store "$PRODUCTION_COIN_INFERENCE_SOURCE_STORE" \
                >/dev/null 2>&1; then
            run_production_coin_input_timer_installer 1
            python3 "$PRODUCTION_COIN_READINESS_SCRIPT" \
                --environment production \
                --production-confirmation check-production-coin-inference-readiness \
                source --market-store "$PRODUCTION_COIN_INFERENCE_SOURCE_STORE"
            log "Production coin inference upstream collectors are identity-bound, active, successful, and checkpointed; market-age diagnostics were emitted."
            return 0
        fi
        sleep 5
    done
    die "Production coin inference upstream collectors did not reach the required fresh/readable state."
}

verify_running_production_coin_consumers() {
    [[ "$PRODUCTION_COIN_INFERENCE_REQUESTED" == "1" ]] || return 0
    local attempt local_digest remote_digest
    for attempt in $(seq 1 5); do
        local_digest="$(file_sha256 "$FOREIGN_COIN_SNAPSHOT_PATH")"
        remote_digest="$(ssh_iran "sha256sum '$IRAN_COIN_SNAPSHOT_PATH' | awk '{print \$1}'")"
        if [[ "$local_digest" == "$remote_digest" \
            && "$local_digest" =~ ^[0-9a-f]{64}$ ]] \
            && docker exec trading_bot_app python3 /app/scripts/check_production_coin_inference_readiness.py \
                --environment production \
                --production-confirmation check-production-coin-inference-readiness \
                consumer --snapshot /app/runtime/coin-inference/coin-rates.json \
                --expected-sha256 "$local_digest" --expect-enabled \
            && docker exec trading_bot_bot python3 /app/scripts/check_production_coin_inference_readiness.py \
                --environment production \
                --production-confirmation check-production-coin-inference-readiness \
                consumer --snapshot /app/runtime/coin-inference/coin-rates.json \
                --expected-sha256 "$local_digest" --expect-enabled \
            && ssh_iran "docker exec trading_bot_app python3 /app/scripts/check_production_coin_inference_readiness.py --environment production --production-confirmation check-production-coin-inference-readiness consumer --snapshot /app/runtime/coin-inference/coin-rates.json --expected-sha256 '$local_digest' --expect-enabled"; then
            log "Production coin inference consumers passed same-digest, read-only-mount, enabled-flag and transport-freshness probes; hard-reject authority diagnostics were emitted and remain fail-open when degraded."
            return 0
        fi
        sleep 2
    done
    die "Production coin inference consumers did not pass the post-deploy readiness contract."
}

reconcile_production_coin_snapshot_relay() {
    if [[ "$PRODUCTION_COIN_INFERENCE_RELAY_ENABLED" == "0" ]]; then
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1 || true
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" >/dev/null 2>&1 || true
        systemctl disable "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1 || true
        clear_production_coin_relay_recovery_marker
        PRODUCTION_COIN_SNAPSHOT_RELAY_GUARD_ARMED=0
        log "Production coin Snapshot relay is intentionally stopped and disabled."
        return 0
    fi
    ensure_local_production_coin_runtime_dir
    ensure_remote_production_coin_runtime_dir 1
    verify_production_coin_relay_script_parity
    PRODUCTION_COIN_INFERENCE_CONFIRM="$PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM" \
    PROJECT_DIR="$LOCAL_PROJECT_DIR" \
    PRODUCTION_COIN_INFERENCE_SOURCE_ROOT="$PRODUCTION_COIN_INFERENCE_SOURCE_ROOT" \
    PRODUCTION_COIN_INFERENCE_SOURCE_STORE="$PRODUCTION_COIN_INFERENCE_SOURCE_STORE" \
    PRODUCTION_COIN_INFERENCE_RUNTIME_ROOT="$FOREIGN_COIN_SNAPSHOT_RUNTIME_DIR" \
    PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_PATH="$FOREIGN_COIN_SNAPSHOT_PATH" \
    PRODUCTION_COIN_INFERENCE_REMOTE_HOST="$IRAN_SSH_TARGET" \
    PRODUCTION_COIN_INFERENCE_REMOTE_PORT="$IRAN_SSH_PORT" \
    PRODUCTION_COIN_INFERENCE_REMOTE_RUNTIME_ROOT="$IRAN_COIN_SNAPSHOT_RUNTIME_DIR" \
    PRODUCTION_COIN_INFERENCE_REMOTE_SNAPSHOT="$IRAN_COIN_SNAPSHOT_PATH" \
    PRODUCTION_COIN_INFERENCE_REMOTE_PROJECT_DIR="$IRAN_PROJECT_DIR" \
    PRODUCTION_COIN_INFERENCE_REMOTE_IDENTITY_FILE="${IRAN_SSH_PRIVATE_KEY_PATH:-}" \
    PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS="$PRODUCTION_COIN_SNAPSHOT_MAXIMUM_AGE_SECONDS" \
    PRODUCTION_INSTALL_LOCK_INHERITED=verified-release-held-lock \
        bash "$PRODUCTION_COIN_SNAPSHOT_RELAY_INSTALLER"
    PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED=1
    PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE=1
    if [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED" == "1" ]]; then
        systemctl enable "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1
    else
        systemctl disable "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1
    fi
    if [[ "$PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE" == "1" ]]; then
        systemctl start "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1
    else
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1
    fi
    if ! (verify_production_coin_snapshot_relay); then
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" >/dev/null 2>&1 || true
        systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" >/dev/null 2>&1 || true
        die "Production coin Snapshot relay verification failed; the relay was left stopped."
    fi
    clear_production_coin_relay_recovery_marker
}

require_env_value() {
    local env_path="$1"
    local key="$2"
    local value
    value="$(read_env_value "$env_path" "$key")"
    [[ -n "$value" ]] || die "Missing required env value '$key' in $env_path"
}

env_value_state() {
    local value="${1:-}"
    if [[ -z "$value" ]]; then
        printf 'EMPTY'
    else
        printf 'SET(len=%s)' "${#value}"
    fi
}

backup_runtime_env_file() {
    local env_path="$1"
    local role_label="$2"
    [[ -f "$env_path" ]] || return 0

    local timestamp safe_name backup_path source_sha256 backup_sha256
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    safe_name="$(printf '%s' "$env_path" | sed 's#^/##; s#[^A-Za-z0-9._-]#_#g')"
    install -d -m 0700 -- "$ENV_BACKUP_DIR"
    validate_secure_env_directory "$ENV_BACKUP_DIR" \
        || die "Runtime env backup directory is not secure."
    backup_path="$(mktemp "$ENV_BACKUP_DIR/${safe_name}.${timestamp}.XXXXXX.bak")"
    install -m 0600 -- "$env_path" "$backup_path"
    fsync_file_and_parent "$backup_path"
    source_sha256="$(file_sha256 "$env_path")"
    backup_sha256="$(file_sha256 "$backup_path")"
    [[ "$backup_sha256" == "$source_sha256" ]] \
        || die "$role_label runtime env backup digest mismatch."
    log "Backed up $role_label runtime env sha256=$backup_sha256."
}

validate_runtime_env_source_policy() {
    local project_env_path="$LOCAL_PROJECT_DIR/.env"
    local project_iran_env_path="$LOCAL_PROJECT_DIR/.env.iran"
    local source foreign_output iran_output project_env project_iran_env
    source="$(canonical_path "$RUNTIME_ENV_SOURCE_PATH")"
    foreign_output="$(canonical_path "$FOREIGN_RUNTIME_ENV_PATH")"
    iran_output="$(canonical_path "$IRAN_RUNTIME_ENV_PATH")"
    project_env="$(canonical_path "$project_env_path")"
    project_iran_env="$(canonical_path "$project_iran_env_path")"

    if [[ "$ALLOW_PROJECT_ENV_SOURCE" != "1" ]]; then
        if [[ "$source" == "$project_env" || "$source" == "$project_iran_env" ]]; then
            die "RUNTIME_ENV_SOURCE_PATH points at a project-root env file. Use an immutable secure source outside the repo, or set ALLOW_PROJECT_ENV_SOURCE=1 only for an intentional emergency release."
        fi
    fi

    [[ "$foreign_output" != "$iran_output" ]] || die "FOREIGN_RUNTIME_ENV_PATH and IRAN_RUNTIME_ENV_PATH must be different files."
    if [[ "$source" == "$foreign_output" || "$source" == "$iran_output" ]]; then
        die "RUNTIME_ENV_SOURCE_PATH must be different from both rendered runtime output paths."
    fi
    if [[ "$foreign_output" == "$project_env" || "$foreign_output" == "$project_iran_env" \
        || "$iran_output" == "$project_env" || "$iran_output" == "$project_iran_env" ]]; then
        die "Rendered runtime env outputs must be staging-only files separate from every live project env destination."
    fi
    if [[ -n "${IRAN_PROJECT_DIR:-}" \
        && "$iran_output" == "$(canonical_path "$IRAN_PROJECT_DIR/.env")" ]]; then
        die "Rendered Iran runtime env output must be separate from the remote live env destination."
    fi
}

validate_secure_runtime_env_source_file() {
    python3 - "$RUNTIME_ENV_SOURCE_PATH" <<'PY'
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
metadata = path.lstat()
allowed_owners = {0, os.geteuid()}
if (
    path.is_symlink()
    or not stat.S_ISREG(metadata.st_mode)
    or path.resolve(strict=True) != path
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_uid not in allowed_owners
):
    raise SystemExit(2)
parent = path.parent
parent_metadata = parent.lstat()
pending = parent / ".production-runtime-source.pending.json"
if (
    parent.is_symlink()
    or not stat.S_ISDIR(parent_metadata.st_mode)
    or parent.resolve(strict=True) != parent
    or parent_metadata.st_uid not in allowed_owners
    or stat.S_IMODE(parent_metadata.st_mode) & 0o022
):
    raise SystemExit(3)
if pending.exists() or pending.is_symlink():
    raise SystemExit(4)
PY
}

validate_secure_env_directory() {
    local directory="$1"
    python3 - "$directory" <<'PY'
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
metadata = path.lstat()
if (
    path.is_symlink()
    or not stat.S_ISDIR(metadata.st_mode)
    or path.resolve(strict=True) != path
    or metadata.st_uid not in {0, os.geteuid()}
    or stat.S_IMODE(metadata.st_mode) & 0o022
):
    raise SystemExit(2)
PY
}

validate_runtime_identity_files() {
    [[ -f "$DEPLOYMENT_SURFACE_GUARD" ]] || die "Deployment surface guard missing: $DEPLOYMENT_SURFACE_GUARD"
    local guard_args=(
        --repo-root "$LOCAL_PROJECT_DIR"
        --manifest-path "$MANIFEST_PATH"
        --runtime-env "foreign=$FOREIGN_RUNTIME_ENV_PATH"
        --runtime-env "iran=$IRAN_RUNTIME_ENV_PATH"
    )
    if [[ "$ALLOW_PROJECT_ENV_SOURCE" == "1" ]]; then
        guard_args+=(--allow-project-env-source)
    fi
    python3 "$DEPLOYMENT_SURFACE_GUARD" "${guard_args[@]}"
    validate_offer_expiry_receipt_env_files
    validate_iran_otp_delivery_secret_projection
    validate_runtime_release_sha_files
}

validate_iran_otp_delivery_secret_projection() {
    local foreign_secret iran_secret iran_telegram_otp
    foreign_secret="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "OTP_DELIVERY_STATE_SECRET")"
    iran_secret="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "OTP_DELIVERY_STATE_SECRET")"
    iran_telegram_otp="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "TELEGRAM_LOGIN_OTP_ENABLED")"
    [[ -z "$foreign_secret" ]] \
        || die "Foreign runtime must not receive the Iran OTP delivery state secret."
    if is_truthy "$iran_telegram_otp"; then
        [[ ${#iran_secret} -ge 32 ]] \
            || die "Iran Telegram OTP is enabled but the rendered OTP delivery state secret is missing or too short in the immutable source projection."
    fi
}

validate_offer_expiry_receipt_env_files() {
    local foreign_value iran_value
    foreign_value="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED")"
    iran_value="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED")"

    [[ -n "$foreign_value" ]] || die "Foreign runtime env is missing OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED"
    [[ -n "$iran_value" ]] || die "Iran runtime env is missing OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED"
    [[ "${foreign_value,,}" == "${iran_value,,}" ]] || die "Offer-expiry receipt rollout must have the same value on foreign and Iran runtimes"
    if is_truthy "$REQUIRE_OFFER_EXPIRY_COMMAND_RECEIPTS" && ! is_truthy "$foreign_value"; then
        die "Production requires OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED=true on both runtimes"
    fi
}

validate_runtime_release_sha_files() {
    local expected_sha foreign_sha iran_sha
    expected_sha="$(git -C "$LOCAL_PROJECT_DIR" rev-parse HEAD)"
    foreign_sha="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "RELEASE_SHA")"
    iran_sha="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "RELEASE_SHA")"

    [[ "$foreign_sha" == "$expected_sha" ]] || die "Foreign runtime RELEASE_SHA does not match production HEAD"
    [[ "$iran_sha" == "$expected_sha" ]] || die "Iran runtime RELEASE_SHA does not match production HEAD"
}

summarize_web_push_env_file() {
    local env_path="$1"
    local role_label="$2"
    local enabled public_key private_key subject ttl timeout

    enabled="$(read_env_value "$env_path" "WEB_PUSH_ENABLED")"
    public_key="$(read_env_value "$env_path" "WEB_PUSH_VAPID_PUBLIC_KEY")"
    private_key="$(read_env_value "$env_path" "WEB_PUSH_VAPID_PRIVATE_KEY")"
    subject="$(read_env_value "$env_path" "WEB_PUSH_VAPID_SUBJECT")"
    ttl="$(read_env_value "$env_path" "WEB_PUSH_TTL_SECONDS")"
    timeout="$(read_env_value "$env_path" "WEB_PUSH_TIMEOUT_SECONDS")"

    log "$role_label Web Push env: WEB_PUSH_ENABLED=${enabled:-EMPTY} VAPID_PUBLIC_KEY=$(env_value_state "$public_key") VAPID_PRIVATE_KEY=$(env_value_state "$private_key") VAPID_SUBJECT=$(env_value_state "$subject") TTL=${ttl:-EMPTY} TIMEOUT=${timeout:-EMPTY}"
}

validate_web_push_env_file() {
    local env_path="$1"
    local role_label="$2"
    [[ -f "$env_path" ]] || die "Missing runtime env for $role_label: $env_path"

    local enabled subject
    enabled="$(read_env_value "$env_path" "WEB_PUSH_ENABLED")"
    if is_truthy "$enabled"; then
        require_env_value "$env_path" "WEB_PUSH_VAPID_PUBLIC_KEY"
        require_env_value "$env_path" "WEB_PUSH_VAPID_PRIVATE_KEY"
        require_env_value "$env_path" "WEB_PUSH_VAPID_SUBJECT"
        subject="$(read_env_value "$env_path" "WEB_PUSH_VAPID_SUBJECT")"
        case "$subject" in
            mailto:*|http://*|https://*) ;;
            *) die "$role_label WEB_PUSH_VAPID_SUBJECT must start with mailto:, http://, or https:// in $env_path" ;;
        esac
        return 0
    fi

    if is_truthy "$REQUIRE_WEB_PUSH"; then
        die "$role_label env has REQUIRE_WEB_PUSH=1 but WEB_PUSH_ENABLED is not true in $env_path"
    fi
}

export_runtime_renderer_overrides() {
    local key
    local keys=(
        PUBLIC_WEBAPP_URL
        FOREIGN_SERVER_ALIASES
        IRAN_SERVER_ALIASES
        TELEGRAM_DIRECT_REGISTRATION_ENABLED
        TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED
        TELEGRAM_LOGIN_OTP_ENABLED
        OTP_SMS_AUTO_FALLBACK_ENABLED
        OTP_SMS_AUTO_FALLBACK_SECONDS
        OTP_TTL_SECONDS
        TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS
        TELEGRAM_REGISTRATION_JOB_BATCH_SIZE
        TELEGRAM_REGISTRATION_JOB_CONCURRENCY
        OTP_SMS_FALLBACK_JOB_CONCURRENCY
        INVITATION_SMS_STANDARD_ENABLED
        INVITATION_SMS_CUSTOMER_TIER1_ENABLED
        INVITATION_SMS_ACCOUNTANT_ENABLED
        INVITATION_SMS_CUSTOMER_TIER2_ENABLED
        INVITATION_CONTRACT_V2_ENABLED
        REGISTRATION_SYNC_V2_ENABLED
        REGISTRATION_SYNC_ACCEPT_UNVERSIONED
        INVITATION_PUBLIC_RATE_LIMIT_PER_MINUTE
        OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED
        RELEASE_SHA
        DB_POOL_SIZE
        DB_MAX_OVERFLOW
        IRAN_DB_POOL_SIZE
        IRAN_DB_MAX_OVERFLOW
        DB_POOL_RECYCLE_SECONDS
        DB_POOL_PRE_PING
        BACKGROUND_LEADER_LOCK_TTL_SECONDS
        BACKGROUND_LEADER_LOCK_REFRESH_SECONDS
        BACKGROUND_LEADER_RETRY_SECONDS
        POSTGRES_MAX_CONNECTIONS
        POSTGRES_SHARED_BUFFERS
        POSTGRES_EFFECTIVE_CACHE_SIZE
        POSTGRES_WORK_MEM
        POSTGRES_MAINTENANCE_WORK_MEM
        POSTGRES_RANDOM_PAGE_COST
        POSTGRES_EFFECTIVE_IO_CONCURRENCY
        POSTGRES_CHECKPOINT_TIMEOUT
        POSTGRES_MAX_WAL_SIZE
        POSTGRES_MIN_WAL_SIZE
        POSTGRES_WAL_BUFFERS
        IRAN_POSTGRES_MAX_CONNECTIONS
        IRAN_POSTGRES_SHARED_BUFFERS
        IRAN_POSTGRES_EFFECTIVE_CACHE_SIZE
        IRAN_POSTGRES_WORK_MEM
        IRAN_POSTGRES_MAINTENANCE_WORK_MEM
        IRAN_POSTGRES_RANDOM_PAGE_COST
        IRAN_POSTGRES_EFFECTIVE_IO_CONCURRENCY
        IRAN_POSTGRES_CHECKPOINT_TIMEOUT
        IRAN_POSTGRES_MAX_WAL_SIZE
        IRAN_POSTGRES_MIN_WAL_SIZE
        IRAN_POSTGRES_WAL_BUFFERS
        REDIS_APPENDONLY
        REDIS_APPENDFSYNC
        REDIS_MAXMEMORY
        REDIS_MAXMEMORY_POLICY
    )
    for key in "${keys[@]}"; do
        if [[ -v "$key" ]]; then
            export "$key"
        fi
    done
}

validate_observability_env_file() {
    local env_path="$1"
    local role_label="$2"
    [[ -f "$env_path" ]] || die "Missing runtime env for $role_label: $env_path"

    require_env_value "$env_path" "TRUSTED_PROXY_CIDRS"
    require_env_value "$env_path" "OBSERVABILITY_TELEGRAM_USER_HASH_SALT"
    require_env_value "$env_path" "GRAFANA_ALERT_DEFAULT_RECEIVER"
    require_env_value "$env_path" "GRAFANA_ALERT_CRITICAL_RECEIVER"
    require_env_value "$env_path" "GRAFANA_ALERT_WARNING_RECEIVER"
    require_env_value "$env_path" "GRAFANA_ALERT_WEBHOOK_URL"
    require_env_value "$env_path" "GRAFANA_ALERT_EMAIL_ADDRESSES"

    local trusted_proxy_cidrs
    trusted_proxy_cidrs="$(read_env_value "$env_path" "TRUSTED_PROXY_CIDRS")"
    local hash_salt
    hash_salt="$(read_env_value "$env_path" "OBSERVABILITY_TELEGRAM_USER_HASH_SALT")"
    local default_receiver
    default_receiver="$(read_env_value "$env_path" "GRAFANA_ALERT_DEFAULT_RECEIVER")"
    local critical_receiver
    critical_receiver="$(read_env_value "$env_path" "GRAFANA_ALERT_CRITICAL_RECEIVER")"
    local warning_receiver
    warning_receiver="$(read_env_value "$env_path" "GRAFANA_ALERT_WARNING_RECEIVER")"
    local webhook_url
    webhook_url="$(read_env_value "$env_path" "GRAFANA_ALERT_WEBHOOK_URL")"
    local email_addresses
    email_addresses="$(read_env_value "$env_path" "GRAFANA_ALERT_EMAIL_ADDRESSES")"

    [[ "$trusted_proxy_cidrs" != "127.0.0.1/32,::1/128" ]] || die "$role_label env still uses loopback-only TRUSTED_PROXY_CIDRS. Set the real trusted reverse-proxy CIDRs in $env_path"
    [[ -n "$hash_salt" ]] || die "$role_label env is missing OBSERVABILITY_TELEGRAM_USER_HASH_SALT in $env_path"
    [[ "$default_receiver" != "Trading Bot Local Webhook" ]] || die "$role_label env still uses the local default alert receiver in $env_path"
    [[ "$critical_receiver" != "Trading Bot Local Webhook" ]] || die "$role_label env still uses the local critical alert receiver in $env_path"
    [[ "$warning_receiver" != "Trading Bot Local Webhook" ]] || die "$role_label env still uses the local warning alert receiver in $env_path"
    [[ "$webhook_url" != "http://127.0.0.1:9/trading-bot-alerts-disabled" ]] || die "$role_label env still uses the disabled Grafana webhook URL in $env_path"
    [[ "$email_addresses" != "alerts@example.invalid" ]] || die "$role_label env still uses the placeholder Grafana email addresses in $env_path"
}

validate_observability_release_inputs() {
    validate_observability_env_file "$FOREIGN_RUNTIME_ENV_PATH" "Foreign"
    validate_observability_env_file "$IRAN_RUNTIME_ENV_PATH" "Iran"
    summarize_web_push_env_file "$FOREIGN_RUNTIME_ENV_PATH" "Foreign"
    validate_web_push_env_file "$FOREIGN_RUNTIME_ENV_PATH" "Foreign"
    summarize_web_push_env_file "$IRAN_RUNTIME_ENV_PATH" "Iran"
    validate_web_push_env_file "$IRAN_RUNTIME_ENV_PATH" "Iran"
}

install_sync_sampler_local() {
    log "Ensuring foreign sync health sampler is installed"
    (cd "$LOCAL_PROJECT_DIR" && bash ./scripts/install_sync_health_monitor.sh)
}

install_sync_sampler_remote() {
    log "Ensuring Iran sync health sampler is installed"
    ssh_iran "set -euo pipefail
cd '$IRAN_PROJECT_DIR'
SYNC_HEALTH_MONITOR_SKIP_IRAN=1 bash ./scripts/install_sync_health_monitor.sh"
}

verify_sync_sampler_local() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl is-enabled trading-bot-sync-health-sampler.timer >/dev/null 2>&1 || die "Foreign sync sampler timer is not enabled"
        systemctl is-active trading-bot-sync-health-sampler.timer >/dev/null 2>&1 || die "Foreign sync sampler timer is not active"
        return 0
    fi
    grep -R "sample_sync_health.py" /etc/cron.d /var/spool/cron >/dev/null 2>&1 || die "Foreign sync sampler is not installed via cron"
}

verify_sync_sampler_remote() {
    ssh_iran "set -euo pipefail
if command -v systemctl >/dev/null 2>&1; then
  systemctl is-enabled trading-bot-sync-health-sampler.timer >/dev/null 2>&1 || exit 11
  systemctl is-active trading-bot-sync-health-sampler.timer >/dev/null 2>&1 || exit 12
else
  grep -R 'sample_sync_health.py' /etc/cron.d /var/spool/cron >/dev/null 2>&1 || exit 13
fi" || die "Iran sync sampler is not installed and active"
}

ensure_local_tools() {
    need_cmd ssh
    need_cmd scp
    need_cmd rsync
    need_cmd git
    need_cmd python3
    need_cmd md5sum
    need_cmd sha256sum
    need_cmd sed
}

ensure_clean_release_tree() {
    if [[ "$IRAN_ALLOW_DIRTY_RELEASE" == "1" ]]; then
        log "IRAN_ALLOW_DIRTY_RELEASE=1; allowing production release from a dirty working tree."
        return 0
    fi

    local status_output
    status_output="$(git -C "$LOCAL_PROJECT_DIR" status --porcelain --untracked-files=all)"
    if [[ -n "$status_output" ]]; then
        printf '%s\n' "$status_output" | sed -n '1,40p' >&2
        die "Production release requires a clean git working tree because rsync deploys local files. Commit, stash, or set IRAN_ALLOW_DIRTY_RELEASE=1 explicitly."
    fi
}

ensure_production_release_git_ref() {
    if ! git -C "$LOCAL_PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        die "Production release must run from a Git checkout so branch identity can be verified."
    fi

    local branch head_sha upstream upstream_sha origin_main_sha remote_main_binding
    branch="$(git -C "$LOCAL_PROJECT_DIR" symbolic-ref --short HEAD 2>/dev/null || true)"
    head_sha="$(git -C "$LOCAL_PROJECT_DIR" rev-parse --short HEAD)"
    RELEASE_SHA="$(git -C "$LOCAL_PROJECT_DIR" rev-parse HEAD)"
    PRODUCTION_RELEASE_TREE="$(git -C "$LOCAL_PROJECT_DIR" rev-parse 'HEAD^{tree}')"
    export RELEASE_SHA PRODUCTION_RELEASE_TREE

    if [[ "$IRAN_ALLOW_NON_MAIN_RELEASE" == "1" ]]; then
        log "IRAN_ALLOW_NON_MAIN_RELEASE=1; allowing production release from branch ${branch:-detached} at $head_sha."
    elif [[ "$branch" != "$PRODUCTION_RELEASE_BRANCH" ]]; then
        die "Production release must run from '$PRODUCTION_RELEASE_BRANCH' (current: ${branch:-detached}, sha: $head_sha). Merge the intended candidate/hotfix to $PRODUCTION_RELEASE_BRANCH first, or set IRAN_ALLOW_NON_MAIN_RELEASE=1 for an explicit emergency override."
    fi

    upstream="$(git -C "$LOCAL_PROJECT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    [[ "$upstream" == "origin/main" ]] \
        || die "Production release branch 'main' must track origin/main exactly (current upstream: ${upstream:-missing})."

    upstream_sha="$(git -C "$LOCAL_PROJECT_DIR" rev-parse --short "$upstream" 2>/dev/null || true)"
    if [[ -z "$upstream_sha" ]]; then
        die "Unable to resolve upstream '$upstream' for production release branch verification."
    fi

    if [[ "$IRAN_ALLOW_RELEASE_BRANCH_DRIFT" == "1" ]]; then
        log "IRAN_ALLOW_RELEASE_BRANCH_DRIFT=1; allowing local HEAD $head_sha to differ from $upstream $upstream_sha."
        return 0
    fi

    if [[ "$(git -C "$LOCAL_PROJECT_DIR" rev-parse HEAD)" != "$(git -C "$LOCAL_PROJECT_DIR" rev-parse "$upstream")" ]]; then
        die "Production release branch '$branch' must match upstream '$upstream' exactly (local: $head_sha, upstream: $upstream_sha). Push/pull first, or set IRAN_ALLOW_RELEASE_BRANCH_DRIFT=1 for an explicit emergency override."
    fi

    origin_main_sha="$(git -C "$LOCAL_PROJECT_DIR" rev-parse --verify 'refs/remotes/origin/main^{commit}' 2>/dev/null || true)"
    [[ "$origin_main_sha" == "$RELEASE_SHA" ]] \
        || die "Production release HEAD must match the local origin/main tracking ref exactly. Fetch/push first."
    remote_main_binding="$(
        git -C "$LOCAL_PROJECT_DIR" ls-remote --exit-code origin refs/heads/main 2>/dev/null \
            | awk 'NF { print $1 " " $2 }'
    )" || die "Unable to verify the live origin/main binding."
    [[ "$remote_main_binding" == "$RELEASE_SHA refs/heads/main" ]] \
        || die "Production release HEAD must be pushed uniquely to the live origin/main ref."
}

verify_frozen_release_source() {
    [[ -n "$RELEASE_SHA" && -n "$PRODUCTION_RELEASE_TREE" ]] \
        || die "Release Git identity is not initialized."
    [[ "$IRAN_ALLOW_DIRTY_RELEASE" == "0" \
        && "$IRAN_ALLOW_NON_MAIN_RELEASE" == "0" \
        && "$IRAN_ALLOW_RELEASE_BRANCH_DRIFT" == "0" ]] \
        || die "Official two-host release cannot use Git safety overrides."
    [[ "$(git -C "$LOCAL_PROJECT_DIR" rev-parse HEAD)" == "$RELEASE_SHA" \
        && "$(git -C "$LOCAL_PROJECT_DIR" rev-parse 'HEAD^{tree}')" == "$PRODUCTION_RELEASE_TREE" \
        && "$(git -C "$LOCAL_PROJECT_DIR" symbolic-ref --short HEAD)" == "$PRODUCTION_RELEASE_BRANCH" \
        && "$(git -C "$LOCAL_PROJECT_DIR" rev-parse '@{u}')" == "$RELEASE_SHA" \
        && -z "$(git -C "$LOCAL_PROJECT_DIR" status --porcelain --untracked-files=all)" ]] \
        || die "Immutable pushed production source drifted during release."
}

local_node_version_ok() {
    local version major minor
    command -v node >/dev/null 2>&1 || return 1
    version="$(node -p 'process.versions.node' 2>/dev/null || true)"
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
    major="${version%%.*}"
    minor="${version#*.}"
    minor="${minor%%.*}"

    if (( major > 22 )); then
        return 0
    fi
    if (( major == 22 && minor >= 12 )); then
        return 0
    fi
    if (( major == 20 && minor >= 19 )); then
        return 0
    fi
    return 1
}

install_local_node_runtime() {
    local node_version="${DEPLOY_NODE_VERSION:-22.12.0}"
    local node_arch install_root archive_url tmp_dir archive_path extracted_dir

    case "$(normalize_arch "$(uname -m)")" in
        amd64) node_arch="x64" ;;
        arm64) node_arch="arm64" ;;
        *) die "Unsupported local Node.js architecture: $(uname -m)" ;;
    esac

    log "Installing local Node.js $node_version for frontend production builds"
    export DEBIAN_FRONTEND=noninteractive
    apt-get -o Acquire::Retries=5 update
    apt-get -o Acquire::Retries=5 install -y ca-certificates curl xz-utils

    install_root="/usr/local/lib/nodejs"
    tmp_dir="$RELEASE_TMP_DIR/nodejs"
    archive_url="https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-${node_arch}.tar.xz"
    archive_path="$tmp_dir/node-v${node_version}-linux-${node_arch}.tar.xz"
    extracted_dir="$install_root/node-v${node_version}-linux-${node_arch}"

    mkdir -p "$tmp_dir" "$install_root"
    curl -fsSL "$archive_url" -o "$archive_path"
    rm -rf "$extracted_dir"
    tar -xJf "$archive_path" -C "$install_root"
    ln -sfn "$extracted_dir/bin/node" /usr/local/bin/node
    ln -sfn "$extracted_dir/bin/npm" /usr/local/bin/npm
    ln -sfn "$extracted_dir/bin/npx" /usr/local/bin/npx
    hash -r

    local_node_version_ok || die "Installed Node.js is still too old for the frontend build: $(node --version 2>/dev/null || true)"
    npm --version >/dev/null 2>&1 || die "npm is unavailable after local Node.js installation"
}

ensure_local_runtime_packages() {
    local missing_packages=()
    local need_docker=0
    local need_node_runtime=0
    local need_pip=0
    local need_buildx=0

    if ! command -v docker >/dev/null 2>&1; then
        need_docker=1
        missing_packages+=(docker.io docker-compose-plugin docker-buildx-plugin)
    elif ! docker compose version >/dev/null 2>&1; then
        need_docker=1
        missing_packages+=(docker-compose-plugin)
    fi

    if ! docker buildx version >/dev/null 2>&1; then
        need_buildx=1
        missing_packages+=(docker-buildx-plugin)
    fi

    if ! local_node_version_ok || ! command -v npm >/dev/null 2>&1; then
        need_node_runtime=1
    fi

    if ! python3 -m pip --version >/dev/null 2>&1; then
        need_pip=1
        missing_packages+=(python3-pip)
    fi

    if [[ ${#missing_packages[@]} -gt 0 ]]; then
        log "Installing missing local packages: ${missing_packages[*]}"
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y "${missing_packages[@]}"
        if [[ $need_docker -eq 1 ]]; then
            systemctl enable --now docker || true
        fi
    fi

    if [[ $need_node_runtime -eq 1 ]]; then
        install_local_node_runtime
    fi

    need_cmd docker
    need_cmd npm
    local_node_version_ok || die "Node.js $(node --version 2>/dev/null || true) is too old. Frontend build requires Node.js 20.19+ or 22.12+."
    python3 -m pip --version >/dev/null 2>&1 || die "python3-pip is still unavailable after local installation"
    if [[ $need_buildx -eq 1 ]]; then
        docker buildx version >/dev/null 2>&1 || die "docker buildx is still unavailable after local installation"
    fi
}

check_local() {
    log "Checking local prerequisites (read-only)"
    ensure_local_tools
    [[ "$(id -u)" -eq 0 ]] || die "This release script must be run as root so it can update /etc/hosts and manage Docker."
    ensure_clean_release_tree
    ensure_production_release_git_ref
    ssh_iran "echo connected-to-\$(hostname)"
    detect_runtime_metadata
    [[ -f "$LOCAL_PROJECT_DIR/requirements.txt" ]] || die "requirements.txt missing"
    [[ -f "$LOCAL_PROJECT_DIR/docker-compose.iran.yml" ]] || die "docker-compose.iran.yml missing"
    [[ -f "$LOCAL_PROJECT_DIR/Dockerfile.iran" ]] || die "Dockerfile.iran missing"
    [[ -f "$PROJECT_DIR/deploy/production/nginx-iran-online.conf.template" ]] || die "Nginx template missing"
    [[ -f "$RELEASE_ARTIFACT_RENDERER" ]] || die "Release artifact renderer missing: $RELEASE_ARTIFACT_RENDERER"
    [[ -f "$CHANGE_LOG_SOURCE_SEQUENCE_ALIGNER" ]] || die "Change-log source sequence aligner missing: $CHANGE_LOG_SOURCE_SEQUENCE_ALIGNER"
    [[ -f "$TRADE_NUMBER_SEQUENCE_ALIGNER" ]] || die "Trade-number sequence aligner missing: $TRADE_NUMBER_SEQUENCE_ALIGNER"
    validate_runtime_env_source_policy
    validate_secure_runtime_env_source_file \
        || die "Immutable production runtime env source must be canonical, owner-controlled, non-symlink, and mode 0600."
    validate_production_coin_inference_activation_contract
    validate_production_coin_relay_manifest
    log "Read-only local checks passed"
}

prepare_local_release_inputs() {
    # This is intentionally separate from check_local: package installation,
    # rendered env creation, and release-artifact rendering are mutations and
    # may only run after the production operation/source locks are held.
    ensure_local_runtime_packages
    check_local
    ensure_runtime_env_file
    render_release_artifacts
    validate_observability_release_inputs
    log "Mutable local release preparation passed"
}

prepare_iran_package_bundle() {
    if [[ "$IRAN_APT_BUNDLE_MODE" != "same-arch" ]]; then
        log "Skipping foreign-built Iran apt bundle because apt identity differs (foreign=${LOCAL_DPKG_ARCH}/${LOCAL_OS_CODENAME:-unknown} iran=${IRAN_DPKG_ARCH}/${IRAN_OS_CODENAME:-unknown})."
        return 0
    fi

    local bundle_dir="$RELEASE_TMP_DIR/iran-packages"
    local bundle_tar="$RELEASE_TMP_DIR/iran-packages.tar.gz"
    local bundle_hash_file="$RELEASE_TMP_DIR/iran-packages.sha256"
    local bundle_signature
    bundle_signature="$(printf '%s\n%s\n%s\n%s\n' "$IRAN_OS_CODENAME" "$IRAN_IMAGE_PLATFORM" "$IRAN_BOOTSTRAP_APT_PACKAGES" "$IRAN_BOOTSTRAP_COMPOSE_PACKAGES" | sha256sum | cut -d' ' -f1)"

    if [[ -f "$bundle_tar" && -f "$bundle_hash_file" && "$(cat "$bundle_hash_file")" == "$bundle_signature" ]]; then
        return 0
    fi

    log "Preparing Iran bootstrap packages locally"
    rm -rf "$bundle_dir"
    rm -f "$bundle_tar" "$bundle_hash_file"
    mkdir -p "$bundle_dir"
    chmod 755 "$bundle_dir"
    mkdir -p "$bundle_dir/partial"
    if id -u _apt >/dev/null 2>&1; then
        chown _apt:root "$bundle_dir/partial" 2>/dev/null || true
        chmod 700 "$bundle_dir/partial" 2>/dev/null || true
    fi

    local ubuntu_image="ubuntu:${IRAN_OS_CODENAME:-noble}"
    log "Downloading Iran bootstrap packages in a clean container image=$ubuntu_image platform=$IRAN_IMAGE_PLATFORM"
    docker run --rm \
        --platform "$IRAN_IMAGE_PLATFORM" \
        -v "$bundle_dir:/bundle" \
        "$ubuntu_image" \
        bash -lc "set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
compose_package=''
for candidate in $IRAN_BOOTSTRAP_COMPOSE_PACKAGES; do
  if apt-cache show "\$candidate" >/dev/null 2>&1; then
    compose_package="\$candidate"
    break
  fi
done
[ -n "\$compose_package" ] || { echo 'No supported Docker Compose package is available.' >&2; exit 1; }
apt-get -o Acquire::Retries=5 -y --download-only -o Dir::Cache::archives=/bundle install $IRAN_BOOTSTRAP_APT_PACKAGES "\$compose_package""
    tar -C "$bundle_dir" -czf "$bundle_tar" .
    printf '%s\n' "$bundle_signature" > "$bundle_hash_file"
    log "Iran bootstrap package bundle prepared at $bundle_tar"
}

bootstrap_iran() {
    log "Bootstrapping the Iran host"
    local bootstrap_ready_guard
    bootstrap_ready_guard="$(remote_bootstrap_ready_guard)"
    local post_bootstrap_guard
    post_bootstrap_guard="$(remote_post_bootstrap_guard)"
    local docker_cleanup_guard
    docker_cleanup_guard="$(remote_docker_cleanup_guard)"
    local docker_service_guard
    docker_service_guard="$(remote_docker_service_guard)"
    ssh_iran "mkdir -p '$IRAN_DEPLOY_BASE_DIR' '$IRAN_DEPLOY_BASE_DIR/releases' '$IRAN_PROJECT_DIR'"
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" ]] && ssh_iran "$bootstrap_ready_guard"; then
        log "Iran bootstrap prerequisites already satisfied; skipping package upload/install."
        return 0
    fi
    if [[ "$IRAN_APT_BUNDLE_MODE" == "same-arch" ]]; then
        prepare_iran_package_bundle
        scp_iran "$RELEASE_TMP_DIR/iran-packages.tar.gz" "$IRAN_SSH_TARGET:$IRAN_DEPLOY_BASE_DIR/releases/iran-packages.tar.gz"
        ssh_iran "export DEBIAN_FRONTEND=noninteractive
set -euo pipefail
package_dir='$IRAN_DEPLOY_BASE_DIR/releases/iran-packages'
package_tar='$IRAN_DEPLOY_BASE_DIR/releases/iran-packages.tar.gz'
rm -rf \"\$package_dir\"
mkdir -p \"\$package_dir\"
tar -xzf \"\$package_tar\" -C \"\$package_dir\"
$docker_cleanup_guard
if ! apt-get install -y --no-download \"\$package_dir\"/*.deb; then
  apt-get -o Acquire::Retries=5 update
  apt-get -o Acquire::Retries=5 install -y --fix-missing \"\$package_dir\"/*.deb
fi
$docker_service_guard
systemctl enable --now nginx
python3 -m pip --version >/dev/null 2>&1 || true
timedatectl set-timezone 'UTC' || true
if command -v ufw >/dev/null 2>&1 && [ '$IRAN_ENABLE_UFW' = '1' ]; then
  ufw allow OpenSSH || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
fi
$post_bootstrap_guard"
else
        ssh_iran "export DEBIAN_FRONTEND=noninteractive
set -euo pipefail
apt-get -o Acquire::Retries=5 update
$docker_cleanup_guard
compose_package=''
for candidate in $IRAN_BOOTSTRAP_COMPOSE_PACKAGES; do
  if apt-cache show "\$candidate" >/dev/null 2>&1; then
    compose_package="\$candidate"
    break
  fi
done
[ -n "\$compose_package" ] || { echo 'No supported Docker Compose package is available.' >&2; exit 1; }
apt-get -o Acquire::Retries=5 install -y --fix-missing $IRAN_BOOTSTRAP_APT_PACKAGES "\$compose_package"
$docker_service_guard
systemctl enable --now nginx
python3 -m pip --version >/dev/null 2>&1 || true
timedatectl set-timezone 'UTC' || true
if command -v ufw >/dev/null 2>&1 && [ '$IRAN_ENABLE_UFW' = '1' ]; then
  ufw allow OpenSSH || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
fi
$post_bootstrap_guard"
    fi
    log "Iran host bootstrap complete"
}

render_release_artifacts() {
    local template="$PROJECT_DIR/deploy/production/nginx-iran-online.conf.template"
    mkdir -p "$RELEASE_ARTIFACT_DIR"
    python3 "$RELEASE_ARTIFACT_RENDERER" \
        --manifest "$MANIFEST_PATH" \
        --template "$template" \
        --output-dir "$RELEASE_ARTIFACT_DIR" >/dev/null
}

render_nginx_config() {
    render_release_artifacts
    printf '%s\n' "$RELEASE_ARTIFACT_DIR/iran-online-nginx.conf"
}

render_nginx_https_config() {
    local template="$PROJECT_DIR/deploy/production/nginx-iran-online-https.conf.template"
    mkdir -p "$RELEASE_ARTIFACT_DIR"
    python3 "$RELEASE_ARTIFACT_RENDERER" \
        --manifest "$MANIFEST_PATH" \
        --template "$template" \
        --output-dir "$RELEASE_ARTIFACT_DIR" >/dev/null
    printf '%s\n' "$RELEASE_ARTIFACT_DIR/iran-online-nginx.conf"
}

configure_nginx() {
    log "Rendering and installing Iran Nginx config"
    local rendered
    rendered="$(render_nginx_config)"
    scp_iran "$rendered" "$IRAN_SSH_TARGET:/etc/nginx/sites-available/trading-bot"
    ssh_iran "set -euo pipefail
ln -sf /etc/nginx/sites-available/trading-bot /etc/nginx/sites-enabled/trading-bot
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx"
    log "Iran Nginx config installed"
}

configure_nginx_https() {
    log "Rendering and installing Iran HTTPS Nginx config"
    local rendered
    rendered="$(render_nginx_https_config)"
    scp_iran "$rendered" "$IRAN_SSH_TARGET:/etc/nginx/sites-available/trading-bot"
    ssh_iran "set -euo pipefail
ln -sf /etc/nginx/sites-available/trading-bot /etc/nginx/sites-enabled/trading-bot
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx"
    log "Iran HTTPS Nginx config installed"
}

issue_cert() {
    if [[ "$IRAN_SKIP_CERTBOT" == "1" ]]; then
        log "Skipping certbot because IRAN_SKIP_CERTBOT=1"
        return 0
    fi
    log "Requesting/renewing SSL certificate on the Iran host"
    local cert_renewal_guard
    cert_renewal_guard="$(remote_cert_renewal_guard)"
    ssh_iran "set -euo pipefail
domain='$IRAN_APP_DOMAIN'
email='$IRAN_CERTBOT_EMAIL'
cert_path=\"/etc/letsencrypt/live/\$domain/fullchain.pem\"
run_certbot() {
  certbot --nginx -d \"\$domain\" --non-interactive --agree-tos --email \"\$email\" --redirect --keep-until-expiring
}
if [ -f \"\$cert_path\" ] && openssl x509 -checkend 1814400 -noout -in \"\$cert_path\" >/dev/null 2>&1; then
  if ! run_certbot; then
    echo \"WARN: certbot failed for \$domain, but the existing certificate is valid for more than 21 days; continuing.\" >&2
  fi
else
  certbot_status=1
  for attempt in 1 2 3; do
    if run_certbot; then
      certbot_status=0
      break
    fi
    echo \"WARN: certbot attempt \$attempt failed for \$domain; retrying.\" >&2
    sleep \$((attempt * 10))
  done
  if [ \"\$certbot_status\" -ne 0 ]; then
    exit \"\$certbot_status\"
  fi
fi
$cert_renewal_guard"
    configure_nginx_https
    assert_iran_public_listener_ready
    log "SSL certificate step completed"
}

hosts_block() {
    render_release_artifacts
    cat "$RELEASE_ARTIFACT_DIR/hosts.block"
}

filter_hosts_file_for_managed_domains() {
    local source_file="$1"
    local output_file="$2"
    awk \
        -v start_marker="# trading-bot-production-hosts START" \
        -v end_marker="# trading-bot-production-hosts END" \
        -v foreign_domain="$FOREIGN_PUBLIC_DOMAIN" \
        -v iran_domain="$IRAN_PUBLIC_DOMAIN" '
        $0 == start_marker { in_managed_block = 1; next }
        $0 == end_marker { in_managed_block = 0; next }
        in_managed_block { next }
        /^[[:space:]]*($|#)/ { print; next }
        {
            for (i = 2; i <= NF; i++) {
                if ($i == foreign_domain || $i == iran_domain) {
                    next
                }
            }
            print
        }
    ' "$source_file" > "$output_file"
}

replace_hosts_block_local() {
    local hosts_file="/etc/hosts"
    local block
    block="$(hosts_block)"
    local tmp
    tmp="$(mktemp)"
    filter_hosts_file_for_managed_domains "$hosts_file" "$tmp"
    printf '\n%s\n' "$block" >> "$tmp"
    cp "$tmp" "$hosts_file"
    chown root:root "$hosts_file"
    chmod 0644 "$hosts_file"
    rm -f "$tmp"
}

replace_hosts_block_remote() {
    local block
    block="$(hosts_block)"
    ssh_iran "set -euo pipefail
hosts_file='/etc/hosts'
tmp=\$(mktemp)
awk -v start_marker='# trading-bot-production-hosts START' \\
    -v end_marker='# trading-bot-production-hosts END' \\
    -v foreign_domain='$FOREIGN_PUBLIC_DOMAIN' \\
    -v iran_domain='$IRAN_PUBLIC_DOMAIN' '
  \$0 == start_marker { in_managed_block = 1; next }
  \$0 == end_marker { in_managed_block = 0; next }
  in_managed_block { next }
  /^[[:space:]]*(\$|#)/ { print; next }
  {
    for (i = 2; i <= NF; i++) {
      if (\$i == foreign_domain || \$i == iran_domain) {
        next
      }
    }
    print
  }
' \"\$hosts_file\" > \"\$tmp\"
cat >> \"\$tmp\" <<'EOF_HOSTS'
$block
EOF_HOSTS
cp \"\$tmp\" \"\$hosts_file\"
chown root:root \"\$hosts_file\"
chmod 0644 \"\$hosts_file\"
rm -f \"\$tmp\""
}

ensure_local_timezone_utc() {
    log "Ensuring foreign host timezone is UTC"
    timedatectl set-timezone 'UTC' || true
}

sync_hosts_mappings() {
    if [[ "$IRAN_HOSTS_SYNC_ENABLED" != "1" ]]; then
        log "Skipping /etc/hosts sync because IRAN_HOSTS_SYNC_ENABLED=0"
        return 0
    fi
    log "Synchronizing host mappings on foreign and Iran"
    replace_hosts_block_local
    replace_hosts_block_remote
    log "Host mappings synchronized"
}

prepare_pip_packages() {
    local target_arch="$1"
    local output_dir="$2"
    local hash_file="$3"
    log "Preparing wheel cache for arch=$target_arch at $output_dir"
    local current_hash
    local hash_material
    local bootstrap_requirements="$LOCAL_PROJECT_DIR/deploy/production/pip-bootstrap-requirements.txt"
    [[ -f "$bootstrap_requirements" ]] || die "Missing bootstrap wheel requirements: $bootstrap_requirements"
    hash_material="$(
        {
            md5sum "$LOCAL_PROJECT_DIR/requirements.txt"
            md5sum "$bootstrap_requirements"
        } | md5sum | cut -d' ' -f1
    )-$target_arch"
    current_hash="$hash_material"
    mkdir -p "$output_dir"
    local needs_refresh=0
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" == "1" || ! -f "$hash_file" || "$(cat "$hash_file")" != "$current_hash" ]]; then
        needs_refresh=1
    fi
    if [[ "$needs_refresh" == "0" && "$target_arch" == "$LOCAL_HOST_ARCH" ]]; then
        if ! docker run --rm \
            -v "$output_dir:/tmp/pip_packages:ro" \
            -v "$bootstrap_requirements:/tmp/pip-bootstrap-requirements.txt:ro" \
            -v "$LOCAL_PROJECT_DIR/requirements.txt:/tmp/requirements.txt:ro" \
            "python:3.11-slim-bullseye" sh -lc 'python -m pip install --no-cache-dir --no-index --find-links=/tmp/pip_packages -r /tmp/pip-bootstrap-requirements.txt >/dev/null && python -m pip install --no-cache-dir --no-index --find-links=/tmp/pip_packages -r /tmp/requirements.txt --target /tmp/pip-verify >/dev/null'; then
            log "Existing wheel cache failed validation; rebuilding it."
            needs_refresh=1
        fi
    fi
    if [[ "$needs_refresh" == "1" ]]; then
        rm -f "$output_dir"/*.whl "$output_dir"/*.tar.gz "$output_dir"/*.zip "$output_dir"/.requirements_hash 2>/dev/null || true
        mapfile -t pip_platform_args < <(append_pip_platform_args "$target_arch")
        python3 -m pip download -r "$bootstrap_requirements" \
            -d "$output_dir/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            "${pip_platform_args[@]}" \
            --only-binary=:all:
        # http-ece does not publish wheels, but the built wheel is pure Python.
        # Build it locally first so the platform-restricted binary download can
        # resolve pywebpush without using the pip-conflicting --no-binary flag.
        python3 -m pip wheel --no-deps "http-ece==1.2.1" \
            -w "$output_dir/"
        python3 -m pip download -r "$LOCAL_PROJECT_DIR/requirements.txt" \
            -d "$output_dir/" \
            --find-links "$output_dir/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            "${pip_platform_args[@]}" \
            --only-binary=:all:
        printf '%s' "$current_hash" > "$hash_file"
    else
        log "Wheel cache already matches requirements for arch=$target_arch; skipping rebuild."
    fi
}

hash_context_entry() {
    local context_dir="$1"
    local rel_path="$2"
    local path="$context_dir/$rel_path"

    if [[ -f "$path" ]]; then
        (cd "$context_dir" && sha256sum "$rel_path")
    elif [[ -d "$path" ]]; then
        (cd "$context_dir" && find "$rel_path" -type f -print0 | LC_ALL=C sort -z | xargs -r -0 sha256sum)
    fi
}

build_image_bundle_signature() {
    local context_dir="$1"
    local rel_path

    {
        printf 'signature_scope=%s\n' "iran-immutable-runtime-image-v3"
        printf 'iran_image_platform=%s\n' "$IRAN_IMAGE_PLATFORM"
        printf 'iran_host_arch=%s\n' "$IRAN_HOST_ARCH"
        printf 'python_base_image=%s\n' "python:3.11-slim-bullseye"
        printf 'postgres_image=%s\n' "postgres:15-alpine"
        printf 'redis_image=%s\n' "redis:7-alpine"
        # Production containers execute only the receipt-bound image payload.
        # Every runtime source copied by Dockerfile.iran must be in this digest.
        for rel_path in \
            Dockerfile.iran \
            .dockerignore \
            requirements.txt \
            deploy/production/pip-bootstrap-requirements.txt \
            pip_packages \
            api \
            bot \
            core \
            src \
            migrations \
            models \
            fonts \
            templates \
            alembic.ini \
            main.py \
            manage.py \
            run_bot.py \
            schemas.py \
            seed_fake_data.py \
            trading_settings.json \
            scripts \
            mini_app_dist
        do
            hash_context_entry "$context_dir" "$rel_path"
        done
    } | sha256sum | cut -d' ' -f1
}

iran_release_image_matches() {
    local expected_signature="$1"
    [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' trading_bot_base_iran 2>/dev/null || true)" == "$RELEASE_SHA" \
        && "$(docker image inspect --format '{{index .Config.Labels "io.gold-trade.release.tree"}}' trading_bot_base_iran 2>/dev/null || true)" == "$PRODUCTION_RELEASE_TREE" \
        && "$(docker image inspect --format '{{index .Config.Labels "io.gold-trade.release.input-signature"}}' trading_bot_base_iran 2>/dev/null || true)" == "$expected_signature" ]]
}

frontend_build_signature() {
    {
        printf 'node=%s\n' "$(node -p 'process.versions.node' 2>/dev/null || true)"
        printf 'npm=%s\n' "$(npm --version 2>/dev/null || true)"
        env | LC_ALL=C sort | grep -E '^(VITE_|BASE_URL=|NODE_ENV=)' || true
        local rel path
        for rel in \
            package.json \
            package-lock.json \
            vite.config.ts \
            tsconfig.json \
            tsconfig.app.json \
            tsconfig.node.json \
            postcss.config.js \
            tailwind.config.js \
            index.html \
            public \
            src
        do
            path="$LOCAL_FRONTEND_DIR/$rel"
            if [[ -f "$path" ]]; then
                sha256sum "$path" | sed "s#  $LOCAL_FRONTEND_DIR/#  #"
            elif [[ -d "$path" ]]; then
                (cd "$LOCAL_FRONTEND_DIR" && find "$rel" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum)
            fi
        done
    } | sha256sum | cut -d' ' -f1
}

ensure_frontend_dist() {
    if [[ "$IRAN_SKIP_FRONTEND_BUILD" != "1" ]]; then
        local frontend_signature
        frontend_signature="$(frontend_build_signature)"
        if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && -f "$LOCAL_FRONTEND_SIGNATURE_FILE" && "$(cat "$LOCAL_FRONTEND_SIGNATURE_FILE")" == "$frontend_signature" && -f "$LOCAL_DIST_DIR/index.html" ]]; then
            log "Frontend dist already matches current build inputs; skipping npm build."
            return 0
        fi
        log "Building frontend locally"
        (cd "$LOCAL_FRONTEND_DIR" && if [[ -f package-lock.json ]]; then npm ci --silent; else npm install --silent; fi && NODE_OPTIONS="--max-old-space-size=1024" npm run build)
        mkdir -p "$RELEASE_TMP_DIR"
        printf '%s\n' "$frontend_signature" > "$LOCAL_FRONTEND_SIGNATURE_FILE"
    else
        log "Skipping frontend build because IRAN_SKIP_FRONTEND_BUILD=1"
    fi
}

verify_frontend_release_contracts() {
    local dist_dir="$1"
    local contract_name="market-terminal-offer-history"
    local endpoint_marker="api/offers/market-history"
    local assets_dir="$dist_dir/assets"
    local market_chunks

    [[ -d "$assets_dir" ]] || die "Frontend release contract failed: assets directory missing in $dist_dir"
    mapfile -t market_chunks < <(find "$assets_dir" -maxdepth 1 -type f -name 'MarketView-*.js' | LC_ALL=C sort)
    if [[ "${#market_chunks[@]}" -eq 0 ]]; then
        die "Frontend release contract failed [$contract_name]: MarketView chunk missing in $dist_dir"
    fi
    if ! grep -h -q "$endpoint_marker" "${market_chunks[@]}"; then
        die "Frontend release contract failed [$contract_name]: $endpoint_marker missing from MarketView bundle. Refusing to deploy a frontend that cannot load read-only terminal market offers."
    fi
    log "Frontend release contract passed [$contract_name]"
}

build_release() {
    ensure_frontend_dist
    verify_frontend_release_contracts "$LOCAL_DIST_DIR"
    prepare_pip_packages "$LOCAL_HOST_ARCH" "$LOCAL_PROJECT_DIR/pip_packages" "$LOCAL_PROJECT_DIR/pip_packages/.requirements_hash"
    [[ -d "$LOCAL_DIST_DIR" ]] || die "Frontend dist directory missing: $LOCAL_DIST_DIR"
    mkdir -p "$RELEASE_TMP_DIR"
    local iran_context_dir="$RELEASE_TMP_DIR/iran-build-context"
    local iran_pip_dir="$RELEASE_TMP_DIR/pip_packages-${IRAN_HOST_ARCH}"
    local iran_pip_hash="$iran_pip_dir/.requirements_hash"
    prepare_pip_packages "$IRAN_HOST_ARCH" "$iran_pip_dir" "$iran_pip_hash"
    rm -rf "$iran_context_dir"
    mkdir -p "$iran_context_dir"
    rsync -a --delete \
        --exclude '.git' \
        --exclude '.github' \
        --exclude '.env' \
        --exclude '.env.*' \
        --exclude '.deploy_count' \
        --exclude '.venv' \
        --exclude '.vscode' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude 'app_logs.txt' \
        --exclude 'repomix-output.xml' \
        --exclude 'docs' \
        --exclude 'frontend' \
        --exclude 'node_modules' \
        --exclude 'pip_packages' \
        --exclude 'tests' \
        --exclude 'tmp' \
        --exclude 'uploads' \
        --exclude 'map_data' \
        --exclude 'audit_trail' \
        --exclude 'pip_packages' \
        "$LOCAL_PROJECT_DIR/" "$iran_context_dir/"
    rsync -a --delete "$iran_pip_dir/" "$iran_context_dir/pip_packages/"

    local image_signature
    image_signature="$(build_image_bundle_signature "$iran_context_dir")"
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" \
        && -s "$LOCAL_IMAGE_BUNDLE" \
        && -f "$LOCAL_IMAGE_SIGNATURE_FILE" \
        && "$(cat "$LOCAL_IMAGE_SIGNATURE_FILE")" == "$image_signature" ]] \
        && iran_release_image_matches "$image_signature"; then
        log "Docker image bundle already matches current build inputs; skipping image build/save."
        return 0
    fi

    log "Building Docker images for Iran platform=$IRAN_IMAGE_PLATFORM"
    ensure_buildx_for_target
    if [[ "$LOCAL_HOST_ARCH" == "$IRAN_HOST_ARCH" ]]; then
        docker pull "postgres:15-alpine" >/dev/null
        docker pull "redis:7-alpine" >/dev/null
        docker build \
            --label "org.opencontainers.image.revision=$RELEASE_SHA" \
            --label "io.gold-trade.release.tree=$PRODUCTION_RELEASE_TREE" \
            --label "io.gold-trade.release.input-signature=$image_signature" \
            -f "$iran_context_dir/Dockerfile.iran" -t trading_bot_base_iran "$iran_context_dir"
        docker save trading_bot_base_iran postgres:15-alpine redis:7-alpine -o "$LOCAL_IMAGE_BUNDLE"
    else
        docker pull --platform "$IRAN_IMAGE_PLATFORM" postgres:15-alpine >/dev/null
        docker tag postgres:15-alpine "postgres:15-alpine-iran-$IRAN_HOST_ARCH"
        docker pull --platform "$IRAN_IMAGE_PLATFORM" redis:7-alpine >/dev/null
        docker tag redis:7-alpine "redis:7-alpine-iran-$IRAN_HOST_ARCH"
        docker buildx build --platform "$IRAN_IMAGE_PLATFORM" \
            --label "org.opencontainers.image.revision=$RELEASE_SHA" \
            --label "io.gold-trade.release.tree=$PRODUCTION_RELEASE_TREE" \
            --label "io.gold-trade.release.input-signature=$image_signature" \
            -f "$iran_context_dir/Dockerfile.iran" -t trading_bot_base_iran \
            --output "type=docker,dest=$RELEASE_TMP_DIR/trading_bot_base_iran.tar" "$iran_context_dir"
        docker load -i "$RELEASE_TMP_DIR/trading_bot_base_iran.tar" >/dev/null
        docker save trading_bot_base_iran "postgres:15-alpine-iran-$IRAN_HOST_ARCH" "redis:7-alpine-iran-$IRAN_HOST_ARCH" -o "$LOCAL_IMAGE_BUNDLE"
    fi
    iran_release_image_matches "$image_signature" \
        || die "Iran release image OCI identity does not match the exact release/build signature."
    printf '%s\n' "$image_signature" > "$LOCAL_IMAGE_SIGNATURE_FILE"
    log "Local release build complete"
}

expected_wheel_cache_signature() {
    local target_arch="$1"
    local bootstrap_requirements="$LOCAL_PROJECT_DIR/deploy/production/pip-bootstrap-requirements.txt"
    {
        md5sum "$LOCAL_PROJECT_DIR/requirements.txt"
        md5sum "$bootstrap_requirements"
    } | md5sum | cut -d' ' -f1 | awk -v arch="$target_arch" '{print $1 "-" arch}'
}

verify_prepared_wheel_cache() {
    local target_arch="$1" output_dir="$2" expected actual
    expected="$(expected_wheel_cache_signature "$target_arch")"
    [[ -d "$output_dir" && -f "$output_dir/.requirements_hash" ]] \
        || die "Prepared wheel cache is missing for architecture $target_arch."
    actual="$(cat "$output_dir/.requirements_hash")"
    [[ "$actual" == "$expected" ]] \
        || die "Prepared wheel cache does not match release requirements for architecture $target_arch."
    find "$output_dir" -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' \) -print -quit | grep -q . \
        || die "Prepared wheel cache is empty for architecture $target_arch."
}

verify_prepared_release_artifacts() {
    [[ -f "$LOCAL_FRONTEND_SIGNATURE_FILE" \
        && "$(cat "$LOCAL_FRONTEND_SIGNATURE_FILE")" == "$(frontend_build_signature)" ]] \
        || die "Prepared frontend artifact does not match the exact release source."
    verify_frontend_release_contracts "$LOCAL_DIST_DIR"
    verify_prepared_wheel_cache "$LOCAL_HOST_ARCH" "$LOCAL_PROJECT_DIR/pip_packages"
    verify_prepared_wheel_cache "$IRAN_HOST_ARCH" "$RELEASE_TMP_DIR/pip_packages-$IRAN_HOST_ARCH"
    load_foreign_image_build_receipt
    load_iran_image_build_receipt
    verify_foreign_image_build_receipt
    verify_iran_image_build_receipt
    [[ -f "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST" \
        && "$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$(file_sha256 "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST")" == "$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256" ]] \
        || die "Committed Iran source payload evidence is missing or drifted."
    log "Verified prebuilt frontend, wheel caches, and exact foreign/Iran image receipts."
}

prepare_release_evidence_artifacts() {
    build_release
    write_iran_image_build_receipt
    verify_iran_image_build_receipt
    prebuild_foreign_release_image
    prepare_committed_iran_source_payload
    verify_prepared_release_artifacts
    log "Prepared release evidence artifacts without touching services or databases."
}

create_official_deploy_sh_authority() {
    local target="$1" authority_dir
    [[ "$target" == "foreign" ]] || die "Unsupported official deploy.sh authority target."
    [[ -f "$PRODUCTION_RELEASE_LOCK_PATH" && ! -L "$PRODUCTION_RELEASE_LOCK_PATH" ]] \
        || die "Official deploy.sh authority requires the active production operation lock."
    authority_dir="$(dirname "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH")"
    [[ ! -L "/var/lib/trading-bot" && ! -L "$authority_dir" ]] \
        || die "Official deploy.sh authority directory ancestry must not contain symlinks."
    install -d -m 0700 -- "$authority_dir"
    [[ -d "$authority_dir" && ! -L "$authority_dir" \
        && "$(stat -c '%u' "$authority_dir")" == "$(id -u)" \
        && "$(stat -c '%a' "$authority_dir")" == "700" ]] \
        || die "Official deploy.sh authority directory must be owner-controlled mode 0700."
    [[ ! -e "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH" \
        && ! -L "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH" ]] \
        || die "A stale official deploy.sh authority exists; manual recovery review is required."
    python3 - "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH" "$PRODUCTION_RELEASE_LOCK_PATH" \
        "$target" "$BASHPID" "$RELEASE_SHA" "$PRODUCTION_RELEASE_TREE" <<'PY'
import json
import os
from pathlib import Path
import sys

destination = Path(sys.argv[1])
lock = Path(sys.argv[2])
lock_stat = lock.stat()
payload = {
    "schema_version": 1,
    "environment": "production",
    "target": sys.argv[3],
    "parent_pid": int(sys.argv[4]),
    "release_sha": sys.argv[5],
    "release_tree": sys.argv[6],
    "release_lock_device": lock_stat.st_dev,
    "release_lock_inode": lock_stat.st_ino,
    "secrets_disclosed": False,
}
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(destination, flags, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
directory = os.open(destination.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

invoke_official_deploy_sh_foreign() {
    create_official_deploy_sh_authority foreign
    local status=0
    PRODUCTION_OFFICIAL_DEPLOY_AUTHORITY_PATH="$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH" \
    PRODUCTION_RELEASE_LOCK_PATH="$PRODUCTION_RELEASE_LOCK_PATH" \
    PRODUCTION_RELEASE_SHA="$RELEASE_SHA" \
    PRODUCTION_RELEASE_TREE="$PRODUCTION_RELEASE_TREE" \
    PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID="$PRODUCTION_FOREIGN_IMAGE_ID" \
    PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE="$PRODUCTION_FOREIGN_IMAGE_SIGNATURE" \
    DEPLOY_MANIFEST="$MANIFEST_PATH" \
    IRAN_HOST="$IRAN_HOST" \
    IRAN_PROJECT_DIR="$IRAN_PROJECT_DIR" \
    "$@" || status=$?
    if [[ -e "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH" \
        || -L "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH" ]]; then
        rm -f -- "$PRODUCTION_DEPLOY_SH_AUTHORITY_PATH"
    fi
    return "$status"
}

write_foreign_image_build_receipt() {
    local receipt_dir image_id image_signature
    receipt_dir="$RELEASE_ARTIFACT_DIR"
    install -d -m 0700 -- "$receipt_dir"
    image_id="$(docker image inspect --format '{{.Id}}' trading_bot_base)"
    image_signature="$(cat "$LOCAL_PROJECT_DIR/tmp/deploy-state/foreign-image.signature")"
    [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ \
        && "$image_signature" =~ ^[0-9a-f]{64}$ ]] \
        || die "Foreign image identity/signature is invalid after prebuild."
    PRODUCTION_FOREIGN_IMAGE_RECEIPT="$receipt_dir/foreign-image-prebuild-receipt.json"
    rm -f -- "$PRODUCTION_FOREIGN_IMAGE_RECEIPT"
    python3 - "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" "$RELEASE_SHA" \
        "$PRODUCTION_RELEASE_TREE" "$image_id" "$image_signature" <<'PY'
import json
import os
from pathlib import Path
import sys

destination = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "environment": "production",
    "release_sha": sys.argv[2],
    "release_tree": sys.argv[3],
    "image_id": sys.argv[4],
    "input_signature": sys.argv[5],
    "secrets_disclosed": False,
}
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(destination, flags, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
directory = os.open(destination.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
    PRODUCTION_FOREIGN_IMAGE_ID="$image_id"
    PRODUCTION_FOREIGN_IMAGE_SIGNATURE="$image_signature"
    PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256="$(file_sha256 "$PRODUCTION_FOREIGN_IMAGE_RECEIPT")"
}

load_foreign_image_build_receipt() {
    PRODUCTION_FOREIGN_IMAGE_RECEIPT="${PRODUCTION_FOREIGN_IMAGE_RECEIPT:-$RELEASE_ARTIFACT_DIR/foreign-image-prebuild-receipt.json}"
    [[ -f "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" && ! -L "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" ]] \
        || die "Exact foreign image receipt is missing. Run the production prebuild first."
    local loaded
    loaded="$(python3 - "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" <<'PY'
import json
import re
from pathlib import Path
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(payload.get("image_id") or "")):
    raise SystemExit(2)
if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("input_signature") or "")):
    raise SystemExit(2)
print(payload["image_id"], payload["input_signature"])
PY
)" || die "Exact foreign image receipt is malformed."
    read -r PRODUCTION_FOREIGN_IMAGE_ID PRODUCTION_FOREIGN_IMAGE_SIGNATURE <<<"$loaded"
    PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256="$(file_sha256 "$PRODUCTION_FOREIGN_IMAGE_RECEIPT")"
}

verify_foreign_image_build_receipt() {
    if [[ -z "$PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256" ]]; then
        load_foreign_image_build_receipt
    fi
    [[ -f "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" \
        && ! -L "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" \
        && "$(file_sha256 "$PRODUCTION_FOREIGN_IMAGE_RECEIPT")" == "$PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256" \
        && "$(docker image inspect --format '{{.Id}}' trading_bot_base)" == "$PRODUCTION_FOREIGN_IMAGE_ID" \
        && "$(cat "$LOCAL_PROJECT_DIR/tmp/deploy-state/foreign-image.signature")" == "$PRODUCTION_FOREIGN_IMAGE_SIGNATURE" ]] \
        || die "Exact foreign prebuild receipt/image binding drifted before migration."
    python3 - "$PRODUCTION_FOREIGN_IMAGE_RECEIPT" "$RELEASE_SHA" \
        "$PRODUCTION_RELEASE_TREE" "$PRODUCTION_FOREIGN_IMAGE_ID" \
        "$PRODUCTION_FOREIGN_IMAGE_SIGNATURE" <<'PY'
import json
from pathlib import Path
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "schema_version": 1,
    "environment": "production",
    "release_sha": sys.argv[2],
    "release_tree": sys.argv[3],
    "image_id": sys.argv[4],
    "input_signature": sys.argv[5],
    "secrets_disclosed": False,
}
if payload != expected:
    raise SystemExit(2)
PY
}

prebuild_foreign_release_image() {
    log "Prebuilding the exact foreign image before production writer quiescence"
    (
        cd "$LOCAL_PROJECT_DIR"
        invoke_official_deploy_sh_foreign \
            env PRODUCTION_PREBUILD_ONLY=1 \
            PRODUCTION_DEFER_FOREIGN_WRITER_START=1 \
            PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE=0 \
            bash ./deploy.sh foreign
    )
    write_foreign_image_build_receipt
    verify_foreign_image_build_receipt
    log "Foreign image prebuild gate passed without touching services or databases."
}

write_iran_image_build_receipt() {
    local receipt_dir image_id image_signature bundle_sha
    receipt_dir="$RELEASE_ARTIFACT_DIR"
    install -d -m 0700 -- "$receipt_dir"
    image_id="$(docker image inspect --format '{{.Id}}' trading_bot_base_iran)"
    image_signature="$(cat "$LOCAL_IMAGE_SIGNATURE_FILE")"
    bundle_sha="$(file_sha256 "$LOCAL_IMAGE_BUNDLE")"
    [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ \
        && "$image_signature" =~ ^[0-9a-f]{64}$ \
        && "$bundle_sha" =~ ^[0-9a-f]{64}$ ]] \
        || die "Iran image identity/signature/bundle digest is invalid after build."
    PRODUCTION_IRAN_IMAGE_RECEIPT="$receipt_dir/iran-image-prebuild-receipt.json"
    python3 - "$PRODUCTION_IRAN_IMAGE_RECEIPT" "$RELEASE_SHA" "$PRODUCTION_RELEASE_TREE" \
        "$image_id" "$image_signature" "$bundle_sha" "$IRAN_HOST" \
        "$IRAN_PROJECT_DIR" <<'PY'
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

destination = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "environment": "production",
    "role": "iran",
    "release_sha": sys.argv[2],
    "release_tree": sys.argv[3],
    "image_id": sys.argv[4],
    "input_signature": sys.argv[5],
    "bundle_sha256": sys.argv[6],
    "target": {
        "host": sys.argv[7],
        "project_dir": sys.argv[8],
        "compose_project": "current",
        "image": "trading_bot_base_iran:latest",
    },
    "secrets_disclosed": False,
}
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(candidate, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
    chmod 0600 "$PRODUCTION_IRAN_IMAGE_RECEIPT"
    PRODUCTION_IRAN_IMAGE_ID="$image_id"
    PRODUCTION_IRAN_IMAGE_SIGNATURE="$image_signature"
    PRODUCTION_IRAN_IMAGE_BUNDLE_SHA256="$bundle_sha"
    PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256="$(file_sha256 "$PRODUCTION_IRAN_IMAGE_RECEIPT")"
}

load_iran_image_build_receipt() {
    PRODUCTION_IRAN_IMAGE_RECEIPT="${PRODUCTION_IRAN_IMAGE_RECEIPT:-$RELEASE_ARTIFACT_DIR/iran-image-prebuild-receipt.json}"
    [[ -f "$PRODUCTION_IRAN_IMAGE_RECEIPT" && ! -L "$PRODUCTION_IRAN_IMAGE_RECEIPT" ]] \
        || die "Independent Iran image receipt is missing. Run build-release first."
    local loaded
    loaded="$(python3 - "$PRODUCTION_IRAN_IMAGE_RECEIPT" <<'PY'
import json
import re
from pathlib import Path
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for field, pattern in (
    ("image_id", r"sha256:[0-9a-f]{64}"),
    ("input_signature", r"[0-9a-f]{64}"),
    ("bundle_sha256", r"[0-9a-f]{64}"),
):
    if not re.fullmatch(pattern, str(payload.get(field) or "")):
        raise SystemExit(2)
print(payload["image_id"], payload["input_signature"], payload["bundle_sha256"])
PY
)" || die "Independent Iran image receipt is malformed."
    read -r PRODUCTION_IRAN_IMAGE_ID PRODUCTION_IRAN_IMAGE_SIGNATURE \
        PRODUCTION_IRAN_IMAGE_BUNDLE_SHA256 <<<"$loaded"
    PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256="$(file_sha256 "$PRODUCTION_IRAN_IMAGE_RECEIPT")"
}

verify_iran_image_build_receipt() {
    if [[ -z "$PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256" ]]; then
        load_iran_image_build_receipt
    fi
    local current_input_signature
    current_input_signature="$(build_image_bundle_signature "$RELEASE_TMP_DIR/iran-build-context")"
    [[ -f "$PRODUCTION_IRAN_IMAGE_RECEIPT" \
        && ! -L "$PRODUCTION_IRAN_IMAGE_RECEIPT" \
        && "$(file_sha256 "$PRODUCTION_IRAN_IMAGE_RECEIPT")" == "$PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256" \
        && "$(file_sha256 "$LOCAL_IMAGE_BUNDLE")" == "$PRODUCTION_IRAN_IMAGE_BUNDLE_SHA256" \
        && "$(cat "$LOCAL_IMAGE_SIGNATURE_FILE")" == "$PRODUCTION_IRAN_IMAGE_SIGNATURE" \
        && "$current_input_signature" == "$PRODUCTION_IRAN_IMAGE_SIGNATURE" ]] \
        || die "Exact Iran prebuild receipt/bundle binding drifted before migration."
    local local_identity
    local_identity="$(docker image inspect --format '{{.Id}}|{{index .Config.Labels "org.opencontainers.image.revision"}}|{{index .Config.Labels "io.gold-trade.release.tree"}}|{{index .Config.Labels "io.gold-trade.release.input-signature"}}' trading_bot_base_iran)"
    [[ "$local_identity" == "$PRODUCTION_IRAN_IMAGE_ID|$RELEASE_SHA|$PRODUCTION_RELEASE_TREE|$PRODUCTION_IRAN_IMAGE_SIGNATURE" ]] \
        || die "Exact Iran local image ID/OCI identity drifted before migration."
    python3 - "$PRODUCTION_IRAN_IMAGE_RECEIPT" "$RELEASE_SHA" \
        "$PRODUCTION_RELEASE_TREE" "$PRODUCTION_IRAN_IMAGE_ID" \
        "$PRODUCTION_IRAN_IMAGE_SIGNATURE" "$PRODUCTION_IRAN_IMAGE_BUNDLE_SHA256" \
        "$IRAN_HOST" "$IRAN_PROJECT_DIR" <<'PY'
import json
from pathlib import Path
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "schema_version": 1,
    "environment": "production",
    "role": "iran",
    "release_sha": sys.argv[2],
    "release_tree": sys.argv[3],
    "image_id": sys.argv[4],
    "input_signature": sys.argv[5],
    "bundle_sha256": sys.argv[6],
    "target": {
        "host": sys.argv[7],
        "project_dir": sys.argv[8],
        "compose_project": "current",
        "image": "trading_bot_base_iran:latest",
    },
    "secrets_disclosed": False,
}
if payload != expected:
    raise SystemExit(2)
PY
}

verify_release_evidence_gate() {
    verify_runtime_env_pair_lock
    verify_foreign_image_build_receipt
    verify_iran_image_build_receipt
    [[ "$PRODUCTION_BACKUP_RECEIPT_PATH" == /* \
        && "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH" == /* \
        && "$PRODUCTION_BACKUP_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]] \
        || die "Exact production backup and migration-rehearsal receipt paths/digests are required."
    local loaded
    loaded="$(PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 - \
        "$MANIFEST_PATH" "$PRODUCTION_BACKUP_RECEIPT_PATH" \
        "$PRODUCTION_BACKUP_RECEIPT_SHA256" \
        "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH" \
        "$PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256" \
        "$RELEASE_SHA" "$PRODUCTION_RELEASE_TREE" "$LOCAL_PROJECT_DIR" \
        "$PRODUCTION_FOREIGN_IMAGE_ID" "$PRODUCTION_FOREIGN_IMAGE_SIGNATURE" \
        "$PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256" \
        "$PRODUCTION_TWO_HOST_RELEASE_RESUMING" \
        "$PRODUCTION_RELEASE_EVIDENCE_MAXIMUM_AGE_SECONDS" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from scripts.rehearse_production_migration import (
    ALREADY_AT_HEAD_MODE,
    DEFAULT_RECEIPT_ROOT,
    EXPECTED_ROLES,
    _parse_utc,
    migration_contract,
    production_backup_manifest_values,
    source_alembic_head,
    verify_backup_receipt,
)

(
    manifest_raw, backup_raw, backup_digest, rehearsal_raw,
    rehearsal_digest, release_sha, release_tree, project_raw,
    foreign_image_id, foreign_signature, foreign_receipt_digest,
    resume_raw, maximum_age_raw,
) = sys.argv[1:]
manifest = Path(manifest_raw)
backup_path = Path(backup_raw)
rehearsal_path = Path(rehearsal_raw)
project = Path(project_raw)
resume = resume_raw == "1"
maximum_age = int(maximum_age_raw)
if maximum_age != 3600:
    raise SystemExit("release evidence freshness policy drifted")

def stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

if (
    not rehearsal_path.is_absolute()
    or rehearsal_path.parent != DEFAULT_RECEIPT_ROOT
    or rehearsal_path.is_symlink()
    or not rehearsal_path.is_file()
    or rehearsal_path.resolve(strict=True) != rehearsal_path
    or DEFAULT_RECEIPT_ROOT.is_symlink()
    or not DEFAULT_RECEIPT_ROOT.is_dir()
    or DEFAULT_RECEIPT_ROOT.resolve(strict=True) != DEFAULT_RECEIPT_ROOT
):
    raise SystemExit("migration rehearsal receipt path is not approved")
root_meta = DEFAULT_RECEIPT_ROOT.stat()
receipt_meta = rehearsal_path.stat()
if (
    root_meta.st_uid not in {0, os.geteuid()}
    or stat.S_IMODE(root_meta.st_mode) != 0o700
    or receipt_meta.st_uid not in {0, os.geteuid()}
    or stat.S_IMODE(receipt_meta.st_mode) != 0o600
    or receipt_meta.st_nlink != 1
    or stream_sha256(rehearsal_path) != rehearsal_digest
):
    raise SystemExit("migration rehearsal receipt security/digest check failed")
payload = json.loads(rehearsal_path.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit("migration rehearsal receipt is malformed")

# The backup is taken from the currently deployed (old) release, while the
# source and migration-runner receipts bind the new target release.  Derive the
# pre-release SHA from both backup roles and never conflate it with new HEAD.
backup_payload = json.loads(backup_path.read_text(encoding="utf-8"))
backup_results = backup_payload.get("results") if isinstance(backup_payload, dict) else None
backup_release_shas = {
    str(row.get("release_sha") or "")
    for row in backup_results or []
    if isinstance(row, dict) and row.get("role") in EXPECTED_ROLES
}
if len(backup_release_shas) != 1:
    raise SystemExit("backup roles do not bind one deployed pre-release SHA")
pre_release_sha = next(iter(backup_release_shas))
if not re.fullmatch(r"[0-9a-f]{40}", pre_release_sha):
    raise SystemExit("backup pre-release SHA is invalid")

# On a forward recovery, validate the same artifact bytes and all bindings but
# evaluate the backup timestamps at their original evidence time.  Requiring a
# newly-fresh backup after a migration may have begun would make safe recovery
# impossible and invite an unbound replacement receipt.
backup_now = None
if resume:
    backup_now = _parse_utc(backup_payload.get("created_at"))
source_head = source_alembic_head(project)
backup = verify_backup_receipt(
    receipt_path=backup_path,
    receipt_sha256=backup_digest,
    expected_release_sha=pre_release_sha,
    manifest_values=production_backup_manifest_values(manifest),
    max_age_seconds=maximum_age,
    now=backup_now,
    expected_source_head=source_head,
)

contract = migration_contract(backup.pre_migration_head, source_head)
expected_target_bindings = {
    artifact.role: artifact.target_binding_sha256 for artifact in backup.artifacts
}
expected_database_identities = {
    artifact.role: artifact.database_identity_sha256 for artifact in backup.artifacts
}
expected_artifacts = {artifact.role: artifact for artifact in backup.artifacts}
required_top = {
    "schema_version", "contract", "status", "mode", "source_commit",
    "source_tree", "source_alembic_head", "production_release_sha",
    "migration_mode", "pre_migration_head", "migration_runner_image_id",
    "migration_runner_prebuild_receipt_sha256", "migration_runner_release_tree",
    "migration_runner_input_signature", "migration_runner_oci_revision",
    "migration_runner_oci_release_tree", "migration_runner_oci_input_signature",
    "backup_receipt_sha256", "backup_artifact_set_sha256", "backup_created_at",
    "roles", "target_bindings_sha256", "expected_public_table_delta",
    "docker_network", "production_mutation", "run_id", "started_at", "finished_at",
    "duration_seconds", "committed_source_archive_sha256", "results",
    "cleanup_status", "cleanup_failure_codes", "error_code",
}
if set(payload) != required_top:
    raise SystemExit("migration rehearsal receipt schema is not exact")
if (
    payload.get("schema_version") != 1
    or payload.get("contract") != "production-migration-rehearsal-v1"
    or payload.get("status") != "passed"
    or payload.get("mode") != "execute"
    or payload.get("source_commit") != release_sha
    or payload.get("source_tree") != release_tree
    or payload.get("source_alembic_head") != source_head
    or payload.get("production_release_sha") != pre_release_sha
    or payload.get("migration_mode") != contract.mode
    or payload.get("pre_migration_head") != contract.pre_revision
    or payload.get("migration_runner_image_id") != foreign_image_id
    or payload.get("migration_runner_prebuild_receipt_sha256") != foreign_receipt_digest
    or payload.get("migration_runner_release_tree") != release_tree
    or payload.get("migration_runner_input_signature") != foreign_signature
    or payload.get("migration_runner_oci_revision") != release_sha
    or payload.get("migration_runner_oci_release_tree") != release_tree
    or payload.get("migration_runner_oci_input_signature") != foreign_signature
    or payload.get("backup_receipt_sha256") != backup_digest
    or payload.get("backup_artifact_set_sha256") != backup.artifact_set_sha256
    or payload.get("backup_created_at") != backup.created_at
    or payload.get("roles") != list(EXPECTED_ROLES)
    or payload.get("target_bindings_sha256") != expected_target_bindings
    or payload.get("expected_public_table_delta") != contract.expected_public_table_delta
    or payload.get("docker_network") != "random-internal-no-published-ports"
    or payload.get("production_mutation") is not False
    or payload.get("cleanup_status") != "passed"
    or payload.get("cleanup_failure_codes") != []
    or payload.get("error_code") is not None
    or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("committed_source_archive_sha256") or ""))
):
    raise SystemExit("migration rehearsal receipt binding is invalid")
results = payload.get("results")
if not isinstance(results, list) or [row.get("role") for row in results if isinstance(row, dict)] != list(EXPECTED_ROLES):
    raise SystemExit("migration rehearsal role results are incomplete")
for row in results:
    role = row["role"]
    artifact = expected_artifacts[role]
    schema_before = row.get("schema_before_sha256")
    schema_after = row.get("schema_after_sha256")
    schema_noop = row.get("schema_noop_sha256")
    if (
        row.get("status") != "passed"
        or row.get("artifact_sha256") != artifact.sha256
        or row.get("artifact_size_bytes") != artifact.size_bytes
        or row.get("database_identity_sha256") != expected_database_identities[role]
        or row.get("target_binding_sha256") != expected_target_bindings[role]
        or row.get("migration_mode") != contract.mode
        or row.get("pre_revision") != contract.pre_revision
        or row.get("post_revision") != source_head
        or row.get("public_table_delta") != contract.expected_public_table_delta
        or row.get("added_tables") != list(contract.expected_added_tables)
        or (row.get("preexisting_table_count_contract") or {}).get("all_row_counts_preserved") is not True
        or row.get("new_table_seed_contract") != (
            {"telegram_delivery_feeder_states": 1, "all_other_new_tables": 0}
            if contract.require_initial_seed_contract
            else None
        )
        or row.get("invalid_or_unready_indexes") != 0
        or row.get("concurrent_index_state") != "valid-ready"
        or row.get("first_upgrade_noop") is not contract.require_first_upgrade_noop
        or row.get("second_upgrade_noop") is not True
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(value or ""))
            for value in (schema_before, schema_after, schema_noop)
        )
        or schema_after != schema_noop
        or (contract.mode == ALREADY_AT_HEAD_MODE and schema_before != schema_after)
    ):
        raise SystemExit("migration rehearsal result binding is invalid")

started = _parse_utc(payload.get("started_at"))
finished = _parse_utc(payload.get("finished_at"))
backup_created = _parse_utc(payload.get("backup_created_at"))
if started > finished or backup_created > started:
    raise SystemExit("migration rehearsal timestamps are inconsistent")
if not resume:
    age = (datetime.now(timezone.utc) - finished).total_seconds()
    if age < -300 or age > maximum_age:
        raise SystemExit("migration rehearsal receipt is not fresh")
print(
    pre_release_sha,
    backup.artifact_set_sha256,
    source_head,
    expected_target_bindings["foreign"],
    expected_target_bindings["iran"],
)
PY
)" || die "Production backup/restore-smoke or migration-rehearsal evidence gate failed."
    read -r PRODUCTION_PRE_RELEASE_SHA PRODUCTION_BACKUP_ARTIFACT_SET_SHA256 PRODUCTION_RELEASE_SCHEMA_HEAD \
        PRODUCTION_FOREIGN_TARGET_BINDING_SHA256 PRODUCTION_IRAN_TARGET_BINDING_SHA256 \
        <<<"$loaded"
    [[ "$PRODUCTION_PRE_RELEASE_SHA" =~ ^[0-9a-f]{40}$ \
        && "$PRODUCTION_BACKUP_ARTIFACT_SET_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_FOREIGN_TARGET_BINDING_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_IRAN_TARGET_BINDING_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_RELEASE_SCHEMA_HEAD" =~ ^[0-9a-z]{12}$ ]] \
        || die "Production release evidence output is malformed."
    if [[ "$PRODUCTION_TWO_HOST_RELEASE_RESUMING" != "1" ]]; then
        local foreign_live_release iran_live_release
        foreign_live_release="$(docker exec trading_bot_app printenv RELEASE_SHA | tr -d '[:space:]')"
        iran_live_release="$(ssh_iran "docker exec trading_bot_app printenv RELEASE_SHA | tr -d '[:space:]'")"
        [[ "$foreign_live_release" == "$PRODUCTION_PRE_RELEASE_SHA" \
            && "$iran_live_release" == "$PRODUCTION_PRE_RELEASE_SHA" ]] \
            || die "Live foreign/Iran writers do not match the backup-bound pre-release SHA."
    fi
    PRODUCTION_RELEASE_EVIDENCE_VERIFIED=1
    log "Verified exact production backup/restore-smoke and migration-rehearsal evidence."
}

verify_runtime_env_pair_lock() {
    [[ "$PRODUCTION_RUNTIME_ENV_PAIR_LOCKED" == "1" ]] \
        || die "Production runtime env pair is not release-locked."
    [[ -f "$RUNTIME_ENV_SOURCE_PATH" && ! -L "$RUNTIME_ENV_SOURCE_PATH" ]] \
        || die "Immutable production runtime env source changed type after release lock."
    [[ "$(file_sha256 "$RUNTIME_ENV_SOURCE_PATH")" == "$PRODUCTION_RUNTIME_ENV_SOURCE_SHA256" ]] \
        || die "Immutable production runtime env source drifted after the release pair was rendered."
    [[ -f "$FOREIGN_RUNTIME_ENV_PATH" && ! -L "$FOREIGN_RUNTIME_ENV_PATH" \
        && "$(file_sha256 "$FOREIGN_RUNTIME_ENV_PATH")" == "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" ]] \
        || die "Foreign rendered runtime env drifted after the release pair was locked."
    [[ -f "$IRAN_RUNTIME_ENV_PATH" && ! -L "$IRAN_RUNTIME_ENV_PATH" \
        && "$(file_sha256 "$IRAN_RUNTIME_ENV_PATH")" == "$PRODUCTION_RUNTIME_ENV_IRAN_SHA256" ]] \
        || die "Iran rendered runtime env drifted after the release pair was locked."
    if [[ "$PRODUCTION_RUNTIME_ENV_FOREIGN_INSTALLED" == "1" ]]; then
        [[ -f "$LOCAL_PROJECT_DIR/.env" && ! -L "$LOCAL_PROJECT_DIR/.env" \
            && "$(file_sha256 "$LOCAL_PROJECT_DIR/.env")" == "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" ]] \
            || die "Installed foreign runtime env drifted from the release-locked projection."
    fi
}

verify_installed_runtime_env_pair() {
    verify_runtime_env_pair_lock
    local remote_digest
    [[ -f "$LOCAL_PROJECT_DIR/.env" && ! -L "$LOCAL_PROJECT_DIR/.env" \
        && "$(file_sha256 "$LOCAL_PROJECT_DIR/.env")" == "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" ]] \
        || die "Installed foreign runtime env does not match the release-locked projection."
    validate_remote_shell_path "$IRAN_PROJECT_DIR" "IRAN_PROJECT_DIR"
    remote_digest="$(ssh_iran "test -f '$IRAN_PROJECT_DIR/.env' && test ! -L '$IRAN_PROJECT_DIR/.env' && sha256sum '$IRAN_PROJECT_DIR/.env' | awk '{print \$1}'")"
    [[ "$remote_digest" == "$PRODUCTION_RUNTIME_ENV_IRAN_SHA256" ]] \
        || die "Installed Iran runtime env does not match the release-locked projection."
}

ensure_runtime_env_file() {
    validate_runtime_env_source_policy
    validate_secure_runtime_env_source_file \
        || die "Immutable production runtime env source must be canonical, owner-controlled, non-symlink, and mode 0600."
    [[ -r "$RUNTIME_ENV_SOURCE_PATH" ]] || die "Immutable production runtime env source is not readable: $RUNTIME_ENV_SOURCE_PATH"
    if [[ "$PRODUCTION_RUNTIME_ENV_PAIR_LOCKED" == "1" ]]; then
        verify_runtime_env_pair_lock
        log "Reused the release-locked foreign and Iran runtime env pair."
        return 0
    fi

    local source_sha256_before source_sha256_after
    source_sha256_before="$(file_sha256 "$RUNTIME_ENV_SOURCE_PATH")"
    [[ "$source_sha256_before" =~ ^[0-9a-f]{64}$ ]] \
        || die "Immutable production runtime env source digest is invalid."

    install -d -m 0700 -- "$(dirname "$FOREIGN_RUNTIME_ENV_PATH")" "$(dirname "$IRAN_RUNTIME_ENV_PATH")"
    validate_secure_env_directory "$(dirname "$FOREIGN_RUNTIME_ENV_PATH")" \
        || die "Foreign rendered runtime env directory is not secure."
    validate_secure_env_directory "$(dirname "$IRAN_RUNTIME_ENV_PATH")" \
        || die "Iran rendered runtime env directory is not secure."
    for existing_output in "$FOREIGN_RUNTIME_ENV_PATH" "$IRAN_RUNTIME_ENV_PATH"; do
        if [[ -e "$existing_output" ]]; then
            [[ -f "$existing_output" && ! -L "$existing_output" ]] \
                || die "Rendered runtime env output must be a regular non-symlink file."
        fi
    done
    backup_runtime_env_file "$FOREIGN_RUNTIME_ENV_PATH" "foreign rendered"
    backup_runtime_env_file "$IRAN_RUNTIME_ENV_PATH" "Iran rendered"
    export_runtime_renderer_overrides
    python3 "$RUNTIME_ENV_RENDERER" \
        --source-env-file "$RUNTIME_ENV_SOURCE_PATH" \
        --local-output "$FOREIGN_RUNTIME_ENV_PATH" \
        --iran-output "$IRAN_RUNTIME_ENV_PATH" \
        --foreign-frontend-url "$FOREIGN_FRONTEND_URL" \
        --iran-frontend-url "$IRAN_FRONTEND_URL" \
        --foreign-server-url "$FOREIGN_SERVER_URL" \
        --foreign-server-domain "$FOREIGN_SERVER_DOMAIN" \
        --iran-server-url "$IRAN_SERVER_URL" \
        --iran-server-domain "$IRAN_SERVER_DOMAIN" \
        --foreign-api-workers "${FOREIGN_API_WORKERS:-2}" \
        --iran-api-workers "${IRAN_API_WORKERS:-4}"

    source_sha256_after="$(file_sha256 "$RUNTIME_ENV_SOURCE_PATH")"
    [[ "$source_sha256_after" == "$source_sha256_before" ]] \
        || die "Immutable production runtime env source drifted while rendering the release pair."
    validate_runtime_identity_files
    [[ "$(file_sha256 "$RUNTIME_ENV_SOURCE_PATH")" == "$source_sha256_before" ]] \
        || die "Immutable production runtime env source drifted before the release pair lock was committed."
    PRODUCTION_RUNTIME_ENV_SOURCE_SHA256="$source_sha256_before"
    PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256="$(file_sha256 "$FOREIGN_RUNTIME_ENV_PATH")"
    PRODUCTION_RUNTIME_ENV_IRAN_SHA256="$(file_sha256 "$IRAN_RUNTIME_ENV_PATH")"
    [[ "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" =~ ^[0-9a-f]{64}$ \
        && "$PRODUCTION_RUNTIME_ENV_IRAN_SHA256" =~ ^[0-9a-f]{64}$ ]] \
        || die "Rendered production runtime env pair digest is invalid."
    PRODUCTION_RUNTIME_ENV_PAIR_LOCKED=1
    log "Rendered foreign and Iran runtime env files from the immutable production source."
    summarize_web_push_env_file "$FOREIGN_RUNTIME_ENV_PATH" "Foreign"
    summarize_web_push_env_file "$IRAN_RUNTIME_ENV_PATH" "Iran"
}

install_foreign_runtime_env() {
    local project_env_path="$LOCAL_PROJECT_DIR/.env"
    verify_runtime_env_pair_lock
    atomic_install_local_runtime_env "$FOREIGN_RUNTIME_ENV_PATH" "$project_env_path" "foreign"
    [[ "$(file_sha256 "$project_env_path")" == "$PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256" ]] \
        || die "Installed foreign runtime env does not match the release-locked projection."
    PRODUCTION_RUNTIME_ENV_FOREIGN_INSTALLED=1
    log "Installed rendered foreign runtime env atomically."
}

validate_writer_quiesce_state_file() {
    [[ "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" == "/var/lib/trading-bot/production-release/writer-quiesce-state.json" ]] \
        || die "Writer quiesce state must use the canonical production path."
    local parent
    parent="$(dirname "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")"
    [[ -d "$parent" && ! -L "$parent" \
        && "$(stat -c '%u' "$parent")" == "$(id -u)" \
        && "$(stat -c '%a' "$parent")" == "700" ]] \
        || die "Writer quiesce state parent must be an owner-controlled 0700 directory."
    if [[ -e "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" \
        || -L "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" ]]; then
        [[ -f "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" \
            && ! -L "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" \
            && "$(stat -c '%u' "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")" == "$(id -u)" \
            && "$(stat -c '%a' "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")" == "600" \
            && "$(stat -c '%h' "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")" == "1" ]] \
            || die "Writer quiesce state must be an owner-only regular file."
    fi
}

local_writer_inventory() {
    local service id policy_name retry_count
    for service in app bot sync_worker; do
        id="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        [[ -n "$id" && "$id" != *$'\n'* ]] \
            || die "Exactly one foreign writer container is required before quiescence: $service"
        policy_name="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$id")"
        retry_count="$(docker inspect --format '{{.HostConfig.RestartPolicy.MaximumRetryCount}}' "$id")"
        if [[ "$policy_name" == "on-failure" && "$retry_count" != "0" ]]; then
            policy_name="$policy_name:$retry_count"
        fi
        printf 'foreign\t%s\t%s\t%s\n' "$service" "$id" "$policy_name"
    done
}

iran_writer_inventory() {
    ssh_iran "set -euo pipefail
for service in app sync_worker; do
  id=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -n \"\$id\" ] && [ \"\$(printf '%s\\n' \"\$id\" | wc -l)\" -eq 1 ] || { echo \"Exactly one Iran writer container is required before quiescence: \$service\" >&2; exit 32; }
  policy=\"\$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' \"\$id\")\"
  retry=\"\$(docker inspect --format '{{.HostConfig.RestartPolicy.MaximumRetryCount}}' \"\$id\")\"
  if [ \"\$policy\" = on-failure ] && [ \"\$retry\" != 0 ]; then policy=\"\$policy:\$retry\"; fi
  printf 'iran\\t%s\\t%s\\t%s\\n' \"\$service\" \"\$id\" \"\$policy\"
done"
}

capture_writer_quiesce_state() {
    [[ ! -L "/var/lib/trading-bot" \
        && ! -L "$(dirname "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")" ]] \
        || die "Writer quiesce state directory ancestry must not contain symlinks."
    install -d -m 0700 -- "$(dirname "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")"
    validate_writer_quiesce_state_file
    if [[ -f "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" ]]; then
        python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" "$RELEASE_SHA" \
            "$PRODUCTION_RUNTIME_ENV_SOURCE_SHA256" <<'PY'
import json, re, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("release_sha") != sys.argv[2] or payload.get("source_sha256") != sys.argv[3]:
    raise SystemExit(2)
statuses = {
    "quiesce_prepared", "writers_quiesced",
    "foreign_replacement_creating", "foreign_replacement_prepared",
    "iran_replacement_creating", "replacements_prepared",
    "writers_running_restart_disabled",
}
if payload.get("schema_version") != 2 or payload.get("status") not in statuses or payload.get("secrets_disclosed") is not False:
    raise SystemExit(2)
expected = {("foreign", "app"), ("foreign", "bot"), ("foreign", "sync_worker"), ("iran", "app"), ("iran", "sync_worker")}
rows = payload.get("writers")
if not isinstance(rows, list) or {(row.get("role"), row.get("service")) for row in rows} != expected:
    raise SystemExit(2)
for row in rows:
    if set(row) != {"role", "service", "initial_container_id", "current_container_id", "restart_policy"}:
        raise SystemExit(2)
    if not re.fullmatch(r"[0-9a-f]{12,64}", str(row.get("initial_container_id", ""))):
        raise SystemExit(2)
    if not re.fullmatch(r"[0-9a-f]{12,64}", str(row.get("current_container_id", ""))):
        raise SystemExit(2)
    if not re.fullmatch(r"(?:no|always|unless-stopped|on-failure(?::[1-9][0-9]*)?)", str(row.get("restart_policy", ""))):
        raise SystemExit(2)
PY
        return 0
    fi
    local state_dir foreign_file iran_file
    state_dir="$(dirname "$PRODUCTION_WRITER_QUIESCE_STATE_FILE")"
    install -d -m 0700 -- "$state_dir"
    foreign_file="$(mktemp "$RELEASE_TMP_DIR/writers.foreign.XXXXXX")"
    iran_file="$(mktemp "$RELEASE_TMP_DIR/writers.iran.XXXXXX")"
    chmod 0600 "$foreign_file" "$iran_file"
    local_writer_inventory > "$foreign_file"
    iran_writer_inventory > "$iran_file"
    if ! python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" "$RELEASE_SHA" \
        "$PRODUCTION_RUNTIME_ENV_SOURCE_SHA256" "$foreign_file" "$iran_file" <<'PY'
import json, os, re, sys
from pathlib import Path
from uuid import uuid4
destination = Path(sys.argv[1])
rows = []
for source in (Path(sys.argv[4]), Path(sys.argv[5])):
    for line in source.read_text(encoding="utf-8").splitlines():
        role, service, container_id, policy = line.split("\t")
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise SystemExit(2)
        if not re.fullmatch(r"(?:no|always|unless-stopped|on-failure(?::[1-9][0-9]*)?)", policy):
            raise SystemExit(2)
        rows.append({
            "role": role,
            "service": service,
            "initial_container_id": container_id,
            "current_container_id": container_id,
            "restart_policy": policy,
        })
expected = {("foreign", "app"), ("foreign", "bot"), ("foreign", "sync_worker"), ("iran", "app"), ("iran", "sync_worker")}
if {(row["role"], row["service"]) for row in rows} != expected or len(rows) != len(expected):
    raise SystemExit(2)
payload = {
    "schema_version": 2,
    "status": "quiesce_prepared",
    "release_sha": sys.argv[2],
    "source_sha256": sys.argv[3],
    "writers": sorted(rows, key=lambda row: (row["role"], row["service"])),
    "recovery_action": "rerun_exact_same_release_do_not_restart_old_code",
    "secrets_disclosed": False,
}
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(candidate, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
    then
        rm -f -- "$foreign_file" "$iran_file"
        die "Could not persist the writer quiesce journal."
    fi
    rm -f -- "$foreign_file" "$iran_file"
}

mark_writer_quiesce_complete() {
    update_writer_journal_phase writers_quiesced
}

update_writer_journal_phase() {
    local next_phase="$1"
    NEXT_WRITER_PHASE="$next_phase" python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" <<'PY'
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

destination = Path(sys.argv[1])
payload = json.loads(destination.read_text(encoding="utf-8"))
next_phase = os.environ["NEXT_WRITER_PHASE"]
transitions = {
    "quiesce_prepared": {"writers_quiesced"},
    "writers_quiesced": {"writers_quiesced", "foreign_replacement_creating"},
    "foreign_replacement_creating": {"writers_quiesced", "foreign_replacement_creating", "foreign_replacement_prepared"},
    "foreign_replacement_prepared": {"writers_quiesced", "foreign_replacement_prepared", "iran_replacement_creating"},
    "iran_replacement_creating": {"writers_quiesced", "iran_replacement_creating", "replacements_prepared"},
    "replacements_prepared": {"writers_quiesced", "replacements_prepared", "writers_running_restart_disabled"},
    "writers_running_restart_disabled": {"writers_quiesced", "writers_running_restart_disabled"},
}
if next_phase not in transitions.get(payload.get("status"), set()):
    raise SystemExit(2)
payload["status"] = next_phase
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(candidate, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
}

record_writer_replacement_inventory() {
    local role="$1" expected_phase="$2" next_phase="$3" inventory_file="$4"
    [[ "$role" == "foreign" || "$role" == "iran" ]] \
        || die "Invalid writer replacement role."
    ROLE="$role" EXPECTED_WRITER_PHASE="$expected_phase" NEXT_WRITER_PHASE="$next_phase" \
        python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" "$inventory_file" <<'PY'
import json
import os
import re
from pathlib import Path
import sys
from uuid import uuid4

destination = Path(sys.argv[1])
inventory_path = Path(sys.argv[2])
payload = json.loads(destination.read_text(encoding="utf-8"))
role = os.environ["ROLE"]
expected_phase = os.environ["EXPECTED_WRITER_PHASE"]
next_phase = os.environ["NEXT_WRITER_PHASE"]
if payload.get("schema_version") != 2 or payload.get("status") != expected_phase:
    raise SystemExit(2)
expected_services = {"app", "bot", "sync_worker"} if role == "foreign" else {"app", "sync_worker"}
inventory = {}
for line in inventory_path.read_text(encoding="utf-8").splitlines():
    service, container_id = line.split("\t")
    if service in inventory or not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        raise SystemExit(2)
    inventory[service] = container_id
if set(inventory) != expected_services:
    raise SystemExit(2)
for row in payload.get("writers") or []:
    if row.get("role") == role:
        row["current_container_id"] = inventory[row["service"]]
payload["status"] = next_phase
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(candidate, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
}

writer_state_value() {
    local role="$1" service="$2" field="$3"
    python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" "$role" "$service" "$field" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
matches = [row for row in payload["writers"] if row["role"] == sys.argv[2] and row["service"] == sys.argv[3]]
if len(matches) != 1 or sys.argv[4] not in {"initial_container_id", "current_container_id", "restart_policy"}:
    raise SystemExit(2)
print(matches[0][sys.argv[4]])
PY
}

writer_state_status() {
    python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" <<'PY'
import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["status"])
PY
}

reconcile_unjournaled_writer_replacements() {
    local status service expected current image policy inventory
    status="$(writer_state_status)"
    if [[ "$status" == "foreign_replacement_creating" ]]; then
        inventory="$(mktemp "$RELEASE_TMP_DIR/writers.foreign.reconcile.XXXXXX")"
        chmod 0600 "$inventory"
        for service in app bot sync_worker; do
            expected="$(writer_state_value foreign "$service" current_container_id)"
            current="$(docker ps -aq \
                --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
                --filter "label=com.docker.compose.service=$service")"
            [[ -n "$current" && "$current" != *$'\n'* ]] \
                || die "A foreign writer disappeared during replacement recovery: $service"
            if [[ "$current" != "$expected" ]]; then
                image="$(docker inspect --format '{{.Image}}' "$current")"
                policy="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$current")"
                [[ "$image" == "$PRODUCTION_FOREIGN_IMAGE_ID" && "$policy" == "no" ]] \
                    || die "Unjournaled foreign replacement is not the exact restart-disabled release image: $service"
            fi
            printf '%s\t%s\n' "$service" "$current" >>"$inventory"
        done
        record_writer_replacement_inventory foreign \
            foreign_replacement_creating foreign_replacement_creating "$inventory"
        rm -f -- "$inventory"
    elif [[ "$status" == "iran_replacement_creating" ]]; then
        inventory="$(mktemp "$RELEASE_TMP_DIR/writers.iran.reconcile.XXXXXX")"
        chmod 0600 "$inventory"
        local expected_app expected_sync
        expected_app="$(writer_state_value iran app current_container_id)"
        expected_sync="$(writer_state_value iran sync_worker current_container_id)"
        ssh_iran "set -euo pipefail
for pair in app:$expected_app sync_worker:$expected_sync; do
  service=\"\${pair%%:*}\"; expected=\"\${pair#*:}\"
  current=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -n \"\$current\" ] && [ \"\$(printf '%s\\n' \"\$current\" | wc -l)\" -eq 1 ] || exit 42
  if [ \"\$current\" != \"\$expected\" ]; then
    [ \"\$(docker inspect --format '{{.Image}}' \"\$current\")\" = '$PRODUCTION_IRAN_REMOTE_IMAGE_ID' ] || exit 43
    [ \"\$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' \"\$current\")\" = no ] || exit 44
  fi
  printf '%s\\t%s\\n' \"\$service\" \"\$current\"
done" >"$inventory" \
            || die "Could not reconcile an interrupted Iran writer replacement."
        record_writer_replacement_inventory iran \
            iran_replacement_creating iran_replacement_creating "$inventory"
        rm -f -- "$inventory"
    fi
}

disable_and_stop_current_foreign_writers() {
    local service expected current
    for service in app bot sync_worker; do
        expected="$(writer_state_value foreign "$service" current_container_id)"
        current="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        [[ -z "$current" || "$current" == "$expected" ]] \
            || die "Unexpected foreign writer container appeared during release: $service"
        if [[ -n "$current" ]]; then
            docker update --restart=no "$current" >/dev/null
            docker stop -t 30 "$current" >/dev/null || true
            [[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$current")" == "no" ]] \
                || die "Foreign writer restart policy was not disabled: $service"
        fi
    done
}

disable_and_stop_current_iran_writers() {
    local expected_app expected_sync
    expected_app="$(writer_state_value iran app current_container_id)"
    expected_sync="$(writer_state_value iran sync_worker current_container_id)"
    ssh_iran "set -euo pipefail
for pair in app:$expected_app sync_worker:$expected_sync; do
  service=\"\${pair%%:*}\"; expected=\"\${pair#*:}\"
  current=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -z \"\$current\" ] || [ \"\$current\" = \"\$expected\" ] || { echo \"Unexpected Iran writer container: \$service\" >&2; exit 33; }
  if [ -n \"\$current\" ]; then
    docker update --restart=no \"\$current\" >/dev/null
    docker stop -t 30 \"\$current\" >/dev/null || true
    [ \"\$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' \"\$current\")\" = no ] || exit 34
  fi
done"
}

clear_writer_quiesce_state() {
    validate_writer_quiesce_state_file
    if [[ -f "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" ]]; then
        python3 - "$PRODUCTION_WRITER_QUIESCE_STATE_FILE" <<'PY'
import os
from pathlib import Path
import sys
path = Path(sys.argv[1])
path.unlink()
directory = os.open(path.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
    fi
}

quiesce_two_host_writers_for_migration() {
    log "Quiescing production writers on both hosts before the first schema migration"
    capture_writer_quiesce_state
    PRODUCTION_TWO_HOST_WRITER_RESTART_GUARD_ARMED=1
    reconcile_unjournaled_writer_replacements
    disable_and_stop_current_foreign_writers
    disable_and_stop_current_iran_writers
    local service running
    for service in app bot sync_worker; do
        running="$(docker ps -q \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        [[ -z "$running" ]] || die "Foreign writer service remained active before migration: $service"
    done
    ssh_iran "set -euo pipefail
for service in app sync_worker; do
  running=\"\$(docker ps -q --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -z \"\$running\" ] || { echo \"Iran writer remained active before migration: \$service\" >&2; exit 31; }
done"
    mark_writer_quiesce_complete
    PRODUCTION_TWO_HOST_WRITERS_QUIESCED=1
    log "Both production writer planes are restart-disabled and quiesced; DB and Redis services were not targeted."
}

verify_two_host_schema_head() {
    [[ "$PRODUCTION_TWO_HOST_WRITERS_QUIESCED" == "1" ]] \
        || die "Two-host schema verification requires both writer planes to remain quiesced."
    local heads_output expected_head foreign_head iran_head
    heads_output="$(
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD run --rm --no-deps migration python -m alembic heads
    )"
    expected_head="$(printf '%s\n' "$heads_output" | sed -n -E 's/^([0-9A-Za-z_]+)[[:space:]]+\(head\)$/\1/p')"
    [[ -n "$expected_head" && "$expected_head" != *$'\n'* ]] \
        || die "Production release requires exactly one Alembic head."
    foreign_head="$(
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD exec -T db sh -lc \
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"'
    )"
    iran_head="$(ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T db sh -lc 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"SELECT version_num FROM alembic_version\"'")"
    foreign_head="$(printf '%s' "$foreign_head" | tr -d '[:space:]')"
    iran_head="$(printf '%s' "$iran_head" | tr -d '[:space:]')"
    [[ "$foreign_head" == "$expected_head" && "$iran_head" == "$expected_head" ]] \
        || die "Both production databases must reach the single release schema head before any writer restarts."
    PRODUCTION_TWO_HOST_SCHEMAS_VERIFIED=1
    log "Both production databases match the single release schema head."
}

emergency_disable_all_foreign_writers() {
    local service ids
    for service in app bot sync_worker; do
        ids="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        if [[ -n "$ids" ]]; then
            # shellcheck disable=SC2086
            docker update --restart=no $ids >/dev/null 2>&1 || true
            # shellcheck disable=SC2086
            docker stop -t 30 $ids >/dev/null 2>&1 || true
        fi
    done
}

emergency_disable_all_iran_writers() {
    ssh_iran "set -euo pipefail
for service in app sync_worker; do
  ids=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  if [ -n \"\$ids\" ]; then
    docker update --restart=no \$ids >/dev/null 2>&1 || true
    docker stop -t 30 \$ids >/dev/null 2>&1 || true
  fi
done" || true
}

write_writer_restart_disabled_override() {
    local role="$1"
    [[ "$role" == "foreign" || "$role" == "iran" ]] \
        || die "Invalid writer restart-disabled override role."
    local destination="$RELEASE_ARTIFACT_DIR/writer-restart-disabled.$role.override.yml"
    install -d -m 0700 -- "$RELEASE_ARTIFACT_DIR"
    python3 - "$destination" "$role" <<'PY'
import os
from pathlib import Path
import sys
from uuid import uuid4

destination = Path(sys.argv[1])
role = sys.argv[2]
services = ("app", "bot", "sync_worker") if role == "foreign" else ("app", "sync_worker")
content = "services:\n" + "".join(f'  {service}:\n    restart: "no"\n' for service in services)
candidate = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(candidate, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(candidate, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    candidate.unlink(missing_ok=True)
PY
    printf '%s\n' "$destination"
}

install_remote_writer_restart_disabled_override() {
    local local_override="$1" remote_override remote_candidate expected_digest
    remote_override="$REMOTE_RELEASE_STATE_DIR/writer-restart-disabled.override.yml"
    remote_candidate="$remote_override.$RELEASE_SHA.tmp"
    expected_digest="$(file_sha256 "$local_override")"
    ssh_iran "install -d -m 0700 '$REMOTE_RELEASE_STATE_DIR'; rm -f -- '$remote_candidate'"
    scp_iran "$local_override" "$IRAN_SSH_TARGET:$remote_candidate"
    ssh_iran "set -euo pipefail
[ -f '$remote_candidate' ] && [ ! -L '$remote_candidate' ]
[ \"\$(sha256sum '$remote_candidate' | awk '{print \$1}')\" = '$expected_digest' ]
chmod 0600 '$remote_candidate'
mv -f -- '$remote_candidate' '$remote_override'
[ \"\$(sha256sum '$remote_override' | awk '{print \$1}')\" = '$expected_digest' ]"
    printf '%s\n' "$remote_override"
}

prepare_restart_disabled_foreign_writers() {
    local service id image override inventory
    override="$(write_writer_restart_disabled_override foreign)"
    update_writer_journal_phase foreign_replacement_creating
    (
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD -f docker-compose.yml -f "$override" \
            up --no-start --force-recreate --no-deps app bot sync_worker
    )
    inventory="$(mktemp "$RELEASE_TMP_DIR/writers.foreign.replacement.XXXXXX")"
    chmod 0600 "$inventory"
    for service in app bot sync_worker; do
        id="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        [[ -n "$id" && "$id" != *$'\n'* ]] \
            || die "Exactly one prepared foreign writer container is required: $service"
        image="$(docker inspect --format '{{.Image}}' "$id")"
        [[ "$image" == "$PRODUCTION_FOREIGN_IMAGE_ID" \
            && "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$id")" == "no" ]] \
            || die "Prepared foreign writer is not the exact restart-disabled release image: $service"
        printf '%s\t%s\n' "$service" "$id" >>"$inventory"
    done
    record_writer_replacement_inventory foreign \
        foreign_replacement_creating foreign_replacement_prepared "$inventory"
    rm -f -- "$inventory"
}

prepare_restart_disabled_iran_writers() {
    local local_override remote_override inventory
    local_override="$(write_writer_restart_disabled_override iran)"
    remote_override="$(install_remote_writer_restart_disabled_override "$local_override")"
    update_writer_journal_phase iran_replacement_creating
    inventory="$(mktemp "$RELEASE_TMP_DIR/writers.iran.replacement.XXXXXX")"
    chmod 0600 "$inventory"
    ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml -f '$remote_override' up --no-start --force-recreate --no-deps app sync_worker
for service in app sync_worker; do
  id=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -n \"\$id\" ] && [ \"\$(printf '%s\\n' \"\$id\" | wc -l)\" -eq 1 ] || exit 35
  [ \"\$(docker inspect --format '{{.Image}}' \"\$id\")\" = '$PRODUCTION_IRAN_REMOTE_IMAGE_ID' ] || exit 36
  [ \"\$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' \"\$id\")\" = no ] || exit 37
  printf '%s\\t%s\\n' \"\$service\" \"\$id\"
done" >"$inventory" \
        || die "Iran writer replacement preparation failed."
    record_writer_replacement_inventory iran \
        iran_replacement_creating replacements_prepared "$inventory"
    rm -f -- "$inventory"
}

restore_current_foreign_writer_policies() {
    local service policy id expected
    for service in app bot sync_worker; do
        policy="$(writer_state_value foreign "$service" restart_policy)"
        id="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        expected="$(writer_state_value foreign "$service" current_container_id)"
        [[ -n "$id" && "$id" != *$'\n'* && "$id" == "$expected" ]] || return 1
        docker update --restart="$policy" "$id" >/dev/null || return 1
        [[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$id")" == "${policy%%:*}" ]] || return 1
    done
}

restore_current_iran_writer_policies() {
    local app_policy sync_policy app_id sync_id
    app_policy="$(writer_state_value iran app restart_policy)"
    sync_policy="$(writer_state_value iran sync_worker restart_policy)"
    app_id="$(writer_state_value iran app current_container_id)"
    sync_id="$(writer_state_value iran sync_worker current_container_id)"
    ssh_iran "set -euo pipefail
for triple in app:$app_id:$app_policy sync_worker:$sync_id:$sync_policy; do
  service=\"\${triple%%:*}\"; remainder=\"\${triple#*:}\"; expected=\"\${remainder%%:*}\"; policy=\"\${remainder#*:}\"
  id=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -n \"\$id\" ] && [ \"\$id\" = \"\$expected\" ] && [ \"\$(printf '%s\\n' \"\$id\" | wc -l)\" -eq 1 ] || exit 37
  docker update --restart=\"\$policy\" \"\$id\" >/dev/null
  [ \"\$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' \"\$id\")\" = \"\${policy%%:*}\" ] || exit 38
done"
}

start_prepared_foreign_writers() {
    local ids service id status health
    ids=""
    for service in app bot sync_worker; do
        id="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$PRODUCTION_FOREIGN_COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        [[ -n "$id" && "$id" != *$'\n'* \
            && "$id" == "$(writer_state_value foreign "$service" current_container_id)" ]] || return 1
        ids="$ids $id"
    done
    # shellcheck disable=SC2086
    docker start $ids >/dev/null || return 1
    for _attempt in $(seq 1 60); do
        status=ready
        for id in $ids; do
            [[ "$(docker inspect --format '{{.State.Status}}' "$id")" == "running" ]] || status=waiting
            health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")"
            [[ "$health" != "unhealthy" ]] || return 1
            [[ "$health" == "healthy" || "$health" == "none" ]] || status=waiting
        done
        [[ "$status" == "ready" ]] && return 0
        sleep 2
    done
    return 1
}

start_prepared_iran_writers() {
    local app_id sync_id
    app_id="$(writer_state_value iran app current_container_id)"
    sync_id="$(writer_state_value iran sync_worker current_container_id)"
    ssh_iran "set -euo pipefail
ids=''
for pair in app:$app_id sync_worker:$sync_id; do
  service=\"\${pair%%:*}\"; expected=\"\${pair#*:}\"
  id=\"\$(docker ps -aq --filter label=com.docker.compose.project=current --filter label=com.docker.compose.service=\$service)\"
  [ -n \"\$id\" ] && [ \"\$id\" = \"\$expected\" ] && [ \"\$(printf '%s\\n' \"\$id\" | wc -l)\" -eq 1 ] || exit 39
  ids=\"\$ids \$id\"
done
docker start \$ids >/dev/null
for attempt in \$(seq 1 60); do
  status=ready
  for id in \$ids; do
    [ \"\$(docker inspect --format '{{.State.Status}}' \"\$id\")\" = running ] || status=waiting
    health=\"\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \"\$id\")\"
    [ \"\$health\" != unhealthy ] || exit 40
    { [ \"\$health\" = healthy ] || [ \"\$health\" = none ]; } || status=waiting
  done
  [ \"\$status\" = ready ] && exit 0
  sleep 2
done
exit 41"
}

start_two_host_writers_after_schema_convergence() {
    [[ "$PRODUCTION_TWO_HOST_WRITERS_QUIESCED" == "1" \
        && "$PRODUCTION_TWO_HOST_SCHEMAS_VERIFIED" == "1" ]] \
        || die "Production writers cannot start before two-host schema convergence."
    log "Preparing replacement writer containers with their journaled restart policies"
    if ! prepare_restart_disabled_foreign_writers; then
        emergency_disable_all_foreign_writers
        emergency_disable_all_iran_writers
        die "Foreign writer preparation failed after schema convergence; all writer policies remain disabled."
    fi
    if ! prepare_restart_disabled_iran_writers; then
        emergency_disable_all_foreign_writers
        emergency_disable_all_iran_writers
        die "Iran writer preparation failed after schema convergence; all writer policies remain disabled."
    fi
    log "Starting restart-disabled foreign writers after two-host schema convergence"
    if ! start_prepared_foreign_writers; then
        emergency_disable_all_foreign_writers
        emergency_disable_all_iran_writers
        die "Foreign writers failed to start after schema convergence; all writer policies remain disabled."
    fi
    if ! start_prepared_iran_writers; then
        emergency_disable_all_foreign_writers
        emergency_disable_all_iran_writers
        die "Iran writers failed to start after schema convergence; both writer planes were returned to restart-disabled stopped state."
    fi
    update_writer_journal_phase writers_running_restart_disabled
    PRODUCTION_TWO_HOST_WRITERS_QUIESCED=0
    log "Both production writer planes started on the converged schema with restart still disabled until final health passes."
}

finalize_two_host_writer_restart_policies() {
    [[ "$PRODUCTION_TWO_HOST_WRITER_RESTART_GUARD_ARMED" == "1" \
        && "$PRODUCTION_TWO_HOST_SCHEMAS_VERIFIED" == "1" ]] \
        || die "Writer restart policies cannot be finalized before the schema and health gates."
    if ! restore_current_foreign_writer_policies \
        || ! restore_current_iran_writer_policies; then
        emergency_disable_all_foreign_writers
        emergency_disable_all_iran_writers
        die "Writer restart-policy restoration failed; both writer planes were returned to restart-disabled stopped state."
    fi
    clear_writer_quiesce_state
    PRODUCTION_TWO_HOST_WRITER_RESTART_GUARD_ARMED=0
    log "Prior writer restart policies were restored after the final two-host health gate."
}

deploy_foreign() {
    local defer_writer_start="${1:-0}"
    if [[ "$IRAN_SKIP_FOREIGN_DEPLOY" == "1" ]]; then
        log "Skipping foreign deploy because IRAN_SKIP_FOREIGN_DEPLOY=1"
        return 0
    fi
    log "Deploying the foreign server locally"
    verify_frozen_release_source
    verify_foreign_image_build_receipt
    ensure_runtime_env_file
    ensure_local_production_coin_runtime_dir
    install_foreign_runtime_env
    (
        cd "$LOCAL_PROJECT_DIR"
        invoke_official_deploy_sh_foreign \
            env PRODUCTION_DEFER_FOREIGN_WRITER_START="$defer_writer_start" \
            PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE="$defer_writer_start" \
            bash ./deploy.sh foreign
    )
    verify_frozen_release_source
}

prepare_committed_iran_source_payload() {
    verify_frozen_release_source
    local export_dir="$RELEASE_TMP_DIR/iran-source-export"
    case "$export_dir:$LOCAL_IRAN_SOURCE_PAYLOAD_DIR:$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST" in
        "$RELEASE_TMP_DIR"/*:"$RELEASE_TMP_DIR"/*:"$RELEASE_TMP_DIR"/*) ;;
        *) die "Iran source payload paths escaped the private release directory." ;;
    esac
    rm -rf -- "$export_dir" "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR"
    rm -f -- "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST"
    install -d -m 0700 -- "$export_dir" "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR"
    git -C "$LOCAL_PROJECT_DIR" archive --format=tar "$RELEASE_SHA" \
        | tar -xf - -C "$export_dir"
    rsync -a --delete \
        --exclude '.github' \
        --exclude '.githooks' \
        --exclude '.agents' \
        --exclude '.claude' \
        --exclude '.codex' \
        --exclude '.cursor' \
        --exclude '.env' \
        --exclude '.env.*' \
        --exclude '.venv' \
        --exclude '.vscode' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude 'docs' \
        --exclude 'frontend' \
        --exclude 'node_modules' \
        --exclude 'tests' \
        --exclude 'tmp' \
        --exclude 'uploads' \
        --exclude 'map_data' \
        --exclude 'audit_trail' \
        --exclude 'mutants' \
        --exclude 'stage9-test-packages' \
        --exclude 'stage9-test-packages-py311' \
        "$export_dir/" "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR/"
    rm -rf -- "$export_dir"
    [[ -z "$(find "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR" -type l -print -quit)" \
        && -z "$(find "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR" ! -type f ! -type d -print -quit)" ]] \
        || die "Committed Iran source payload contains an unsupported filesystem entry."
    (
        cd "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR"
        find . -type f -print0 | LC_ALL=C sort -z | xargs -r -0 sha256sum
    ) >"$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST"
    chmod 0600 "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST"
    [[ -s "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST" ]] \
        || die "Committed Iran source payload manifest is empty."
    PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256="$(file_sha256 "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST")"
    export PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256
    verify_frozen_release_source
}

sync_project() {
    verify_frozen_release_source
    [[ -d "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR" \
        && -f "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST" \
        && "$(file_sha256 "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST")" == "${PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256:-}" ]] \
        || die "Exact committed Iran source payload was not prepared before writer quiescence."
    log "Syncing production payload to the Iran host"
    ensure_runtime_env_file
    resolve_production_coin_runtime_contract
    local staging_dir="$IRAN_PROJECT_DIR"
    ssh_iran "mkdir -p '$IRAN_DEPLOY_BASE_DIR' '$IRAN_DEPLOY_BASE_DIR/releases' '$REMOTE_RELEASE_STATE_DIR' '$staging_dir'"
    ensure_remote_production_coin_runtime_dir
    run_iran_transfer rsync -avz --delete \
        --exclude '.git' \
        --exclude '.github' \
        --exclude '.venv' \
        --exclude '.vscode' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude '.env.*' \
        --exclude 'frontend' \
        --exclude 'node_modules' \
        --exclude 'tests' \
        --exclude 'tmp' \
        --exclude 'uploads' \
        --exclude 'map_data' \
        --exclude 'audit_trail' \
        -e "$RSYNC_SSH" \
        "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR/" "$IRAN_SSH_TARGET:$staging_dir/"
    local local_pip_hash_file="$LOCAL_PROJECT_DIR/pip_packages/.requirements_hash"
    local remote_pip_hash=""
    if [[ -f "$local_pip_hash_file" ]]; then
        remote_pip_hash="$(ssh_iran "cat '$staging_dir/pip_packages/.requirements_hash' 2>/dev/null || true")"
    fi
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && -f "$local_pip_hash_file" && "$remote_pip_hash" == "$(cat "$local_pip_hash_file")" ]]; then
        log "Remote pip wheelhouse already matches requirements; skipping pip package sync."
    else
        run_iran_transfer rsync -avz --delete -e "$RSYNC_SSH" \
            "$LOCAL_PROJECT_DIR/pip_packages/" "$IRAN_SSH_TARGET:$staging_dir/pip_packages/"
    fi
    run_iran_transfer rsync -avz --delete -e "$RSYNC_SSH" \
        "$LOCAL_DIST_DIR/" "$IRAN_SSH_TARGET:$staging_dir/mini_app_dist/"
    local remote_manifest_candidate="$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST.$RELEASE_SHA.tmp"
    scp_iran "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST" \
        "$IRAN_SSH_TARGET:$remote_manifest_candidate"
    ssh_iran "set -euo pipefail
[ -f '$remote_manifest_candidate' ] && [ ! -L '$remote_manifest_candidate' ]
[ \"\$(sha256sum '$remote_manifest_candidate' | awk '{print \$1}')\" = '$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256' ]
chmod 0600 '$remote_manifest_candidate'
mv -f -- '$remote_manifest_candidate' '$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST'
cd '$IRAN_PROJECT_DIR'
sha256sum -c '$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST' >/dev/null" \
        || die "Iran committed source payload failed its exact file manifest."
    ssh_iran "set -euo pipefail
assets_dir='$staging_dir/mini_app_dist/assets'
find \"\$assets_dir\" -maxdepth 1 -type f -name 'MarketView-*.js' | grep -q . || exit 21
grep -h -q 'api/offers/market-history' \"\$assets_dir\"/MarketView-*.js || exit 22" \
        || die "Remote Iran frontend release contract failed: deployed MarketView bundle cannot load read-only terminal market offers."
    atomic_install_iran_runtime_env
    ssh_iran "set -euo pipefail
command -v setfacl >/dev/null
id www-data >/dev/null 2>&1
setfacl -m u:www-data:--x '$IRAN_PROJECT_DIR'
runuser -u www-data -- test -r '$IRAN_PROJECT_DIR/mini_app_dist/index.html'
runuser -u www-data -- test ! -r '$IRAN_PROJECT_DIR/.env'" \
        || die "Iran web root ACL could not grant nginx traversal without exposing runtime env."
    verify_installed_runtime_env_pair
    verify_remote_immutable_runtime_payload
    log "Production payload sync complete"
}

verify_remote_immutable_runtime_payload() {
    verify_frozen_release_source
    local expected_compose_sha expected_dist_sha observed
    [[ -f "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST" \
        && "$(file_sha256 "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST")" == "${PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256:-}" ]] \
        || die "Local committed Iran source manifest drifted."
    expected_compose_sha="$(file_sha256 "$LOCAL_PROJECT_DIR/docker-compose.iran.yml")"
    expected_dist_sha="$(directory_sha256 "$LOCAL_DIST_DIR")"
    observed="$(ssh_iran "set -euo pipefail
compose_file='$IRAN_PROJECT_DIR/docker-compose.iran.yml'
dist_dir='$IRAN_PROJECT_DIR/mini_app_dist'
[ -f \"\$compose_file\" ] && [ ! -L \"\$compose_file\" ]
[ -d \"\$dist_dir\" ] && [ ! -L \"\$dist_dir\" ]
[ -f '$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST' ] && [ ! -L '$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST' ]
[ \"\$(sha256sum '$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST' | awk '{print \$1}')\" = '$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256' ]
cd '$IRAN_PROJECT_DIR'
sha256sum -c '$REMOTE_IRAN_SOURCE_PAYLOAD_MANIFEST' >/dev/null
compose_sha=\"\$(sha256sum \"\$compose_file\" | awk '{print \$1}')\"
dist_sha=\"\$(cd \"\$dist_dir\" && find . -type f -print0 | LC_ALL=C sort -z | xargs -r -0 sha256sum | sha256sum | awk '{print \$1}')\"
printf '%s %s %s\\n' \"\$compose_sha\" \"\$dist_sha\" '$PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256'")" \
        || die "Iran immutable runtime payload verification failed."
    [[ "$observed" == "$expected_compose_sha $expected_dist_sha $PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256" ]] \
        || die "Iran Compose/frontend payload drifted from the exact release artifacts."
}

ship_images() {
    local bundle="$LOCAL_IMAGE_BUNDLE"
    [[ -f "$bundle" ]] || die "Docker image bundle missing: $bundle"
    local bundle_sha remote_bundle_sha remote_bundle_candidate remote_sha_candidate
    bundle_sha="$(file_sha256 "$bundle")"
    [[ "$bundle_sha" =~ ^[0-9a-f]{64}$ ]] || die "Local Docker image bundle checksum is invalid."
    if ! remote_bundle_sha="$(ssh_iran "set -euo pipefail
bundle='$REMOTE_IMAGE_BUNDLE'
if [ ! -e \"\$bundle\" ]; then
  printf 'missing\\n'
elif [ -f \"\$bundle\" ] && [ ! -L \"\$bundle\" ]; then
  sha256sum \"\$bundle\" | awk '{print \$1}'
else
  exit 41
fi")"; then
        die "Could not safely inspect the existing Iran Docker image bundle."
    fi
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && "$remote_bundle_sha" == "$bundle_sha" ]]; then
        log "Docker image bundle already exists on Iran with matching checksum; skipping upload."
        return 0
    fi
    remote_bundle_candidate="${REMOTE_IMAGE_BUNDLE}.uploading"
    remote_sha_candidate="${REMOTE_IMAGE_BUNDLE_SHA}.uploading"
    log "Uploading Docker image bundle to the Iran host"
    ssh_iran "set -euo pipefail
mkdir -p '$IRAN_DEPLOY_BASE_DIR/releases' '$REMOTE_RELEASE_STATE_DIR'
rm -f -- '$remote_bundle_candidate' '$remote_sha_candidate'"
    scp_iran "$bundle" "$IRAN_SSH_TARGET:$remote_bundle_candidate"
    ssh_iran "set -euo pipefail
candidate='$remote_bundle_candidate'
bundle='$REMOTE_IMAGE_BUNDLE'
sha_candidate='$remote_sha_candidate'
sha_file='$REMOTE_IMAGE_BUNDLE_SHA'
cleanup() { rm -f -- \"\$candidate\" \"\$sha_candidate\"; }
trap cleanup EXIT
[ -f \"\$candidate\" ] && [ ! -L \"\$candidate\" ]
candidate_sha=\"\$(sha256sum \"\$candidate\" | awk '{print \$1}')\"
[ \"\$candidate_sha\" = '$bundle_sha' ]
chmod 0600 \"\$candidate\"
mv -f -- \"\$candidate\" \"\$bundle\"
[ -f \"\$bundle\" ] && [ ! -L \"\$bundle\" ]
installed_sha=\"\$(sha256sum \"\$bundle\" | awk '{print \$1}')\"
[ \"\$installed_sha\" = '$bundle_sha' ]
printf '%s\\n' '$bundle_sha' > \"\$sha_candidate\"
chmod 0600 \"\$sha_candidate\"
mv -f -- \"\$sha_candidate\" \"\$sha_file\"
trap - EXIT"
    remote_bundle_sha="$(ssh_iran "set -euo pipefail
bundle='$REMOTE_IMAGE_BUNDLE'
[ -f \"\$bundle\" ] && [ ! -L \"\$bundle\" ]
sha256sum \"\$bundle\" | awk '{print \$1}'")" \
        || die "Could not verify the installed Iran Docker image bundle."
    [[ "$remote_bundle_sha" == "$bundle_sha" ]] \
        || die "Iran Docker image bundle checksum does not match the local release bundle after upload."
    log "Docker image bundle upload complete"
}

verify_remote_iran_image_identity() {
    local image_signature="$1" expected_id local_portable_sha remote_identity
    local remote_id remote_revision remote_tree remote_signature remote_portable_sha
    expected_id="$(docker image inspect --format '{{.Id}}' trading_bot_base_iran)"
    [[ "$expected_id" == "$PRODUCTION_IRAN_IMAGE_ID" \
        && "$expected_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || die "Local Iran image ID is invalid."
    local_portable_sha="$(
        docker image inspect \
            --format '{{.Os}}|{{.Architecture}}|{{.Created}}|{{json .Config}}|{{json .RootFS}}' \
            trading_bot_base_iran:latest | sha256sum | awk '{print $1}'
    )"
    [[ "$local_portable_sha" =~ ^[0-9a-f]{64}$ ]] \
        || die "Local Iran portable image identity is invalid."
    remote_identity="$(ssh_iran "set -euo pipefail
image='trading_bot_base_iran:latest'
portable_sha=\"\$(docker image inspect --format '{{.Os}}|{{.Architecture}}|{{.Created}}|{{json .Config}}|{{json .RootFS}}' \"\$image\" | sha256sum | awk '{print \$1}')\"
docker image inspect --format '{{.Id}}|{{index .Config.Labels \"org.opencontainers.image.revision\"}}|{{index .Config.Labels \"io.gold-trade.release.tree\"}}|{{index .Config.Labels \"io.gold-trade.release.input-signature\"}}' \"\$image\"
printf '|%s\\n' \"\$portable_sha\"")"
    remote_identity="$(printf '%s' "$remote_identity" | tr -d '\n')"
    IFS='|' read -r remote_id remote_revision remote_tree remote_signature remote_portable_sha \
        <<<"$remote_identity"
    [[ "$remote_id" =~ ^sha256:[0-9a-f]{64}$ \
        && "$remote_revision" == "$RELEASE_SHA" \
        && "$remote_tree" == "$PRODUCTION_RELEASE_TREE" \
        && "$remote_signature" == "$image_signature" \
        && "$remote_portable_sha" == "$local_portable_sha" ]] \
        || die "Remote Iran image identity/OCI labels do not match the exact release bundle."
    # Docker's classic and containerd image stores may expose different ID
    # values for the same loaded archive (config digest versus manifest
    # digest). Runtime checks use the target host's ID after exact portable
    # image content and release labels have passed.
    PRODUCTION_IRAN_REMOTE_IMAGE_ID="$remote_id"
}

load_images() {
    local bundle="$LOCAL_IMAGE_BUNDLE"
    [[ -f "$bundle" ]] || die "Docker image bundle missing: $bundle"
    verify_iran_image_build_receipt
    local bundle_sha remote_bundle_sha image_signature remote_loaded_signature
    local remote_loaded_binding
    bundle_sha="$(file_sha256 "$bundle")"
    [[ "$bundle_sha" =~ ^[0-9a-f]{64}$ ]] || die "Local Docker image bundle checksum is invalid."
    remote_bundle_sha="$(ssh_iran "set -euo pipefail
bundle='$REMOTE_IMAGE_BUNDLE'
[ -f \"\$bundle\" ] && [ ! -L \"\$bundle\" ]
sha256sum \"\$bundle\" | awk '{print \$1}'")" \
        || die "Iran Docker image bundle is missing or cannot be safely hashed."
    [[ "$remote_bundle_sha" == "$bundle_sha" ]] \
        || die "Iran Docker image bundle checksum does not match the local release bundle; refusing docker load."
    if [[ -f "$LOCAL_IMAGE_SIGNATURE_FILE" ]]; then
        image_signature="$(cat "$LOCAL_IMAGE_SIGNATURE_FILE")"
    else
        image_signature="$(file_sha256 "$bundle")"
    fi
    remote_loaded_signature="$(ssh_iran "cat '$REMOTE_IMAGE_LOADED_SIGNATURE' 2>/dev/null || true")"
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && "$remote_loaded_signature" == "$image_signature" ]]; then
        remote_loaded_binding="$(ssh_iran "docker image inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}|{{index .Config.Labels \"io.gold-trade.release.tree\"}}|{{index .Config.Labels \"io.gold-trade.release.input-signature\"}}' trading_bot_base_iran:latest 2>/dev/null || true")"
        if [[ "$remote_loaded_binding" == "$RELEASE_SHA|$PRODUCTION_RELEASE_TREE|$image_signature" ]] \
            && ssh_iran "docker image inspect trading_bot_base_iran:latest >/dev/null 2>&1 && docker image inspect postgres:15-alpine >/dev/null 2>&1 && docker image inspect redis:7-alpine >/dev/null 2>&1"; then
            verify_remote_iran_image_identity "$image_signature"
            log "Docker images already loaded on Iran with matching signature; skipping docker load."
            return 0
        fi
        log "Docker image cache signature matched but its release binding or required images did not; reloading the exact bundle."
    fi
    log "Loading transferred Docker images on the Iran host"
    ssh_iran "set -euo pipefail
mkdir -p '$REMOTE_RELEASE_STATE_DIR'
bundle='$REMOTE_IMAGE_BUNDLE'
[ -f \"\$bundle\" ] && [ ! -L \"\$bundle\" ]
actual_bundle_sha=\"\$(sha256sum \"\$bundle\" | awk '{print \$1}')\"
[ \"\$actual_bundle_sha\" = '$bundle_sha' ]
docker load -i '$REMOTE_IMAGE_BUNDLE'
if docker image inspect 'postgres:15-alpine-iran-$IRAN_HOST_ARCH' >/dev/null 2>&1; then
  docker tag 'postgres:15-alpine-iran-$IRAN_HOST_ARCH' 'postgres:15-alpine'
fi
if docker image inspect 'redis:7-alpine-iran-$IRAN_HOST_ARCH' >/dev/null 2>&1; then
  docker tag 'redis:7-alpine-iran-$IRAN_HOST_ARCH' 'redis:7-alpine'
fi
printf '%s\n' '$image_signature' > '$REMOTE_IMAGE_LOADED_SIGNATURE'"
    verify_remote_iran_image_identity "$image_signature"
    log "Docker images loaded on the Iran host"
}

foreign_iran_source_sequence_floor() {
    local output floor
    output="$(
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD run --rm --no-deps migration \
            python scripts/align_change_log_source_sequence.py \
            watermark-floor --source-server iran --format value
    )"
    floor="$(printf '%s\n' "$output" | sed '/^[[:space:]]*$/d' | tail -n 1)"
    [[ "$floor" =~ ^[0-9]+$ ]] || die "Could not determine the Iran source-sequence floor from foreign watermarks: $output"
    printf '%s\n' "$floor"
}

deploy_iran() {
    log "Deploying Docker services on the Iran host"
    local defer_writer_start="${1:-0}"
    local compose_resolver iran_source_sequence_floor
    [[ "$defer_writer_start" == "0" || "$defer_writer_start" == "1" ]] \
        || die "Iran writer deferral must be 0 or 1."
    verify_frozen_release_source
    verify_remote_immutable_runtime_payload
    ensure_remote_production_coin_runtime_dir 1
    compose_resolver="$(remote_compose_resolver)"
    iran_source_sequence_floor="$(foreign_iran_source_sequence_floor)"
    log "Iran change_log source-sequence floor from foreign watermarks: $iran_source_sequence_floor"
    # This is deliberately repeated after writer quiescence and immediately
    # before the Iran migration.  A loaded tag or local bundle may not drift
    # from the independent receipt once the release transaction has started.
    verify_frozen_release_source
    verify_remote_immutable_runtime_payload
    verify_iran_image_build_receipt
    verify_remote_iran_image_identity "$PRODUCTION_IRAN_IMAGE_SIGNATURE"
    ssh_iran "set -euo pipefail
$compose_resolver
cd '$IRAN_PROJECT_DIR'
wait_args=''
if [ '$IRAN_DEPLOY_WITH_WAIT' = '1' ] && [ \"\$compose_cmd\" = 'docker compose' ]; then
  wait_args='--wait --wait-timeout 180'
fi
for service in app sync_worker migration; do
  ids=\"\$(docker ps -aq --filter label=com.docker.compose.service=\$service --filter label=com.docker.compose.project=current)\"
  if [ -n \"\$ids\" ]; then
    docker rm -f \$ids >/dev/null 2>&1 || true
  fi
done
for container_name in trading_bot_app trading_bot_sync_worker trading_bot_migration; do
  docker rm -f \"\$container_name\" >/dev/null 2>&1 || true
done
eval \"\$compose_cmd -f docker-compose.iran.yml up -d --no-recreate db redis\"
for attempt in \$(seq 1 60); do
  db_id=\"\$(docker ps -q --filter label=com.docker.compose.service=db --filter label=com.docker.compose.project=current | head -n 1)\"
  db_health=''
  if [ -n \"\$db_id\" ]; then
    db_health=\"\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \"\$db_id\" 2>/dev/null || true)\"
  fi
  if [ \"\$db_health\" = 'healthy' ] || [ \"\$db_health\" = 'running' ]; then
    break
  fi
  if [ \"\$attempt\" -eq 60 ]; then
    echo \"Iran database did not become healthy before migration.\" >&2
    exit 1
  fi
  sleep 2
done
eval \"\$compose_cmd -f docker-compose.iran.yml run --rm --no-deps migration\"
docker rm -f trading_bot_migration >/dev/null 2>&1 || true
eval \"\$compose_cmd -f docker-compose.iran.yml run --rm --no-deps migration python scripts/align_change_log_source_sequence.py align --floor '$iran_source_sequence_floor'\"
docker rm -f trading_bot_migration >/dev/null 2>&1 || true
if [ '$defer_writer_start' = '1' ]; then
  echo 'Iran writer startup deferred until the official two-host schema gate passes.'
else
  eval \"\$compose_cmd -f docker-compose.iran.yml up -d --no-deps \$wait_args app sync_worker\"
fi
eval \"\$compose_cmd -f docker-compose.iran.yml ps\""
    if [[ "$defer_writer_start" == "0" ]]; then
        repair_registry_fingerprint_rollout_quarantine
    fi
    log "Iran deploy step complete"
}

local_runtime_compatibility() {
    (
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD exec -T app python -c \
            'import json; from core.config import settings; from core.sync_protocol import current_sync_registry_fingerprint; print(json.dumps({"release_sha": settings.release_sha, "registry_fingerprint": current_sync_registry_fingerprint()}, sort_keys=True))'
    )
}

iran_runtime_compatibility() {
    ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T app python -c 'import json; from core.config import settings; from core.sync_protocol import current_sync_registry_fingerprint; print(json.dumps({\"release_sha\": settings.release_sha, \"registry_fingerprint\": current_sync_registry_fingerprint()}, sort_keys=True))'"
}

repair_registry_fingerprint_rollout_quarantine() {
    local foreign_runtime iran_runtime foreign_release iran_release foreign_registry iran_registry
    for attempt in $(seq 1 60); do
        foreign_runtime="$(local_runtime_compatibility 2>/dev/null || true)"
        iran_runtime="$(iran_runtime_compatibility 2>/dev/null || true)"
        if [[ -n "$foreign_runtime" && -n "$iran_runtime" ]]; then
            break
        fi
        if [[ "$attempt" -eq 60 ]]; then
            die "Could not read runtime compatibility from both production servers"
        fi
        sleep 2
    done

    foreign_release="$(printf '%s' "$foreign_runtime" | extract_json_field release_sha)"
    iran_release="$(printf '%s' "$iran_runtime" | extract_json_field release_sha)"
    foreign_registry="$(printf '%s' "$foreign_runtime" | extract_json_field registry_fingerprint)"
    iran_registry="$(printf '%s' "$iran_runtime" | extract_json_field registry_fingerprint)"
    [[ -n "$RELEASE_SHA" && "$foreign_release" == "$RELEASE_SHA" && "$iran_release" == "$RELEASE_SHA" ]] \
        || die "Refusing sync-quarantine repair because production release SHAs are not identical"
    [[ -n "$foreign_registry" && "$foreign_registry" == "$iran_registry" ]] \
        || die "Refusing sync-quarantine repair because registry fingerprints are not identical"

    log "Releasing only rollout registry-fingerprint quarantines after exact runtime compatibility verification"
    (
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD run --rm --no-deps migration \
            python scripts/repair_registry_fingerprint_quarantine.py repair \
            --expected-release-sha "$RELEASE_SHA" \
            --expected-registry-fingerprint "$foreign_registry" \
            --confirm RELEASE_REGISTRY_FINGERPRINT_QUARANTINE
    )
    ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml run --rm --no-deps migration \
  python scripts/repair_registry_fingerprint_quarantine.py repair \
  --expected-release-sha '$RELEASE_SHA' \
  --expected-registry-fingerprint '$foreign_registry' \
  --confirm RELEASE_REGISTRY_FINGERPRINT_QUARANTINE"
}

shell_quote() {
    python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$1"
}

extract_json_field() {
    local field="$1"
    python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$field"
}

extract_sync_unsynced_count() {
    python3 -c 'import json, sys; print(int(json.load(sys.stdin).get("unsynced_change_log_count", 0)))'
}

url_scheme() {
    python3 -c 'from urllib.parse import urlparse; import sys; print((urlparse(sys.argv[1]).scheme or "").lower())' "$1"
}

assert_iran_public_listener_ready() {
    local scheme
    scheme="$(url_scheme "$IRAN_HEALTHCHECK_URL")"
    case "$scheme" in
        https)
            ssh_iran "set -euo pipefail
if ! ss -ltn | awk 'NR > 1 {print \$4}' | grep -Eq '(^|:)443$'; then
  echo 'Iran reverse proxy is not listening on TCP 443.' >&2
  exit 21
fi
nginx_dump=\$(nginx -T 2>/dev/null || true)
grep -Eq 'listen[[:space:]]+443([^0-9]|$)' <<< \"\$nginx_dump\" || {
  echo 'Iran active Nginx config has no listen 443 server block.' >&2
  exit 22
}
grep -q 'ssl_certificate ' <<< \"\$nginx_dump\" || {
  echo 'Iran active Nginx config has no ssl_certificate directive.' >&2
  exit 23
}"
            ;;
        http)
            ssh_iran "set -euo pipefail
if ! ss -ltn | awk 'NR > 1 {print \$4}' | grep -Eq '(^|:)80$'; then
  echo 'Iran reverse proxy is not listening on TCP 80.' >&2
  exit 24
fi"
            ;;
        *)
            log "Skipping Iran public listener assertion for unsupported URL scheme: $scheme"
            ;;
    esac
}

run_iran_migration_python() {
    local script_args="$*"
    local compose_resolver
    compose_resolver="$(remote_compose_resolver)"
    ssh_iran "set -euo pipefail
$compose_resolver
cd '$IRAN_PROJECT_DIR'
eval \"\$compose_cmd -f docker-compose.iran.yml run --rm --no-deps migration python $script_args\""
}

wait_for_iran_local_api() {
    local compose_resolver
    compose_resolver="$(remote_compose_resolver)"
    ssh_iran "set -euo pipefail
$compose_resolver
cd '$IRAN_PROJECT_DIR'
for _attempt in \$(seq 1 24); do
  if curl -fsS '$IRAN_LOCAL_API_URL' >/dev/null; then
    break
  fi
  if [ \"\$_attempt\" -eq 24 ]; then
    echo 'Iran local API healthcheck did not become ready in time.' >&2
    exit 1
  fi
  sleep 5
done
eval \"\$compose_cmd -f docker-compose.iran.yml ps\" >/dev/null"
}

inspect_iran_shared_data() {
    log "Inspecting Iran shared-table state"
    local output inspection_json
    output="$(run_iran_migration_python "scripts/inspect_shared_sync_state.py --format json")"
    inspection_json="$(printf '%s\n' "$output" | sed '/^[[:space:]]*$/d' | tail -n 1)"
    printf '%s\n' "$inspection_json" | python3 -m json.tool
}

backup_iran_database_before_shared_reset() {
    local backup_path="$IRAN_DEPLOY_BASE_DIR/backups/iran-shared-reset-$(date -u +%Y%m%dT%H%M%SZ).sql"
    log "Backing up Iran database before shared-table reset: $backup_path"
    ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
mkdir -p '$IRAN_DEPLOY_BASE_DIR/backups'
\$compose_cmd -f docker-compose.iran.yml exec -T db sh -lc 'pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"' > '$backup_path'"
    log "Iran database backup completed: $backup_path"
}

confirm_iran_shared_reset() {
    if [[ "$IRAN_SHARED_RESET_CONFIRM" == "$IRAN_SHARED_RESET_CONFIRM_TEXT" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        die "Iran shared-table reset requires IRAN_SHARED_RESET_CONFIRM=$IRAN_SHARED_RESET_CONFIRM_TEXT in non-interactive mode."
    fi

    local confirm=""
    echo
    echo "This will reset Iran shared tables after taking a pg_dump backup."
    read -r -p "Type $IRAN_SHARED_RESET_CONFIRM_TEXT to continue: " confirm
    [[ "$confirm" == "$IRAN_SHARED_RESET_CONFIRM_TEXT" ]] || die "Iran shared-table reset was not confirmed."
}

reset_iran_shared_tables() {
    local iran_source_sequence_floor
    iran_source_sequence_floor="$(foreign_iran_source_sequence_floor)"
    confirm_iran_shared_reset
    backup_iran_database_before_shared_reset
    log "Resetting Iran shared tables and preserving source-sequence floor=$iran_source_sequence_floor"
ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T db sh -lc 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At' <<'SQL'
BEGIN;
TRUNCATE TABLE change_log, $SHARED_SYNC_TABLES_SQL RESTART IDENTITY CASCADE;
SELECT setval(
  pg_get_serial_sequence('change_log', 'id'),
  GREATEST($iran_source_sequence_floor, 1),
  $iran_source_sequence_floor > 0
);
COMMIT;
SQL"
    log "Iran shared-table reset completed"
}

mark_foreign_preseed_backlog_synced() {
    local cutoff="$1"
    local query="
UPDATE change_log
SET synced = true
WHERE synced = false
  AND table_name IN ('users', 'accountant_relations', 'customer_relations', 'telegram_link_tokens', 'invitations', 'admin_market_messages', 'admin_broadcast_messages', 'notifications', 'user_notification_preferences', 'user_blocks', 'commodities', 'commodity_aliases', 'trading_settings', 'market_schedule_overrides', 'market_runtime_state', 'offers', 'offer_publication_states', 'offer_requests', 'trades', 'trade_delivery_receipts', 'telegram_admin_broadcasts', 'telegram_admin_broadcast_receipts', 'telegram_notification_outbox')
  AND created_at <= '$cutoff'::timestamptz
RETURNING table_name;"
    log "Marking foreign pre-seed shared backlog as synced up to $cutoff"
    (cd "$LOCAL_PROJECT_DIR" && $LOCAL_COMPOSE_CMD exec -T db sh -lc "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"$query\" | sort | uniq -c")
}

mark_iran_seed_generated_backlog_synced() {
    log "Marking Iran seed-generated mandatory/system backlog as synced"
    ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T db sh -lc 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At' <<'SQL'
UPDATE change_log
SET synced = true
WHERE synced = false
  AND (
    (table_name = 'chats' AND data->>'is_system' = 'true' AND data->>'is_mandatory' = 'true')
    OR (table_name = 'chat_members' AND data->>'chat_is_system' = 'true' AND data->>'chat_is_mandatory' = 'true')
    OR table_name = 'market_runtime_state'
  )
RETURNING table_name;
SQL"
}

seed_shared_tables_to_iran() {
    local cutoff
    cutoff="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    wait_for_iran_local_api
    log "Seeding current shared-table state from foreign to Iran"
    (cd "$LOCAL_PROJECT_DIR" && $LOCAL_COMPOSE_CMD run --rm --no-deps migration python scripts/seed_shared_sync_tables.py --target-server iran --batch-size "$IRAN_SHARED_SEED_BATCH_SIZE")
    mark_foreign_preseed_backlog_synced "$cutoff"
    mark_iran_seed_generated_backlog_synced
    verify_shared_sync_health_clean
}

verify_shared_sync_health_clean() {
    log "Verifying cross-server sync health"
    local foreign_observability_key iran_observability_key foreign_health iran_health foreign_unsynced iran_unsynced iran_header
    foreign_observability_key="$(read_env_value "$FOREIGN_RUNTIME_ENV_PATH" "OBSERVABILITY_API_KEY")"
    iran_observability_key="$(read_env_value "$IRAN_RUNTIME_ENV_PATH" "OBSERVABILITY_API_KEY")"
    [[ -n "$foreign_observability_key" ]] || die "OBSERVABILITY_API_KEY is missing from $FOREIGN_RUNTIME_ENV_PATH"
    [[ -n "$iran_observability_key" ]] || die "OBSERVABILITY_API_KEY is missing from $IRAN_RUNTIME_ENV_PATH"

    foreign_health="$(curl -fsS "http://127.0.0.1:8000/api/sync/health" -H "X-Observability-Api-Key: $foreign_observability_key")"
    foreign_unsynced="$(printf '%s' "$foreign_health" | extract_sync_unsynced_count)"
    [[ "$foreign_unsynced" == "0" ]] || die "Foreign sync backlog is not clean after shared-table seed: $foreign_health"

    iran_header="$(shell_quote "X-Observability-Api-Key: $iran_observability_key")"
    iran_health="$(ssh_iran "curl -fsS 'http://127.0.0.1:8000/api/sync/health' -H $iran_header")"
    iran_unsynced="$(printf '%s' "$iran_health" | extract_sync_unsynced_count)"
    [[ "$iran_unsynced" == "0" ]] || die "Iran sync backlog is not clean after shared-table seed: $iran_health"
    log "Cross-server sync health is clean"
}

decide_existing_shared_data_action() {
    local normalized
    normalized="$(printf '%s' "$IRAN_SHARED_DATA_MODE" | tr '[:upper:]' '[:lower:]')"
    case "$normalized" in
        skip)
            printf 'skip\n'
            return 0
            ;;
        reset)
            printf 'reset\n'
            return 0
            ;;
        abort)
            printf 'abort\n'
            return 0
            ;;
        auto|ask|"")
            ;;
        *)
            die "Unsupported IRAN_SHARED_DATA_MODE: $IRAN_SHARED_DATA_MODE"
            ;;
    esac

    if [[ ! -t 0 ]]; then
        die "Iran shared tables contain existing data. Set IRAN_SHARED_DATA_MODE=skip, reset, or abort."
    fi

    echo
    echo "Iran shared tables contain existing project data."
    echo "Choose one action:"
    echo "  skip  - keep Iran data unchanged and continue deploy (default)"
    echo "  reset - pg_dump backup, reset shared tables, then seed current state from foreign"
    echo "  abort - stop release without changing Iran data"
    local action=""
    read -r -p "Action [skip/reset/abort] (default: skip): " action
    action="$(printf '%s' "${action:-skip}" | tr '[:upper:]' '[:lower:]')"
    case "$action" in
        skip|reset|abort) printf '%s\n' "$action" ;;
        *) die "Unsupported shared-data action: $action" ;;
    esac
}

handle_iran_shared_data() {
    local normalized inspection_output inspection_json classification signal_total action
    normalized="$(printf '%s' "$IRAN_SHARED_DATA_MODE" | tr '[:upper:]' '[:lower:]')"
    if [[ "$normalized" == "skip" ]]; then
        log "Skipping Iran shared-table seed/reset because IRAN_SHARED_DATA_MODE=skip"
        return 0
    fi

    log "Inspecting Iran shared tables before seed/reset"
    inspection_output="$(run_iran_migration_python "scripts/inspect_shared_sync_state.py --format json")"
    inspection_json="$(printf '%s\n' "$inspection_output" | sed '/^[[:space:]]*$/d' | tail -n 1)"
    classification="$(printf '%s' "$inspection_json" | extract_json_field classification)"
    signal_total="$(printf '%s' "$inspection_json" | extract_json_field signal_total)"
    log "Iran shared data classification=$classification signal_total=$signal_total"

    case "$classification" in
        fresh)
            if [[ "$normalized" == "reset" ]]; then
                reset_iran_shared_tables
            fi
            seed_shared_tables_to_iran
            ;;
        existing)
            action="$(decide_existing_shared_data_action)"
            case "$action" in
                skip)
                    log "Keeping existing Iran shared data unchanged"
                    ;;
                reset)
                    reset_iran_shared_tables
                    seed_shared_tables_to_iran
                    ;;
                abort)
                    die "Release aborted because Iran shared tables contain existing data."
                    ;;
            esac
            ;;
        *)
            die "Could not classify Iran shared-table state: $inspection_json"
            ;;
    esac
}

healthcheck() {
    log "Running post-deploy health checks"
    wait_for_iran_local_api
    verify_sync_sampler_local
    verify_sync_sampler_remote
    if [[ "$IRAN_RUN_POST_DEPLOY_HEALTHCHECK" == "1" ]]; then
        assert_iran_public_listener_ready
        for _attempt in $(seq 1 24); do
            if curl -kfsS "$IRAN_HEALTHCHECK_URL" >/dev/null; then
                break
            fi
            if [[ "$_attempt" -eq 24 ]]; then
                die "Iran public healthcheck did not become ready in time: $IRAN_HEALTHCHECK_URL"
            fi
            sleep 5
        done
    fi
    verify_no_sync_quarantines
    run_production_data_hygiene_checks
    log "Health checks passed"
}

verify_no_sync_quarantines() {
    local foreign_count iran_count
    foreign_count="$(
        cd "$LOCAL_PROJECT_DIR"
        $LOCAL_COMPOSE_CMD exec -T db sh -lc \
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM change_log WHERE synced = false AND quarantined_at IS NOT NULL"'
    )"
    iran_count="$(ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T db sh -lc 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc \"SELECT COUNT(*) FROM change_log WHERE synced = false AND quarantined_at IS NOT NULL\"'")"
    [[ "$foreign_count" == "0" ]] || die "Foreign sync still contains quarantined change-log rows: $foreign_count"
    [[ "$iran_count" == "0" ]] || die "Iran sync still contains quarantined change-log rows: $iran_count"
}

run_production_data_hygiene_foreign() {
    log "Running production data hygiene check on foreign"
    [[ -f "$PRODUCTION_DATA_HYGIENE_SCRIPT" ]] || die "Production data hygiene script missing: $PRODUCTION_DATA_HYGIENE_SCRIPT"
    (cd "$LOCAL_PROJECT_DIR" && $LOCAL_COMPOSE_CMD exec -T app python scripts/check_production_data_hygiene.py --role foreign --json --fail-on high)
}

run_production_data_hygiene_iran() {
    log "Running production data hygiene check on Iran"
    ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T app python scripts/check_production_data_hygiene.py --role iran --json --fail-on high"
}

run_production_data_hygiene_checks() {
    # VF9 security guard is intentionally read-only. It is complete at code
    # level and runs as part of production-release healthcheck. If it fails,
    # operators must inspect the redacted report and explicitly decide cleanup;
    # this release script must not auto-delete production data.
    run_production_data_hygiene_foreign
    run_production_data_hygiene_iran
    log "Production data hygiene checks passed"
}

decide_iran_connectivity() {
    local normalized
    normalized="$(printf '%s' "$IRAN_CONNECTIVITY_MODE" | tr '[:upper:]' '[:lower:]')"
    case "$normalized" in
        online|yes|y|1)
            printf 'online\n'
            return 0
            ;;
        offline|no|n|0)
            printf 'offline\n'
            return 0
            ;;
        ask|"")
            ;;
        *)
            die "Unsupported IRAN_CONNECTIVITY_MODE: $IRAN_CONNECTIVITY_MODE"
            ;;
    esac

    echo
    echo "Production preflight is ready."
    read -r -p "Does the Iran server currently have working internet access? [yes/no]: " answer
    answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
    case "$answer" in
        yes|y) printf 'online\n' ;;
        no|n) printf 'offline\n' ;;
        *) die "Please answer yes or no." ;;
    esac
}

run_release() {
    local iran_mode="${1:-}"
    if [[ -z "$iran_mode" ]]; then
        iran_mode="$(decide_iran_connectivity)"
    fi
    if [[ "$iran_mode" == "offline" ]]; then
        die "Iran offline scenario is not implemented; no mutable release preparation, production writer quiescence, or migration was started."
    fi
    [[ "$iran_mode" == "online" ]] || die "Production release connectivity decision was not online."
    prepare_local_release_inputs
    trap production_release_exit_guard EXIT
    [[ "$IRAN_SKIP_FOREIGN_DEPLOY" == "0" ]] \
        || die "The full two-host production release cannot skip the foreign commit."
    # Reject a different code/env pair before any remote bootstrap, image load,
    # or other release preparation when a prior two-host transaction is open.
    if [[ "$PRODUCTION_QUEUE_CUTOVER_REBUILD_EVIDENCE" == "1" ]]; then
        log "Rebuilding release evidence for the guarded Queue ownership transition."
        prepare_release_evidence_artifacts
    fi
    prepare_committed_iran_source_payload
    load_two_host_release_state
    ensure_local_timezone_utc
    install_sync_sampler_local
    sync_hosts_mappings
    verify_prepared_release_artifacts
    bootstrap_iran
    configure_nginx
    issue_cert
    ship_images
    load_images
    verify_foreign_image_build_receipt
    verify_release_evidence_gate
    verify_frozen_release_source
    begin_two_host_release_transaction
    capture_production_coin_input_timer_recovery_state
    install_and_verify_production_coin_inputs
    suspend_production_coin_snapshot_relay
    verify_frozen_release_source
    quiesce_two_host_writers_for_migration
    deploy_foreign 1
    write_two_host_release_state foreign_committed
    verify_sync_sampler_local
    sync_project
    write_two_host_release_state iran_payload_installed
    reconcile_production_coin_snapshot_relay
    install_sync_sampler_remote
    deploy_iran 1
    write_two_host_release_state iran_committed
    handle_iran_shared_data
    verify_two_host_schema_head
    if [[ "$PRODUCTION_COIN_INFERENCE_REQUESTED" == "1" ]]; then
        verify_production_coin_snapshot_relay
    fi
    start_two_host_writers_after_schema_convergence
    verify_running_production_coin_consumers
    repair_registry_fingerprint_rollout_quarantine
    healthcheck
    finalize_two_host_writer_restart_policies
    PRODUCTION_COIN_SNAPSHOT_RELAY_GUARD_ARMED=0
    clear_production_coin_relay_recovery_marker
    clear_two_host_release_state
    if [[ "$PRODUCTION_COIN_INFERENCE_REQUESTED" == "1" ]]; then
        clear_production_coin_input_timer_recovery_state \
            || die "Could not commit the production coin input timer transition."
    fi
    release_production_locks
    trap - EXIT
}

main() {
    local release_iran_mode=""
    parse_args "$@"
    if [[ "$COMMAND" == "help" ]]; then
        usage
        exit 0
    fi
    [[ -f "$MANIFEST_PATH" ]] || die "Manifest not found: $MANIFEST_PATH"
    load_manifest
    if [[ "$COMMAND" == "release" ]]; then
        # Connectivity is the first release decision after the read-only
        # manifest load. In particular, do not acquire/create release locks,
        # install packages, render envs, or build artifacts for an offline run.
        release_iran_mode="$(decide_iran_connectivity)"
        if [[ "$release_iran_mode" == "offline" ]]; then
            die "Iran offline scenario is not implemented; no mutable release preparation, production writer quiescence, or migration was started."
        fi
        [[ "$release_iran_mode" == "online" ]] \
            || die "Production release connectivity decision was not online."
    fi
    trap release_production_locks EXIT
    guard_production_release_command
    case "$COMMAND" in
        check-local) check_local ;;
        release) run_release "$release_iran_mode" ;;
        prepare-release-evidence)
            prepare_local_release_inputs
            prepare_release_evidence_artifacts
            ;;
        verify-release-evidence)
            prepare_local_release_inputs
            prepare_committed_iran_source_payload
            load_two_host_release_state
            load_foreign_image_build_receipt
            load_iran_image_build_receipt
            verify_release_evidence_gate
            load_two_host_release_state
            ;;
        deploy-foreign) prepare_local_release_inputs; install_sync_sampler_local; build_release; deploy_foreign; verify_sync_sampler_local ;;
        bootstrap-iran) prepare_local_release_inputs; bootstrap_iran ;;
        configure-nginx) prepare_local_release_inputs; configure_nginx ;;
        issue-cert) prepare_local_release_inputs; issue_cert ;;
        build-release) prepare_local_release_inputs; prepare_release_evidence_artifacts ;;
        sync-project) prepare_local_release_inputs; sync_project ;;
        ship-images) prepare_local_release_inputs; ship_images ;;
        load-images) prepare_local_release_inputs; load_images ;;
        deploy-iran) prepare_local_release_inputs; install_sync_sampler_remote; deploy_iran; verify_sync_sampler_remote ;;
        inspect-shared-data) check_local; inspect_iran_shared_data ;;
        seed-shared-data) prepare_local_release_inputs; handle_iran_shared_data ;;
        healthcheck) check_local; healthcheck ;;
        *) die "Unknown command: $COMMAND" ;;
    esac
    release_production_locks
    trap - EXIT
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
