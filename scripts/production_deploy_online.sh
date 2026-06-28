#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_ENV_RENDERER="$PROJECT_DIR/scripts/render_runtime_envs.py"
RELEASE_ARTIFACT_RENDERER="$PROJECT_DIR/scripts/render_release_artifacts.py"
DEFAULT_MANIFEST="$PROJECT_DIR/deploy/production/online.env"
MANIFEST_PATH="${DEPLOY_MANIFEST:-$DEFAULT_MANIFEST}"
COMMAND=""
IRAN_BOOTSTRAP_APT_PACKAGES="ca-certificates curl gnupg lsb-release rsync jq pigz nginx certbot python3-certbot-nginx docker.io docker-compose python3-pip python3-setuptools python3-wheel"
SHARED_SYNC_TABLES_SQL="users, accountant_relations, customer_relations, telegram_link_tokens, invitations, admin_market_messages, admin_broadcast_messages, notifications, user_blocks, commodities, commodity_aliases, trading_settings, market_schedule_overrides, market_runtime_state, offers, offer_publication_states, offer_requests, trades, trade_delivery_receipts"
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
FOREIGN_COMPOSE_PROJECT_NAME="${FOREIGN_COMPOSE_PROJECT_NAME:-trading_bot}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$FOREIGN_COMPOSE_PROJECT_NAME}"

