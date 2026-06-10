#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_ENV_RENDERER="$PROJECT_DIR/scripts/render_runtime_envs.py"
DEFAULT_MANIFEST="$PROJECT_DIR/deploy/production/online.env"
MANIFEST_PATH="${DEPLOY_MANIFEST:-$DEFAULT_MANIFEST}"
COMMAND=""
IRAN_BOOTSTRAP_APT_PACKAGES="ca-certificates curl gnupg lsb-release rsync jq pigz nginx certbot python3-certbot-nginx docker.io docker-compose python3-pip python3-setuptools python3-wheel"
LOCAL_HOST_ARCH=""
LOCAL_DPKG_ARCH=""
IRAN_HOST_ARCH=""
IRAN_DPKG_ARCH=""
IRAN_OS_CODENAME=""
IRAN_IMAGE_PLATFORM=""
LOCAL_COMPOSE_CMD=""
IRAN_COMPOSE_CMD=""
IRAN_APT_BUNDLE_MODE="same-arch"

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
    if [[ "$LOCAL_DPKG_ARCH" != "$IRAN_DPKG_ARCH" ]]; then
        IRAN_APT_BUNDLE_MODE="remote-install"
    else
        IRAN_APT_BUNDLE_MODE="same-arch"
    fi

    log "Foreign arch=$LOCAL_HOST_ARCH dpkg=$LOCAL_DPKG_ARCH compose='$LOCAL_COMPOSE_CMD'"
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

