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
    : "${IRAN_PROJECT_DIR:?IRAN_PROJECT_DIR is required}"
    : "${IRAN_DEPLOY_BASE_DIR:?IRAN_DEPLOY_BASE_DIR is required}"
    : "${IRAN_TIMEZONE:?IRAN_TIMEZONE is required}"
    : "${IRAN_APP_DOMAIN:?IRAN_APP_DOMAIN is required}"
    : "${IRAN_CERTBOT_EMAIL:?IRAN_CERTBOT_EMAIL is required}"
    : "${IRAN_ENV_SOURCE_PATH:?IRAN_ENV_SOURCE_PATH is required}"

    IRAN_SKIP_CERTBOT="${IRAN_SKIP_CERTBOT:-0}"
    IRAN_SKIP_FRONTEND_BUILD="${IRAN_SKIP_FRONTEND_BUILD:-0}"
    IRAN_DEPLOY_WITH_WAIT="${IRAN_DEPLOY_WITH_WAIT:-1}"
    IRAN_RUN_POST_DEPLOY_HEALTHCHECK="${IRAN_RUN_POST_DEPLOY_HEALTHCHECK:-1}"
    IRAN_ENABLE_UFW="${IRAN_ENABLE_UFW:-0}"
    IRAN_CONNECTIVITY_MODE="${IRAN_CONNECTIVITY_MODE:-ask}"
    IRAN_SKIP_FOREIGN_DEPLOY="${IRAN_SKIP_FOREIGN_DEPLOY:-0}"
    IRAN_HEALTHCHECK_URL="${IRAN_HEALTHCHECK_URL:-https://$IRAN_APP_DOMAIN/api/config}"
    IRAN_LOCAL_API_URL="${IRAN_LOCAL_API_URL:-http://127.0.0.1:8000/api/config}"

    [[ -d "$LOCAL_PROJECT_DIR" ]] || die "LOCAL_PROJECT_DIR does not exist: $LOCAL_PROJECT_DIR"
    [[ -d "$LOCAL_FRONTEND_DIR" ]] || die "LOCAL_FRONTEND_DIR does not exist: $LOCAL_FRONTEND_DIR"
    [[ -f "$IRAN_ENV_SOURCE_PATH" ]] || die "IRAN_ENV_SOURCE_PATH does not exist: $IRAN_ENV_SOURCE_PATH"

    IRAN_SSH_TARGET="$IRAN_SSH_USER@$IRAN_HOST"
    RSYNC_SSH="ssh -p $IRAN_SSH_PORT -o StrictHostKeyChecking=no"
    RELEASE_TMP_DIR="$LOCAL_PROJECT_DIR/tmp/production-release"
    REMOTE_IMAGE_BUNDLE="$IRAN_DEPLOY_BASE_DIR/releases/trading-bot-images.tar"
}

ssh_iran() {
    ssh -p "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no "$IRAN_SSH_TARGET" "$@"
}

scp_iran() {
    scp -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no "$@"
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
}

check_local() {
    log "Checking local prerequisites"
    ensure_local_tools
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
timedatectl set-timezone '$IRAN_TIMEZONE' || true
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
    build_release
    deploy_foreign
    local iran_mode
    iran_mode="$(decide_iran_connectivity)"
    if [[ "$iran_mode" == "offline" ]]; then
        log "Iran offline scenario is not implemented yet. Stopping after the foreign deploy."
        exit 20
    fi
    bootstrap_iran
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