usage() {
    cat <<'EOF'
Production release script driven from the foreign server.

Usage:
  scripts/production_deploy_online.sh [--manifest /path/to/online.env] [command]

Commands:
  help                 Show this help.
  release              Run the full production flow. This is the default.
  check-local          Validate local tooling and manifest.
  deploy-foreign       Build and deploy the foreign server locally.
  bootstrap-iran       Install Docker/Nginx/Certbot prerequisites on the Iran host.
  configure-nginx      Render and install the Iran Nginx config.
  issue-cert           Request/renew the SSL certificate on the Iran host.
  build-release        Build frontend locally, prepare wheel cache, and build/loadable Docker artifacts.
  sync-project         Rsync the production payload and runtime env to the Iran host.
  ship-images          Upload the prepared Docker image bundle to the Iran host.
  load-images          Load the uploaded Docker image bundle on the Iran host.
  deploy-iran          Start Docker services on the Iran host without remote build/pull.
  inspect-shared-data  Inspect Iran shared-table state and print the fresh/existing classification.
  seed-shared-data     Apply guarded shared-table seed/reset handling for the Iran host.
  healthcheck          Validate local and public health endpoints.

Notes:
  - The script first deploys the foreign server locally.
  - It then asks whether Iran currently has working internet.
  - If the answer is "yes", it runs the Iran-online flow using shipped images/artifacts.
  - If the answer is "no", it stops after foreign deploy because the Iran-offline flow is not implemented yet.
  - For SSH, prefer key-based auth. Password auth is supported only when sshpass is installed.
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
    python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$1"
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
  apt-get -o Acquire::Retries=5 install -y --fix-missing docker-compose || true
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
    local iran_ssh_port="22"
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
    local iran_env_source_path="/root/secure-envs/trading-bot/.env.iran.production"
    local local_env_path="/root/secure-envs/trading-bot/.env.foreign.production"
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
    prompt_value iran_env_source_path "Iran runtime env source path" "$iran_env_source_path"
    prompt_value local_env_path "Local .env path" "$local_env_path"
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
IRAN_CERTBOT_EMAIL=$iran_certbot_email

# --- Local / remote env files ---
LOCAL_ENV_SOURCE_PATH=$local_env_path
IRAN_ENV_SOURCE_PATH=$iran_env_source_path
FOREIGN_FRONTEND_URL=$foreign_frontend_url
IRAN_FRONTEND_URL=$iran_frontend_url
REQUIRE_WEB_PUSH=1
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

load_manifest() {
    local env_iran_connectivity_mode="${IRAN_CONNECTIVITY_MODE-}"

    [[ -f "$MANIFEST_PATH" ]] || die "Manifest not found: $MANIFEST_PATH"
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
    : "${IRAN_PROJECT_DIR:?IRAN_PROJECT_DIR is required}"
    : "${IRAN_DEPLOY_BASE_DIR:?IRAN_DEPLOY_BASE_DIR is required}"
    : "${FOREIGN_TIMEZONE:=UTC}"
    : "${IRAN_TIMEZONE:=UTC}"
    : "${IRAN_APP_DOMAIN:?IRAN_APP_DOMAIN is required}"
    : "${IRAN_CERTBOT_EMAIL:?IRAN_CERTBOT_EMAIL is required}"
    : "${IRAN_ENV_SOURCE_PATH:?IRAN_ENV_SOURCE_PATH is required}"
    : "${IRAN_PUBLIC_IP:?IRAN_PUBLIC_IP is required}"
    : "${IRAN_PUBLIC_DOMAIN:?IRAN_PUBLIC_DOMAIN is required}"
    : "${FOREIGN_PUBLIC_IP:?FOREIGN_PUBLIC_IP is required}"
    : "${FOREIGN_PUBLIC_DOMAIN:?FOREIGN_PUBLIC_DOMAIN is required}"
    FOREIGN_SERVER_URL="${FOREIGN_SERVER_URL:-https://$FOREIGN_PUBLIC_DOMAIN}"
    FOREIGN_SERVER_DOMAIN="${FOREIGN_SERVER_DOMAIN:-$FOREIGN_PUBLIC_DOMAIN}"
    IRAN_SERVER_URL="${IRAN_SERVER_URL:-https://$IRAN_APP_DOMAIN}"
    IRAN_SERVER_DOMAIN="${IRAN_SERVER_DOMAIN:-$IRAN_APP_DOMAIN}"
    LOCAL_ENV_SOURCE_PATH="${LOCAL_ENV_SOURCE_PATH:-$LOCAL_PROJECT_DIR/.env}"
    FOREIGN_FRONTEND_URL="${FOREIGN_FRONTEND_URL:-https://$FOREIGN_PUBLIC_DOMAIN}"
    IRAN_FRONTEND_URL="${IRAN_FRONTEND_URL:-https://$IRAN_APP_DOMAIN}"

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
    ENV_BACKUP_DIR="${ENV_BACKUP_DIR:-/root/secure-envs/trading-bot/backups}"
    IRAN_SHARED_DATA_MODE="${IRAN_SHARED_DATA_MODE:-auto}"
    IRAN_SHARED_SEED_BATCH_SIZE="${IRAN_SHARED_SEED_BATCH_SIZE:-50}"
    IRAN_SHARED_RESET_CONFIRM="${IRAN_SHARED_RESET_CONFIRM:-}"

    if [[ "$FOREIGN_TIMEZONE" != "UTC" || "$IRAN_TIMEZONE" != "UTC" ]]; then
        log "Overriding configured timezones to UTC for production release."
        FOREIGN_TIMEZONE="UTC"
        IRAN_TIMEZONE="UTC"
    fi

    [[ -d "$LOCAL_PROJECT_DIR" ]] || die "LOCAL_PROJECT_DIR does not exist: $LOCAL_PROJECT_DIR"
    [[ -d "$LOCAL_FRONTEND_DIR" ]] || die "LOCAL_FRONTEND_DIR does not exist: $LOCAL_FRONTEND_DIR"

    IRAN_SSH_TARGET="$IRAN_SSH_USER@$IRAN_HOST"
    case "$IRAN_SSH_AUTH_METHOD" in
        key)
            if [[ -n "${IRAN_SSH_PRIVATE_KEY_PATH:-}" ]]; then
                [[ -f "$IRAN_SSH_PRIVATE_KEY_PATH" ]] || die "IRAN_SSH_PRIVATE_KEY_PATH does not exist: $IRAN_SSH_PRIVATE_KEY_PATH"
                SSH_IRAN_CMD=(ssh -p "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no -i "$IRAN_SSH_PRIVATE_KEY_PATH")
                SCP_IRAN_CMD=(scp -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no -i "$IRAN_SSH_PRIVATE_KEY_PATH")
            else
                log "IRAN_SSH_PRIVATE_KEY_PATH is empty; using SSH agent/default keys for Iran access."
                SSH_IRAN_CMD=(ssh -p "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no)
                SCP_IRAN_CMD=(scp -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no)
            fi
            ;;
        password)
            : "${IRAN_SSH_PASSWORD:?IRAN_SSH_PASSWORD is required for password auth}"
            need_cmd sshpass
            SSH_IRAN_CMD=(sshpass -p "$IRAN_SSH_PASSWORD" ssh -p "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no)
            SCP_IRAN_CMD=(sshpass -p "$IRAN_SSH_PASSWORD" scp -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no)
            ;;
        *)
            die "Unsupported IRAN_SSH_AUTH_METHOD: $IRAN_SSH_AUTH_METHOD"
            ;;
    esac
    RSYNC_SSH="ssh -p $IRAN_SSH_PORT -o StrictHostKeyChecking=no"
    RELEASE_TMP_DIR="$LOCAL_PROJECT_DIR/tmp/production-release"
    RELEASE_ARTIFACT_DIR="$RELEASE_TMP_DIR/artifacts"
    REMOTE_IMAGE_BUNDLE="$IRAN_DEPLOY_BASE_DIR/releases/trading-bot-images.tar"
    REMOTE_IMAGE_BUNDLE_SHA="$REMOTE_IMAGE_BUNDLE.sha256"
    REMOTE_RELEASE_STATE_DIR="$IRAN_DEPLOY_BASE_DIR/releases/state"
    REMOTE_IMAGE_LOADED_SIGNATURE="$REMOTE_RELEASE_STATE_DIR/docker-images.loaded.signature"
    LOCAL_IMAGE_BUNDLE="$RELEASE_TMP_DIR/docker-images.tar"
    LOCAL_IMAGE_SIGNATURE_FILE="$RELEASE_TMP_DIR/docker-images.signature"
    LOCAL_FRONTEND_SIGNATURE_FILE="$RELEASE_TMP_DIR/frontend-build.signature"
}

ssh_iran() {
    "${SSH_IRAN_CMD[@]}" "$IRAN_SSH_TARGET" "$@"
}

scp_iran() {
    "${SCP_IRAN_CMD[@]}" "$@"
}

read_env_value() {
    local env_path="$1"
    local key="$2"
    local line
    line="$(grep -E "^${key}=" "$env_path" | tail -n 1 || true)"
    printf '%s' "${line#*=}"
}

file_sha256() {
    sha256sum "$1" | awk '{print $1}'
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

    local timestamp safe_name backup_path
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    safe_name="$(printf '%s' "$env_path" | sed 's#^/##; s#[^A-Za-z0-9._-]#_#g')"
    mkdir -p "$ENV_BACKUP_DIR"
    backup_path="$ENV_BACKUP_DIR/${safe_name}.${timestamp}.bak"
    cp -p "$env_path" "$backup_path"
    chmod 600 "$backup_path" || true
    log "Backed up $role_label runtime env to $backup_path"
}

validate_runtime_env_source_policy() {
    local project_env_path="$LOCAL_PROJECT_DIR/.env"
    local local_source project_env iran_output
    local_source="$(canonical_path "$LOCAL_ENV_SOURCE_PATH")"
    project_env="$(canonical_path "$project_env_path")"
    iran_output="$(canonical_path "$IRAN_ENV_SOURCE_PATH")"

    if [[ "$local_source" == "$project_env" && "$ALLOW_PROJECT_ENV_SOURCE" != "1" ]]; then
        die "LOCAL_ENV_SOURCE_PATH points at the project .env ($project_env_path). Use a secure source env outside the repo, or set ALLOW_PROJECT_ENV_SOURCE=1 only for an intentional emergency release."
    fi

    if [[ "$local_source" == "$iran_output" ]]; then
        die "LOCAL_ENV_SOURCE_PATH and IRAN_ENV_SOURCE_PATH must be different files so foreign and Iran runtime envs can be rendered independently."
    fi
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
    validate_observability_env_file "$LOCAL_ENV_SOURCE_PATH" "Foreign"
    validate_observability_env_file "$IRAN_ENV_SOURCE_PATH" "Iran"
    summarize_web_push_env_file "$LOCAL_ENV_SOURCE_PATH" "Foreign"
    validate_web_push_env_file "$LOCAL_ENV_SOURCE_PATH" "Foreign"
    summarize_web_push_env_file "$IRAN_ENV_SOURCE_PATH" "Iran"
    validate_web_push_env_file "$IRAN_ENV_SOURCE_PATH" "Iran"
}

install_sync_sampler_local() {
    log "Ensuring foreign sync health sampler is installed"
    (cd "$LOCAL_PROJECT_DIR" && bash ./scripts/install_sync_health_monitor.sh)
}

install_sync_sampler_remote() {
    log "Ensuring Iran sync health sampler is installed"
    ssh_iran "set -euo pipefail
cd '$IRAN_PROJECT_DIR'
bash ./scripts/install_sync_health_monitor.sh"
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

    local branch head_sha upstream upstream_sha
    branch="$(git -C "$LOCAL_PROJECT_DIR" symbolic-ref --short HEAD 2>/dev/null || true)"
    head_sha="$(git -C "$LOCAL_PROJECT_DIR" rev-parse --short HEAD)"

    if [[ "$IRAN_ALLOW_NON_MAIN_RELEASE" == "1" ]]; then
        log "IRAN_ALLOW_NON_MAIN_RELEASE=1; allowing production release from branch ${branch:-detached} at $head_sha."
    elif [[ "$branch" != "$PRODUCTION_RELEASE_BRANCH" ]]; then
        die "Production release must run from '$PRODUCTION_RELEASE_BRANCH' (current: ${branch:-detached}, sha: $head_sha). Merge the intended candidate/hotfix to $PRODUCTION_RELEASE_BRANCH first, or set IRAN_ALLOW_NON_MAIN_RELEASE=1 for an explicit emergency override."
    fi

    upstream="$(git -C "$LOCAL_PROJECT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    if [[ -z "$upstream" ]]; then
        log "No upstream configured for branch ${branch:-detached}; skipping upstream equality check."
        return 0
    fi

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
    log "Checking local prerequisites"
    ensure_local_runtime_packages
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
    validate_runtime_env_source_policy
    ensure_runtime_env_file
    render_release_artifacts
    validate_observability_release_inputs
    log "Local checks passed"
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
    bundle_signature="$(printf '%s\n%s\n%s\n' "$IRAN_OS_CODENAME" "$IRAN_IMAGE_PLATFORM" "$IRAN_BOOTSTRAP_APT_PACKAGES" | sha256sum | cut -d' ' -f1)"

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
apt-get -o Acquire::Retries=5 -y --download-only -o Dir::Cache::archives=/bundle install $IRAN_BOOTSTRAP_APT_PACKAGES"
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
apt-get -o Acquire::Retries=5 install -y --fix-missing $IRAN_BOOTSTRAP_APT_PACKAGES
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
        printf 'signature_scope=%s\n' "iran-base-image-v2"
        printf 'iran_image_platform=%s\n' "$IRAN_IMAGE_PLATFORM"
        printf 'iran_host_arch=%s\n' "$IRAN_HOST_ARCH"
        printf 'python_base_image=%s\n' "python:3.11-slim-bullseye"
        printf 'postgres_image=%s\n' "postgres:15-alpine"
        printf 'redis_image=%s\n' "redis:7-alpine"
        # Iran compose bind-mounts runtime code. Refresh the heavy image bundle
        # only when base-image dependencies or non-mounted image assets change.
        for rel_path in \
            Dockerfile.iran \
            .dockerignore \
            requirements.txt \
            deploy/production/pip-bootstrap-requirements.txt \
            pip_packages \
            fonts \
            templates
        do
            hash_context_entry "$context_dir" "$rel_path"
        done
    } | sha256sum | cut -d' ' -f1
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
        --exclude 'tests' \
        --exclude 'tmp' \
        --exclude 'uploads' \
        --exclude 'map_data' \
        --exclude 'pip_packages' \
        "$LOCAL_PROJECT_DIR/" "$iran_context_dir/"
    rsync -a --delete "$iran_pip_dir/" "$iran_context_dir/pip_packages/"

    local image_signature
    image_signature="$(build_image_bundle_signature "$iran_context_dir")"
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && -s "$LOCAL_IMAGE_BUNDLE" && -f "$LOCAL_IMAGE_SIGNATURE_FILE" && "$(cat "$LOCAL_IMAGE_SIGNATURE_FILE")" == "$image_signature" ]]; then
        log "Docker image bundle already matches current build inputs; skipping image build/save."
        return 0
    fi

    log "Building Docker images for Iran platform=$IRAN_IMAGE_PLATFORM"
    ensure_buildx_for_target
    if [[ "$LOCAL_HOST_ARCH" == "$IRAN_HOST_ARCH" ]]; then
        docker pull "postgres:15-alpine" >/dev/null
        docker pull "redis:7-alpine" >/dev/null
        docker build -f "$iran_context_dir/Dockerfile.iran" -t trading_bot_base_iran "$iran_context_dir"
        docker save trading_bot_base_iran postgres:15-alpine redis:7-alpine -o "$LOCAL_IMAGE_BUNDLE"
    else
        docker pull --platform "$IRAN_IMAGE_PLATFORM" postgres:15-alpine >/dev/null
        docker tag postgres:15-alpine "postgres:15-alpine-iran-$IRAN_HOST_ARCH"
        docker pull --platform "$IRAN_IMAGE_PLATFORM" redis:7-alpine >/dev/null
        docker tag redis:7-alpine "redis:7-alpine-iran-$IRAN_HOST_ARCH"
        docker buildx build --platform "$IRAN_IMAGE_PLATFORM" -f "$iran_context_dir/Dockerfile.iran" -t trading_bot_base_iran --output "type=docker,dest=$RELEASE_TMP_DIR/trading_bot_base_iran.tar" "$iran_context_dir"
        docker load -i "$RELEASE_TMP_DIR/trading_bot_base_iran.tar" >/dev/null
        docker save trading_bot_base_iran "postgres:15-alpine-iran-$IRAN_HOST_ARCH" "redis:7-alpine-iran-$IRAN_HOST_ARCH" -o "$LOCAL_IMAGE_BUNDLE"
    fi
    printf '%s\n' "$image_signature" > "$LOCAL_IMAGE_SIGNATURE_FILE"
    log "Local release build complete"
}

