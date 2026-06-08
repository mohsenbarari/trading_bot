#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_MANIFEST="$PROJECT_DIR/deploy/production/online.env"
MANIFEST_PATH="${DEPLOY_MANIFEST:-$DEFAULT_MANIFEST}"
COMMAND=""

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
    local iran_certbot_email=""
    local iran_env_source_path="$PROJECT_DIR/deploy/production/iran.runtime.env"

    prompt_value local_project_dir "Local project dir" "$local_project_dir"
    prompt_value local_frontend_dir "Local frontend dir" "$local_frontend_dir"
    prompt_value local_dist_dir "Local dist dir" "$local_dist_dir"
    prompt_value foreign_public_ip "Foreign public IP"
    prompt_value foreign_public_domain "Foreign public domain"
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
    prompt_value iran_certbot_email "Certbot email"
    prompt_value iran_env_source_path "Iran runtime env source path" "$iran_env_source_path"

    cat > "$MANIFEST_PATH" <<EOF
# Production deployment manifest for the foreign-controlled release flow.

# --- Local / foreign control plane ---
LOCAL_PROJECT_DIR=$local_project_dir
LOCAL_FRONTEND_DIR=$local_frontend_dir
LOCAL_DIST_DIR=$local_dist_dir
FOREIGN_PUBLIC_IP=$foreign_public_ip
FOREIGN_PUBLIC_DOMAIN=$foreign_public_domain
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
IRAN_CERTBOT_EMAIL=$iran_certbot_email