remote_docker_cleanup_guard() {
    cat <<'EOF'
docker_cleanup_packages=""
for pkg in containerd.io docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin docker.io containerd runc; do
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
    local iran_env_source_path="$PROJECT_DIR/deploy/production/iran.runtime.env"
    local local_env_path="$PROJECT_DIR/.env"
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

# --- Optional runtime toggles ---
IRAN_SKIP_CERTBOT=0
IRAN_SKIP_FRONTEND_BUILD=0
IRAN_DEPLOY_WITH_WAIT=1
IRAN_RUN_POST_DEPLOY_HEALTHCHECK=1
IRAN_ENABLE_UFW=0
IRAN_CONNECTIVITY_MODE=ask
IRAN_SKIP_FOREIGN_DEPLOY=0
IRAN_HOSTS_SYNC_ENABLED=1

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
    REMOTE_IMAGE_BUNDLE="$IRAN_DEPLOY_BASE_DIR/releases/trading-bot-images.tar"
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

require_env_value() {
    local env_path="$1"
    local key="$2"
    local value
    value="$(read_env_value "$env_path" "$key")"
    [[ -n "$value" ]] || die "Missing required env value '$key' in $env_path"
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
    need_cmd sed
}

ensure_local_runtime_packages() {
    local missing_packages=()
    local need_docker=0
    local need_npm=0
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

    if ! command -v npm >/dev/null 2>&1; then
        need_npm=1
        missing_packages+=(nodejs npm)
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

    need_cmd docker
    need_cmd npm
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
    ssh_iran "echo connected-to-\$(hostname)"
    detect_runtime_metadata
    [[ -f "$LOCAL_PROJECT_DIR/requirements.txt" ]] || die "requirements.txt missing"
    [[ -f "$LOCAL_PROJECT_DIR/docker-compose.iran.yml" ]] || die "docker-compose.iran.yml missing"
    [[ -f "$LOCAL_PROJECT_DIR/Dockerfile.iran" ]] || die "Dockerfile.iran missing"
    [[ -f "$PROJECT_DIR/deploy/production/nginx-iran-online.conf.template" ]] || die "Nginx template missing"
    ensure_runtime_env_file
    validate_observability_release_inputs
    log "Local checks passed"
}

prepare_iran_package_bundle() {
    if [[ "$IRAN_APT_BUNDLE_MODE" != "same-arch" ]]; then
        log "Skipping foreign-built Iran apt bundle because architectures differ (foreign=$LOCAL_DPKG_ARCH iran=$IRAN_DPKG_ARCH)."
        return 0
    fi

    local bundle_dir="$RELEASE_TMP_DIR/iran-packages"
    local bundle_tar="$RELEASE_TMP_DIR/iran-packages.tar.gz"
    local bundle_hash_file="$RELEASE_TMP_DIR/iran-packages.sha256"
    local bundle_signature
    bundle_signature="$(printf '%s\n' "$IRAN_BOOTSTRAP_APT_PACKAGES" | sha256sum | cut -d' ' -f1)"

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
    local post_bootstrap_guard
    post_bootstrap_guard="$(remote_post_bootstrap_guard)"
    local docker_cleanup_guard
    docker_cleanup_guard="$(remote_docker_cleanup_guard)"
    ssh_iran "mkdir -p '$IRAN_DEPLOY_BASE_DIR' '$IRAN_DEPLOY_BASE_DIR/releases' '$IRAN_PROJECT_DIR'"
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
systemctl enable --now docker
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
systemctl enable --now docker
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

render_nginx_config() {
    local template="$PROJECT_DIR/deploy/production/nginx-iran-online.conf.template"
    local output="$PROJECT_DIR/tmp/iran-online-nginx.conf"
    sed \
        -e "s#__SERVER_NAME__#$IRAN_APP_DOMAIN#g" \
        -e "s#__APP_ROOT__#$IRAN_PROJECT_DIR/mini_app_dist#g" \
        "$template" > "$output"
    printf '%s\n' "$output"
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

issue_cert() {
    if [[ "$IRAN_SKIP_CERTBOT" == "1" ]]; then
        log "Skipping certbot because IRAN_SKIP_CERTBOT=1"
        return 0
    fi
    log "Requesting/renewing SSL certificate on the Iran host"
    local cert_renewal_guard
    cert_renewal_guard="$(remote_cert_renewal_guard)"
    ssh_iran "set -euo pipefail
certbot --nginx -d '$IRAN_APP_DOMAIN' --non-interactive --agree-tos --email '$IRAN_CERTBOT_EMAIL' --redirect
$cert_renewal_guard"
    log "SSL certificate step completed"
}

hosts_block() {
    cat <<EOF
# trading-bot-production-hosts START
$FOREIGN_PUBLIC_IP $FOREIGN_PUBLIC_DOMAIN
$IRAN_PUBLIC_IP $IRAN_PUBLIC_DOMAIN
# trading-bot-production-hosts END
EOF
}

replace_hosts_block_local() {
    local hosts_file="/etc/hosts"
    local block
    block="$(hosts_block)"
    local tmp
    tmp="$(mktemp)"
    if grep -qF "# trading-bot-production-hosts START" "$hosts_file"; then
        sed '/^# trading-bot-production-hosts START$/,/^# trading-bot-production-hosts END$/d' "$hosts_file" > "$tmp"
    else
        cat "$hosts_file" > "$tmp"
    fi
    printf '\n%s\n' "$block" >> "$tmp"
    mv "$tmp" "$hosts_file"
}

replace_hosts_block_remote() {
    local block
    block="$(hosts_block)"
    ssh_iran "set -euo pipefail
hosts_file='/etc/hosts'
tmp=\$(mktemp)
if grep -qF '# trading-bot-production-hosts START' \"\$hosts_file\"; then
  sed '/^# trading-bot-production-hosts START$/,/^# trading-bot-production-hosts END$/d' \"\$hosts_file\" > \"\$tmp\"
else
  cat \"\$hosts_file\" > \"\$tmp\"
fi
cat >> \"\$tmp\" <<'EOF_HOSTS'
$block
EOF_HOSTS
mv \"\$tmp\" \"\$hosts_file\""
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
    if [[ ! -f "$hash_file" || "$(cat "$hash_file")" != "$current_hash" ]]; then
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
        rm -f "$output_dir"/*.whl "$output_dir"/.requirements_hash 2>/dev/null || true
        mapfile -t pip_platform_args < <(append_pip_platform_args "$target_arch")
        python3 -m pip download -r "$bootstrap_requirements" \
            -d "$output_dir/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            "${pip_platform_args[@]}" \
            --only-binary=:all:
        python3 -m pip download -r "$LOCAL_PROJECT_DIR/requirements.txt" \
            -d "$output_dir/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            "${pip_platform_args[@]}" \
            --only-binary=:all:
        printf '%s' "$current_hash" > "$hash_file"
    fi
}

build_release() {
    if [[ "$IRAN_SKIP_FRONTEND_BUILD" != "1" ]]; then
        log "Building frontend locally"
        (cd "$LOCAL_FRONTEND_DIR" && npm install --silent && NODE_OPTIONS="--max-old-space-size=1024" npm run build)
    else
        log "Skipping frontend build because IRAN_SKIP_FRONTEND_BUILD=1"
    fi
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
        --exclude '.venv' \
        --exclude '.vscode' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude 'frontend' \
        --exclude 'node_modules' \
        --exclude 'tests' \
        --exclude 'tmp' \
        --exclude 'uploads' \
        --exclude 'map_data' \
        --exclude 'pip_packages' \
        "$LOCAL_PROJECT_DIR/" "$iran_context_dir/"
    rsync -a --delete "$iran_pip_dir/" "$iran_context_dir/pip_packages/"

    log "Building Docker images for Iran platform=$IRAN_IMAGE_PLATFORM"
    ensure_buildx_for_target
    if [[ "$LOCAL_HOST_ARCH" == "$IRAN_HOST_ARCH" ]]; then
        docker pull "postgres:15-alpine" >/dev/null
        docker pull "redis:7-alpine" >/dev/null
        docker build -f "$iran_context_dir/Dockerfile.iran" -t trading_bot_base_iran "$iran_context_dir"
        docker save trading_bot_base_iran postgres:15-alpine redis:7-alpine -o "$RELEASE_TMP_DIR/docker-images.tar"
    else
        docker pull --platform "$IRAN_IMAGE_PLATFORM" postgres:15-alpine >/dev/null
        docker tag postgres:15-alpine "postgres:15-alpine-iran-$IRAN_HOST_ARCH"
        docker pull --platform "$IRAN_IMAGE_PLATFORM" redis:7-alpine >/dev/null
        docker tag redis:7-alpine "redis:7-alpine-iran-$IRAN_HOST_ARCH"
        docker buildx build --platform "$IRAN_IMAGE_PLATFORM" -f "$iran_context_dir/Dockerfile.iran" -t trading_bot_base_iran --output "type=docker,dest=$RELEASE_TMP_DIR/trading_bot_base_iran.tar" "$iran_context_dir"
        docker load -i "$RELEASE_TMP_DIR/trading_bot_base_iran.tar" >/dev/null
        docker save trading_bot_base_iran "postgres:15-alpine-iran-$IRAN_HOST_ARCH" "redis:7-alpine-iran-$IRAN_HOST_ARCH" -o "$RELEASE_TMP_DIR/docker-images.tar"
    fi
    log "Local release build complete"
}

ensure_runtime_env_file() {
    local local_env_path="$LOCAL_ENV_SOURCE_PATH"
    if [[ -f "$local_env_path" && -f "$IRAN_ENV_SOURCE_PATH" ]]; then
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
    local error_tracking_dsn=""
    local trusted_proxy_cidrs="127.0.0.1/32,::1/128"
    local observability_telegram_user_hash_salt=""
    local grafana_alert_default_receiver="Trading Bot Production Webhook"
    local grafana_alert_critical_receiver="Trading Bot Production Webhook"
    local grafana_alert_warning_receiver="Trading Bot Production Email"
    local grafana_alert_webhook_url=""
    local grafana_alert_email_addresses=""

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
    prompt_value observability_api_key "OBSERVABILITY_API_KEY" "" 1
    prompt_value channel_id "CHANNEL_ID"
    prompt_value channel_invite_link "CHANNEL_INVITE_LINK"
    prompt_value smsir_api_key "SMSIR_API_KEY" "" 1
    prompt_value smsir_line_number "SMSIR_LINE_NUMBER"
    prompt_value error_tracking_dsn "ERROR_TRACKING_DSN"
    prompt_value trusted_proxy_cidrs "TRUSTED_PROXY_CIDRS" "$trusted_proxy_cidrs"
    prompt_value observability_telegram_user_hash_salt "OBSERVABILITY_TELEGRAM_USER_HASH_SALT" "" 1
    prompt_value grafana_alert_default_receiver "GRAFANA_ALERT_DEFAULT_RECEIVER" "$grafana_alert_default_receiver"
    prompt_value grafana_alert_critical_receiver "GRAFANA_ALERT_CRITICAL_RECEIVER" "$grafana_alert_critical_receiver"
    prompt_value grafana_alert_warning_receiver "GRAFANA_ALERT_WARNING_RECEIVER" "$grafana_alert_warning_receiver"
    prompt_value grafana_alert_webhook_url "GRAFANA_ALERT_WEBHOOK_URL"
    prompt_value grafana_alert_email_addresses "GRAFANA_ALERT_EMAIL_ADDRESSES"

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
    OBSERVABILITY_API_KEY="$observability_api_key" \
    CHANNEL_ID="$channel_id" \
    CHANNEL_INVITE_LINK="$channel_invite_link" \
    SMSIR_API_KEY="$smsir_api_key" \
    SMSIR_LINE_NUMBER="$smsir_line_number" \
    ERROR_TRACKING_DSN="$error_tracking_dsn" \
    TRUSTED_PROXY_CIDRS="$trusted_proxy_cidrs" \
    OBSERVABILITY_TELEGRAM_USER_HASH_SALT="$observability_telegram_user_hash_salt" \
    GRAFANA_ALERT_DEFAULT_RECEIVER="$grafana_alert_default_receiver" \
    GRAFANA_ALERT_CRITICAL_RECEIVER="$grafana_alert_critical_receiver" \
    GRAFANA_ALERT_WARNING_RECEIVER="$grafana_alert_warning_receiver" \
    GRAFANA_ALERT_WEBHOOK_URL="$grafana_alert_webhook_url" \
    GRAFANA_ALERT_EMAIL_ADDRESSES="$grafana_alert_email_addresses" \
    python3 "$RUNTIME_ENV_RENDERER" \
        --local-output "$local_env_path" \
        --iran-output "$IRAN_ENV_SOURCE_PATH" \
        --foreign-frontend-url "$FOREIGN_FRONTEND_URL" \
        --iran-frontend-url "$IRAN_FRONTEND_URL" \
        --foreign-server-url "$FOREIGN_SERVER_URL" \
        --foreign-server-domain "$FOREIGN_SERVER_DOMAIN" \
        --iran-server-url "$IRAN_SERVER_URL" \
        --iran-server-domain "$IRAN_SERVER_DOMAIN"

    chmod 600 "$local_env_path" || true
    chmod 600 "$IRAN_ENV_SOURCE_PATH" || true
    log "Created local env at $local_env_path"
    log "Created Iran runtime env at $IRAN_ENV_SOURCE_PATH"
}

deploy_foreign() {
    if [[ "$IRAN_SKIP_FOREIGN_DEPLOY" == "1" ]]; then
        log "Skipping foreign deploy because IRAN_SKIP_FOREIGN_DEPLOY=1"
        return 0
    fi
    log "Deploying the foreign server locally"
    ensure_runtime_env_file
    (cd "$LOCAL_PROJECT_DIR" && bash ./deploy.sh foreign)
}

sync_project() {
    log "Syncing production payload to the Iran host"
    ensure_runtime_env_file
    local staging_dir="$IRAN_PROJECT_DIR"
    ssh_iran "mkdir -p '$IRAN_DEPLOY_BASE_DIR' '$IRAN_DEPLOY_BASE_DIR/releases' '$staging_dir'"
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
    rsync -avz --delete -e "$RSYNC_SSH" \
        "$LOCAL_PROJECT_DIR/pip_packages/" "$IRAN_SSH_TARGET:$staging_dir/pip_packages/"
    rsync -avz --delete -e "$RSYNC_SSH" \
        "$LOCAL_DIST_DIR/" "$IRAN_SSH_TARGET:$staging_dir/mini_app_dist/"
    scp_iran "$IRAN_ENV_SOURCE_PATH" "$IRAN_SSH_TARGET:$staging_dir/.env"
    log "Production payload sync complete"
}

ship_images() {
    local bundle="$RELEASE_TMP_DIR/docker-images.tar"
    [[ -f "$bundle" ]] || die "Docker image bundle missing: $bundle"
    log "Uploading Docker image bundle to the Iran host"
    scp_iran "$bundle" "$IRAN_SSH_TARGET:$REMOTE_IMAGE_BUNDLE"
    log "Docker image bundle upload complete"
}

load_images() {
    log "Loading transferred Docker images on the Iran host"
    ssh_iran "set -euo pipefail
docker load -i '$REMOTE_IMAGE_BUNDLE'
if docker image inspect 'postgres:15-alpine-iran-$IRAN_HOST_ARCH' >/dev/null 2>&1; then
  docker tag 'postgres:15-alpine-iran-$IRAN_HOST_ARCH' 'postgres:15-alpine'
fi
if docker image inspect 'redis:7-alpine-iran-$IRAN_HOST_ARCH' >/dev/null 2>&1; then
  docker tag 'redis:7-alpine-iran-$IRAN_HOST_ARCH' 'redis:7-alpine'
fi"
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
eval \"\$compose_cmd -f docker-compose.iran.yml up -d \$wait_args\"
eval \"\$compose_cmd -f docker-compose.iran.yml ps\""
    log "Iran deploy step complete"
}

healthcheck() {
    log "Running post-deploy health checks"
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
    verify_sync_sampler_local
    verify_sync_sampler_remote
    if [[ "$IRAN_RUN_POST_DEPLOY_HEALTHCHECK" == "1" ]]; then
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
        healthcheck) check_local; healthcheck ;;
        *) die "Unknown command: $COMMAND" ;;
    esac
}

main "$@"