ensure_runtime_env_file() {
    local local_env_path="$LOCAL_ENV_SOURCE_PATH"
    local source_env_path=""

    if [[ -f "$local_env_path" ]]; then
        source_env_path="$local_env_path"
    elif [[ -f "$IRAN_ENV_SOURCE_PATH" ]]; then
        source_env_path="$IRAN_ENV_SOURCE_PATH"
    fi

    if [[ -n "$source_env_path" ]]; then
        mkdir -p "$(dirname "$IRAN_ENV_SOURCE_PATH")" "$(dirname "$local_env_path")"
        backup_runtime_env_file "$local_env_path" "foreign"
        backup_runtime_env_file "$IRAN_ENV_SOURCE_PATH" "Iran"
        export_runtime_renderer_overrides
        python3 "$RUNTIME_ENV_RENDERER" \
            --source-env-file "$source_env_path" \
            --local-output "$local_env_path" \
            --iran-output "$IRAN_ENV_SOURCE_PATH" \
            --foreign-frontend-url "$FOREIGN_FRONTEND_URL" \
            --iran-frontend-url "$IRAN_FRONTEND_URL" \
            --foreign-server-url "$FOREIGN_SERVER_URL" \
            --foreign-server-domain "$FOREIGN_SERVER_DOMAIN" \
            --iran-server-url "$IRAN_SERVER_URL" \
            --iran-server-domain "$IRAN_SERVER_DOMAIN" \
            --foreign-api-workers "${FOREIGN_API_WORKERS:-2}" \
            --iran-api-workers "${IRAN_API_WORKERS:-8}"
        chmod 600 "$local_env_path" || true
        chmod 600 "$IRAN_ENV_SOURCE_PATH" || true
        log "Rendered runtime env files from source env: $source_env_path"
        summarize_web_push_env_file "$local_env_path" "Foreign"
        summarize_web_push_env_file "$IRAN_ENV_SOURCE_PATH" "Iran"
        return 0
    fi

    local bot_token=""
    local bot_username=""
    local database_url=""
    local sync_database_url=""
    local postgres_db=""
    local postgres_user=""
    local postgres_password=""
    local redis_url=""
    local jwt_secret_key=""
    local dev_api_key=""
    local sync_api_key=""
    local observability_api_key=""
    local channel_id=""
    local channel_invite_link=""
    local smsir_api_key=""
    local smsir_line_number=""
    local smsir_otp_template_id="585147"
    local smsir_otp_template_parameter="CODE"
    local smsir_invitation_template_id="657938"
    local smsir_invitation_template_parameter="NAME"
    local smsir_accountant_invitation_template_id="162103"
    local smsir_customer_invitation_template_id="903643"
    local error_tracking_dsn=""
    local trusted_proxy_cidrs="127.0.0.1/32,::1/128"
    local observability_telegram_user_hash_salt=""
    local grafana_alert_default_receiver="Trading Bot Production Webhook"
    local grafana_alert_critical_receiver="Trading Bot Production Webhook"
    local grafana_alert_warning_receiver="Trading Bot Production Email"
    local grafana_alert_webhook_url=""
    local grafana_alert_email_addresses=""
    local sync_verify_tls="true"
    local sync_ca_bundle=""
    local web_push_enabled="false"
    local web_push_vapid_public_key=""
    local web_push_vapid_private_key=""
    local web_push_vapid_subject=""
    local web_push_ttl_seconds="3600"
    local web_push_timeout_seconds="5.0"

    if is_truthy "$REQUIRE_WEB_PUSH"; then
        web_push_enabled="true"
    fi

    mkdir -p "$(dirname "$IRAN_ENV_SOURCE_PATH")" "$(dirname "$local_env_path")"

    prompt_value bot_token "BOT_TOKEN" "" 1
    prompt_value bot_username "BOT_USERNAME"
    prompt_value database_url "DATABASE_URL"
    prompt_value sync_database_url "SYNC_DATABASE_URL"
    prompt_value postgres_db "POSTGRES_DB"
    prompt_value postgres_user "POSTGRES_USER"
    prompt_value postgres_password "POSTGRES_PASSWORD" "" 1
    prompt_value redis_url "REDIS_URL"
    prompt_value jwt_secret_key "JWT_SECRET_KEY" "" 1
    prompt_value dev_api_key "DEV_API_KEY" "" 1
    prompt_value sync_api_key "SYNC_API_KEY" "" 1
    prompt_value sync_verify_tls "SYNC_VERIFY_TLS" "$sync_verify_tls"
    prompt_value sync_ca_bundle "SYNC_CA_BUNDLE" "$sync_ca_bundle"
    prompt_value observability_api_key "OBSERVABILITY_API_KEY" "" 1
    prompt_value channel_id "CHANNEL_ID"
    prompt_value channel_invite_link "CHANNEL_INVITE_LINK"
    prompt_value smsir_api_key "SMSIR_API_KEY" "" 1
    prompt_value smsir_line_number "SMSIR_LINE_NUMBER"
    prompt_value smsir_otp_template_id "SMSIR_OTP_TEMPLATE_ID"
    prompt_value smsir_otp_template_parameter "SMSIR_OTP_TEMPLATE_PARAMETER" "$smsir_otp_template_parameter"
    prompt_value smsir_invitation_template_id "SMSIR_INVITATION_TEMPLATE_ID" "$smsir_invitation_template_id"
    prompt_value smsir_invitation_template_parameter "SMSIR_INVITATION_TEMPLATE_PARAMETER" "$smsir_invitation_template_parameter"
    prompt_value smsir_accountant_invitation_template_id "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID" "$smsir_accountant_invitation_template_id"
    prompt_value smsir_customer_invitation_template_id "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID" "$smsir_customer_invitation_template_id"
    prompt_value error_tracking_dsn "ERROR_TRACKING_DSN"
    prompt_value trusted_proxy_cidrs "TRUSTED_PROXY_CIDRS" "$trusted_proxy_cidrs"
    prompt_value observability_telegram_user_hash_salt "OBSERVABILITY_TELEGRAM_USER_HASH_SALT" "" 1
    prompt_value grafana_alert_default_receiver "GRAFANA_ALERT_DEFAULT_RECEIVER" "$grafana_alert_default_receiver"
    prompt_value grafana_alert_critical_receiver "GRAFANA_ALERT_CRITICAL_RECEIVER" "$grafana_alert_critical_receiver"
    prompt_value grafana_alert_warning_receiver "GRAFANA_ALERT_WARNING_RECEIVER" "$grafana_alert_warning_receiver"
    prompt_value grafana_alert_webhook_url "GRAFANA_ALERT_WEBHOOK_URL"
    prompt_value grafana_alert_email_addresses "GRAFANA_ALERT_EMAIL_ADDRESSES"
    prompt_value web_push_enabled "WEB_PUSH_ENABLED" "$web_push_enabled"
    if is_truthy "$web_push_enabled"; then
        prompt_value web_push_vapid_public_key "WEB_PUSH_VAPID_PUBLIC_KEY"
        prompt_value web_push_vapid_private_key "WEB_PUSH_VAPID_PRIVATE_KEY" "" 1
        prompt_value web_push_vapid_subject "WEB_PUSH_VAPID_SUBJECT"
        prompt_value web_push_ttl_seconds "WEB_PUSH_TTL_SECONDS" "$web_push_ttl_seconds"
        prompt_value web_push_timeout_seconds "WEB_PUSH_TIMEOUT_SECONDS" "$web_push_timeout_seconds"
    fi
    if ! is_truthy "$sync_verify_tls" && [[ -z "$sync_ca_bundle" ]]; then
        die "SYNC_VERIFY_TLS=false is not allowed for production sync transport without SYNC_CA_BUNDLE"
    fi

    BOT_TOKEN="$bot_token" \
    BOT_USERNAME="$bot_username" \
    DATABASE_URL="$database_url" \
    SYNC_DATABASE_URL="$sync_database_url" \
    POSTGRES_DB="$postgres_db" \
    POSTGRES_USER="$postgres_user" \
    POSTGRES_PASSWORD="$postgres_password" \
    REDIS_URL="$redis_url" \
    JWT_SECRET_KEY="$jwt_secret_key" \
    DEV_API_KEY="$dev_api_key" \
    SYNC_API_KEY="$sync_api_key" \
    SYNC_VERIFY_TLS="$sync_verify_tls" \
    SYNC_CA_BUNDLE="$sync_ca_bundle" \
    OBSERVABILITY_API_KEY="$observability_api_key" \
    CHANNEL_ID="$channel_id" \
    CHANNEL_INVITE_LINK="$channel_invite_link" \
    SMSIR_API_KEY="$smsir_api_key" \
    SMSIR_LINE_NUMBER="$smsir_line_number" \
    SMSIR_OTP_TEMPLATE_ID="$smsir_otp_template_id" \
    SMSIR_OTP_TEMPLATE_PARAMETER="$smsir_otp_template_parameter" \
    SMSIR_INVITATION_TEMPLATE_ID="$smsir_invitation_template_id" \
    SMSIR_INVITATION_TEMPLATE_PARAMETER="$smsir_invitation_template_parameter" \
    SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID="$smsir_accountant_invitation_template_id" \
    SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID="$smsir_customer_invitation_template_id" \
    ERROR_TRACKING_DSN="$error_tracking_dsn" \
    TRUSTED_PROXY_CIDRS="$trusted_proxy_cidrs" \
    OBSERVABILITY_TELEGRAM_USER_HASH_SALT="$observability_telegram_user_hash_salt" \
    GRAFANA_ALERT_DEFAULT_RECEIVER="$grafana_alert_default_receiver" \
    GRAFANA_ALERT_CRITICAL_RECEIVER="$grafana_alert_critical_receiver" \
    GRAFANA_ALERT_WARNING_RECEIVER="$grafana_alert_warning_receiver" \
    GRAFANA_ALERT_WEBHOOK_URL="$grafana_alert_webhook_url" \
    GRAFANA_ALERT_EMAIL_ADDRESSES="$grafana_alert_email_addresses" \
    WEB_PUSH_ENABLED="$web_push_enabled" \
    WEB_PUSH_VAPID_PUBLIC_KEY="$web_push_vapid_public_key" \
    WEB_PUSH_VAPID_PRIVATE_KEY="$web_push_vapid_private_key" \
    WEB_PUSH_VAPID_SUBJECT="$web_push_vapid_subject" \
    WEB_PUSH_TTL_SECONDS="$web_push_ttl_seconds" \
    WEB_PUSH_TIMEOUT_SECONDS="$web_push_timeout_seconds" \
    python3 "$RUNTIME_ENV_RENDERER" \
        --local-output "$local_env_path" \
        --iran-output "$IRAN_ENV_SOURCE_PATH" \
        --foreign-frontend-url "$FOREIGN_FRONTEND_URL" \
        --iran-frontend-url "$IRAN_FRONTEND_URL" \
        --foreign-server-url "$FOREIGN_SERVER_URL" \
        --foreign-server-domain "$FOREIGN_SERVER_DOMAIN" \
        --iran-server-url "$IRAN_SERVER_URL" \
        --iran-server-domain "$IRAN_SERVER_DOMAIN" \
        --foreign-api-workers "${FOREIGN_API_WORKERS:-2}" \
        --iran-api-workers "${IRAN_API_WORKERS:-8}"

    chmod 600 "$local_env_path" || true
    chmod 600 "$IRAN_ENV_SOURCE_PATH" || true
    log "Created local env at $local_env_path"
    log "Created Iran runtime env at $IRAN_ENV_SOURCE_PATH"
}