# --- Local source env -> remote runtime env ---
IRAN_ENV_SOURCE_PATH=$iran_env_source_path

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
    [[ -f "$MANIFEST_PATH" ]] || die "Manifest not found: $MANIFEST_PATH"
    # shellcheck disable=SC1090
    source "$MANIFEST_PATH"

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
            : "${IRAN_SSH_PRIVATE_KEY_PATH:?IRAN_SSH_PRIVATE_KEY_PATH is required for key auth}"
            [[ -f "$IRAN_SSH_PRIVATE_KEY_PATH" ]] || die "IRAN_SSH_PRIVATE_KEY_PATH does not exist: $IRAN_SSH_PRIVATE_KEY_PATH"
            SSH_IRAN_CMD=(ssh -p "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no -i "$IRAN_SSH_PRIVATE_KEY_PATH")
            SCP_IRAN_CMD=(scp -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no -i "$IRAN_SSH_PRIVATE_KEY_PATH")
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

ensure_local_tools() {
    need_cmd ssh
    need_cmd scp
    need_cmd rsync
    need_cmd git
    need_cmd docker
    need_cmd npm
    need_cmd python3
    need_cmd md5sum
    need_cmd sed
}

check_local() {
    log "Checking local prerequisites"
    ensure_local_tools
    [[ "$(id -u)" -eq 0 ]] || die "This release script must be run as root so it can update /etc/hosts and manage Docker."
    ssh_iran "echo connected-to-\$(hostname)"
    [[ -f "$LOCAL_PROJECT_DIR/requirements.txt" ]] || die "requirements.txt missing"
    [[ -f "$LOCAL_PROJECT_DIR/docker-compose.iran.yml" ]] || die "docker-compose.iran.yml missing"
    [[ -f "$LOCAL_PROJECT_DIR/Dockerfile.iran" ]] || die "Dockerfile.iran missing"
    [[ -f "$PROJECT_DIR/deploy/production/nginx-iran-online.conf.template" ]] || die "Nginx template missing"
    log "Local checks passed"
}

bootstrap_iran() {
    log "Bootstrapping the Iran host"
    ssh_iran "export DEBIAN_FRONTEND=noninteractive
set -euo pipefail
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release rsync jq nginx certbot python3-certbot-nginx
if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y docker.io
fi
if ! docker compose version >/dev/null 2>&1 2>/dev/null; then
  apt-get install -y docker-compose-plugin || apt-get install -y docker-compose
fi
systemctl enable --now docker
systemctl enable --now nginx
mkdir -p '$IRAN_DEPLOY_BASE_DIR' '$IRAN_PROJECT_DIR'
timedatectl set-timezone 'UTC' || true
if command -v ufw >/dev/null 2>&1 && [ '$IRAN_ENABLE_UFW' = '1' ]; then
  ufw allow OpenSSH || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
fi"
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
    ssh_iran "set -euo pipefail
certbot --nginx -d '$IRAN_APP_DOMAIN' --non-interactive --agree-tos --email '$IRAN_CERTBOT_EMAIL' --redirect"
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
    log "Preparing wheel cache locally"
    local hash_file="$LOCAL_PROJECT_DIR/pip_packages/.requirements_hash"
    local current_hash
    current_hash="$(md5sum "$LOCAL_PROJECT_DIR/requirements.txt" | cut -d' ' -f1)"
    mkdir -p "$LOCAL_PROJECT_DIR/pip_packages"
    if [[ ! -f "$hash_file" || "$(cat "$hash_file")" != "$current_hash" ]]; then
        python3 -m pip download -r "$LOCAL_PROJECT_DIR/requirements.txt" \
            -d "$LOCAL_PROJECT_DIR/pip_packages/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            --platform manylinux2014_x86_64 \
            --platform manylinux_2_17_x86_64 \
            --platform manylinux_2_28_x86_64 \
            --platform linux_x86_64 \
            --platform any \
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
    prepare_pip_packages
    [[ -d "$LOCAL_DIST_DIR" ]] || die "Frontend dist directory missing: $LOCAL_DIST_DIR"
    mkdir -p "$RELEASE_TMP_DIR"
    log "Building Docker images locally"
    docker pull postgres:15-alpine >/dev/null
    docker pull redis:7-alpine >/dev/null
    docker build -t trading_bot_base "$LOCAL_PROJECT_DIR"
    docker build -f "$LOCAL_PROJECT_DIR/Dockerfile.iran" -t trading_bot_base_iran "$LOCAL_PROJECT_DIR"
    log "Packing Docker images for Iran transfer"
    docker save trading_bot_base_iran postgres:15-alpine redis:7-alpine -o "$RELEASE_TMP_DIR/docker-images.tar"
    log "Local release build complete"
}

ensure_runtime_env_file() {
    if [[ -f "$IRAN_ENV_SOURCE_PATH" ]]; then
        return 0
    fi

    log "Iran runtime env file not found. Creating it at $IRAN_ENV_SOURCE_PATH"
    mkdir -p "$(dirname "$IRAN_ENV_SOURCE_PATH")"

    local bot_token=""
    local bot_username=""
    local database_url=""
    local sync_database_url=""
    local postgres_db=""
    local postgres_user=""
    local postgres_password=""
    local frontend_url=""
    local redis_url=""
    local jwt_secret_key=""
    local dev_api_key=""
    local sync_api_key=""
    local channel_id=""
    local channel_invite_link=""
    local smsir_api_key=""
    local smsir_line_number=""
    local s3_endpoint_url=""
    local s3_access_key=""
    local s3_secret_key=""
    local s3_bucket_name=""
    local error_tracking_dsn=""

    prompt_value bot_token "BOT_TOKEN" "" 1
    prompt_value bot_username "BOT_USERNAME"
    prompt_value database_url "DATABASE_URL"
    prompt_value sync_database_url "SYNC_DATABASE_URL"
    prompt_value postgres_db "POSTGRES_DB"
    prompt_value postgres_user "POSTGRES_USER"
    prompt_value postgres_password "POSTGRES_PASSWORD" "" 1
    prompt_value frontend_url "FRONTEND_URL"
    prompt_value redis_url "REDIS_URL"
    prompt_value jwt_secret_key "JWT_SECRET_KEY" "" 1
    prompt_value dev_api_key "DEV_API_KEY" "" 1
    prompt_value sync_api_key "SYNC_API_KEY" "" 1
    prompt_value channel_id "CHANNEL_ID"
    prompt_value channel_invite_link "CHANNEL_INVITE_LINK"
    prompt_value smsir_api_key "SMSIR_API_KEY" "" 1
    prompt_value smsir_line_number "SMSIR_LINE_NUMBER"
    prompt_value s3_endpoint_url "S3_ENDPOINT_URL"
    prompt_value s3_access_key "S3_ACCESS_KEY" "" 1
    prompt_value s3_secret_key "S3_SECRET_KEY" "" 1
    prompt_value s3_bucket_name "S3_BUCKET_NAME"
    prompt_value error_tracking_dsn "ERROR_TRACKING_DSN"

    cat > "$IRAN_ENV_SOURCE_PATH" <<EOF
BOT_TOKEN=$bot_token
BOT_USERNAME=$bot_username
DATABASE_URL=$database_url
SYNC_DATABASE_URL=$sync_database_url
POSTGRES_DB=$postgres_db
POSTGRES_USER=$postgres_user
POSTGRES_PASSWORD=$postgres_password
FRONTEND_URL=$frontend_url
REDIS_URL=$redis_url
JWT_SECRET_KEY=$jwt_secret_key
DEV_API_KEY=$dev_api_key
SYNC_API_KEY=$sync_api_key
CHANNEL_ID=$channel_id
CHANNEL_INVITE_LINK=$channel_invite_link
SMSIR_API_KEY=$smsir_api_key
SMSIR_LINE_NUMBER=$smsir_line_number
S3_ENDPOINT_URL=$s3_endpoint_url
S3_ACCESS_KEY=$s3_access_key
S3_SECRET_KEY=$s3_secret_key
S3_BUCKET_NAME=$s3_bucket_name
ERROR_TRACKING_DSN=$error_tracking_dsn
EOF

    chmod 600 "$IRAN_ENV_SOURCE_PATH" || true
    log "Created Iran runtime env at $IRAN_ENV_SOURCE_PATH"
}

deploy_foreign() {
    if [[ "$IRAN_SKIP_FOREIGN_DEPLOY" == "1" ]]; then
        log "Skipping foreign deploy because IRAN_SKIP_FOREIGN_DEPLOY=1"
        return 0
    fi
    log "Deploying the foreign server locally"
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
        --exclude 'pip_packages' \
        --exclude 'tests' \
        --exclude 'tmp' \
        --exclude 'uploads' \
        --exclude 'map_data' \
        -e "$RSYNC_SSH" \
        "$LOCAL_PROJECT_DIR/" "$IRAN_SSH_TARGET:$staging_dir/"
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
docker load -i '$REMOTE_IMAGE_BUNDLE'"
    log "Docker images loaded on the Iran host"
}

deploy_iran() {
    log "Deploying Docker services on the Iran host"
    local wait_args=""
    if [[ "$IRAN_DEPLOY_WITH_WAIT" == "1" ]]; then
        wait_args="--wait --wait-timeout 180"
    fi
    ssh_iran "set -euo pipefail
cd '$IRAN_PROJECT_DIR'
docker compose -f docker-compose.iran.yml up -d $wait_args
docker compose -f docker-compose.iran.yml ps"
    log "Iran deploy step complete"
}

healthcheck() {
    log "Running post-deploy health checks"
    ssh_iran "set -euo pipefail
curl -fsS '$IRAN_LOCAL_API_URL' >/dev/null
docker compose -f '$IRAN_PROJECT_DIR/docker-compose.iran.yml' ps >/dev/null"
    if [[ "$IRAN_RUN_POST_DEPLOY_HEALTHCHECK" == "1" ]]; then
        curl -kfsS "$IRAN_HEALTHCHECK_URL" >/dev/null
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
    build_release
    deploy_foreign
    sync_hosts_mappings
    local iran_mode
    iran_mode="$(decide_iran_connectivity)"
    if [[ "$iran_mode" == "offline" ]]; then
        log "Iran offline scenario is not implemented yet. Stopping after the foreign deploy."
        exit 20
    fi
    bootstrap_iran
    ensure_runtime_env_file
    sync_project
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
        deploy-foreign) check_local; build_release; deploy_foreign ;;
        bootstrap-iran) check_local; bootstrap_iran ;;
        configure-nginx) check_local; configure_nginx ;;
        issue-cert) check_local; issue_cert ;;
        build-release) check_local; build_release ;;
        sync-project) check_local; sync_project ;;
        ship-images) check_local; ship_images ;;
        load-images) check_local; load_images ;;
        deploy-iran) check_local; deploy_iran ;;
        healthcheck) check_local; healthcheck ;;
        *) die "Unknown command: $COMMAND" ;;
    esac
}

main "$@"