install_foreign_runtime_env() {
    local project_env_path="$LOCAL_PROJECT_DIR/.env"
    if [[ "$LOCAL_ENV_SOURCE_PATH" == "$project_env_path" ]]; then
        return 0
    fi
    cp "$LOCAL_ENV_SOURCE_PATH" "$project_env_path"
    chmod 600 "$project_env_path" || true
    log "Installed rendered foreign runtime env at $project_env_path"
}

deploy_foreign() {
    if [[ "$IRAN_SKIP_FOREIGN_DEPLOY" == "1" ]]; then
        log "Skipping foreign deploy because IRAN_SKIP_FOREIGN_DEPLOY=1"
        return 0
    fi
    log "Deploying the foreign server locally"
    ensure_runtime_env_file
    install_foreign_runtime_env
    (cd "$LOCAL_PROJECT_DIR" && bash ./deploy.sh foreign)
}

sync_project() {
    log "Syncing production payload to the Iran host"
    ensure_runtime_env_file
    local staging_dir="$IRAN_PROJECT_DIR"
    ssh_iran "mkdir -p '$IRAN_DEPLOY_BASE_DIR' '$IRAN_DEPLOY_BASE_DIR/releases' '$REMOTE_RELEASE_STATE_DIR' '$staging_dir'"
    rsync -avz --delete \
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
        -e "$RSYNC_SSH" \
        "$LOCAL_PROJECT_DIR/" "$IRAN_SSH_TARGET:$staging_dir/"
    local local_pip_hash_file="$LOCAL_PROJECT_DIR/pip_packages/.requirements_hash"
    local remote_pip_hash=""
    if [[ -f "$local_pip_hash_file" ]]; then
        remote_pip_hash="$(ssh_iran "cat '$staging_dir/pip_packages/.requirements_hash' 2>/dev/null || true")"
    fi
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && -f "$local_pip_hash_file" && "$remote_pip_hash" == "$(cat "$local_pip_hash_file")" ]]; then
        log "Remote pip wheelhouse already matches requirements; skipping pip package sync."
    else
        rsync -avz --delete -e "$RSYNC_SSH" \
            "$LOCAL_PROJECT_DIR/pip_packages/" "$IRAN_SSH_TARGET:$staging_dir/pip_packages/"
    fi
    rsync -avz --delete -e "$RSYNC_SSH" \
        "$LOCAL_DIST_DIR/" "$IRAN_SSH_TARGET:$staging_dir/mini_app_dist/"
    ssh_iran "set -euo pipefail
assets_dir='$staging_dir/mini_app_dist/assets'
find \"\$assets_dir\" -maxdepth 1 -type f -name 'MarketView-*.js' | grep -q . || exit 21
grep -h -q 'api/offers/market-history' \"\$assets_dir\"/MarketView-*.js || exit 22" \
        || die "Remote Iran frontend release contract failed: deployed MarketView bundle cannot load read-only terminal market offers."
    scp_iran "$IRAN_ENV_SOURCE_PATH" "$IRAN_SSH_TARGET:$staging_dir/.env"
    log "Production payload sync complete"
}

ship_images() {
    local bundle="$LOCAL_IMAGE_BUNDLE"
    [[ -f "$bundle" ]] || die "Docker image bundle missing: $bundle"
    local bundle_sha remote_bundle_sha
    bundle_sha="$(file_sha256 "$bundle")"
    remote_bundle_sha="$(ssh_iran "cat '$REMOTE_IMAGE_BUNDLE_SHA' 2>/dev/null || true")"
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && "$remote_bundle_sha" == "$bundle_sha" ]] && ssh_iran "[ -s '$REMOTE_IMAGE_BUNDLE' ]"; then
        log "Docker image bundle already exists on Iran with matching checksum; skipping upload."
        return 0
    fi
    log "Uploading Docker image bundle to the Iran host"
    ssh_iran "mkdir -p '$IRAN_DEPLOY_BASE_DIR/releases' '$REMOTE_RELEASE_STATE_DIR'"
    scp_iran "$bundle" "$IRAN_SSH_TARGET:$REMOTE_IMAGE_BUNDLE"
    ssh_iran "printf '%s\n' '$bundle_sha' > '$REMOTE_IMAGE_BUNDLE_SHA'"
    log "Docker image bundle upload complete"
}

load_images() {
    local bundle="$LOCAL_IMAGE_BUNDLE"
    [[ -f "$bundle" ]] || die "Docker image bundle missing: $bundle"
    local image_signature remote_loaded_signature
    if [[ -f "$LOCAL_IMAGE_SIGNATURE_FILE" ]]; then
        image_signature="$(cat "$LOCAL_IMAGE_SIGNATURE_FILE")"
    else
        image_signature="$(file_sha256 "$bundle")"
    fi
    remote_loaded_signature="$(ssh_iran "cat '$REMOTE_IMAGE_LOADED_SIGNATURE' 2>/dev/null || true")"
    if [[ "$IRAN_FORCE_RELEASE_REFRESH" != "1" && "$remote_loaded_signature" == "$image_signature" ]]; then
        if ssh_iran "docker image inspect trading_bot_base_iran:latest >/dev/null 2>&1 && docker image inspect postgres:15-alpine >/dev/null 2>&1 && docker image inspect redis:7-alpine >/dev/null 2>&1"; then
            log "Docker images already loaded on Iran with matching signature; skipping docker load."
            return 0
        fi
        log "Docker image load signature matched but one or more images are missing; reloading bundle."
    fi
    log "Loading transferred Docker images on the Iran host"
    ssh_iran "set -euo pipefail
mkdir -p '$REMOTE_RELEASE_STATE_DIR'
docker load -i '$REMOTE_IMAGE_BUNDLE'
if docker image inspect 'postgres:15-alpine-iran-$IRAN_HOST_ARCH' >/dev/null 2>&1; then
  docker tag 'postgres:15-alpine-iran-$IRAN_HOST_ARCH' 'postgres:15-alpine'
fi
if docker image inspect 'redis:7-alpine-iran-$IRAN_HOST_ARCH' >/dev/null 2>&1; then
  docker tag 'redis:7-alpine-iran-$IRAN_HOST_ARCH' 'redis:7-alpine'
fi
printf '%s\n' '$image_signature' > '$REMOTE_IMAGE_LOADED_SIGNATURE'"
    log "Docker images loaded on the Iran host"
}

deploy_iran() {
    log "Deploying Docker services on the Iran host"
    local compose_resolver
    compose_resolver="$(remote_compose_resolver)"
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
eval \"\$compose_cmd -f docker-compose.iran.yml up -d --no-deps \$wait_args app sync_worker\"
eval \"\$compose_cmd -f docker-compose.iran.yml ps\""
    log "Iran deploy step complete"
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
printf '%s\n' \"\$nginx_dump\" | grep -Eq 'listen[[:space:]]+443([^0-9]|$)' || {
  echo 'Iran active Nginx config has no listen 443 server block.' >&2
  exit 22
}
printf '%s\n' \"\$nginx_dump\" | grep -q 'ssl_certificate ' || {
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
    confirm_iran_shared_reset
    backup_iran_database_before_shared_reset
    log "Resetting Iran shared tables"
ssh_iran "set -euo pipefail
$(remote_compose_resolver)
cd '$IRAN_PROJECT_DIR'
\$compose_cmd -f docker-compose.iran.yml exec -T db sh -lc 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At' <<'SQL'
TRUNCATE TABLE change_log, $SHARED_SYNC_TABLES_SQL RESTART IDENTITY CASCADE;
SQL"
    log "Iran shared-table reset completed"
}

mark_foreign_preseed_backlog_synced() {
    local cutoff="$1"
    local query="
UPDATE change_log
SET synced = true
WHERE synced = false
  AND table_name IN ('users', 'accountant_relations', 'customer_relations', 'telegram_link_tokens', 'invitations', 'admin_market_messages', 'admin_broadcast_messages', 'notifications', 'user_blocks', 'commodities', 'commodity_aliases', 'trading_settings', 'market_schedule_overrides', 'market_runtime_state', 'offers', 'offer_publication_states', 'offer_requests', 'trades', 'trade_delivery_receipts')
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
    foreign_observability_key="$(read_env_value "$LOCAL_ENV_SOURCE_PATH" "OBSERVABILITY_API_KEY")"
    iran_observability_key="$(read_env_value "$IRAN_ENV_SOURCE_PATH" "OBSERVABILITY_API_KEY")"
    [[ -n "$foreign_observability_key" ]] || die "OBSERVABILITY_API_KEY is missing from $LOCAL_ENV_SOURCE_PATH"
    [[ -n "$iran_observability_key" ]] || die "OBSERVABILITY_API_KEY is missing from $IRAN_ENV_SOURCE_PATH"

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
    log "Health checks passed"
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
    echo "Foreign deploy finished."
    read -r -p "Does the Iran server currently have working internet access? [yes/no]: " answer
    answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
    case "$answer" in
        yes|y) printf 'online\n' ;;
        no|n) printf 'offline\n' ;;
        *) die "Please answer yes or no." ;;
    esac
}

run_release() {
    check_local
    ensure_local_timezone_utc
    install_sync_sampler_local
    deploy_foreign
    verify_sync_sampler_local
    sync_hosts_mappings
    local iran_mode
    iran_mode="$(decide_iran_connectivity)"
    if [[ "$iran_mode" == "offline" ]]; then
        log "Iran offline scenario is not implemented yet. Stopping after the foreign deploy."
        exit 20
    fi
    build_release
    bootstrap_iran
    sync_project
    install_sync_sampler_remote
    configure_nginx
    issue_cert
    ship_images
    load_images
    deploy_iran
    handle_iran_shared_data
    healthcheck
}

main() {
    parse_args "$@"
    if [[ "$COMMAND" == "help" ]]; then
        usage
        exit 0
    fi
    ensure_manifest_file
    load_manifest
    case "$COMMAND" in
        check-local) check_local ;;
        release) run_release ;;
        deploy-foreign) check_local; install_sync_sampler_local; build_release; deploy_foreign; verify_sync_sampler_local ;;
        bootstrap-iran) check_local; bootstrap_iran; install_sync_sampler_remote; verify_sync_sampler_remote ;;
        configure-nginx) check_local; configure_nginx ;;
        issue-cert) check_local; issue_cert ;;
        build-release) check_local; build_release ;;
        sync-project) check_local; sync_project ;;
        ship-images) check_local; ship_images ;;
        load-images) check_local; load_images ;;
        deploy-iran) check_local; install_sync_sampler_remote; deploy_iran; verify_sync_sampler_remote ;;
        inspect-shared-data) check_local; inspect_iran_shared_data ;;
        seed-shared-data) check_local; handle_iran_shared_data ;;
        healthcheck) check_local; healthcheck ;;
        *) die "Unknown command: $COMMAND" ;;
    esac
}

main "$@"
