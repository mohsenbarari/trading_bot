#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/deploy/staging/docker-compose.staging.yml"
NGINX_TEMPLATE="$PROJECT_DIR/deploy/staging/nginx-staging.conf.template"
ENV_FILE="$PROJECT_DIR/.env.staging"

STAGING_DOMAIN="${STAGING_DOMAIN:-staging.362514.ir}"
STAGING_FRONTEND_URL="${STAGING_FRONTEND_URL:-http://$STAGING_DOMAIN}"
STAGING_APP_PORT="${STAGING_APP_PORT:-8100}"
STAGING_PROJECT_NAME="${STAGING_PROJECT_NAME:-trading_bot_staging}"
STAGING_NGINX_SITE="${STAGING_NGINX_SITE:-trading-bot-staging}"
STAGING_ENABLE_BOT="${STAGING_ENABLE_BOT:-0}"
STAGING_ENABLE_DEV_LOGIN="${STAGING_ENABLE_DEV_LOGIN:-}"
STAGING_BASIC_AUTH_FILE="${STAGING_BASIC_AUTH_FILE:-/etc/nginx/.htpasswd-trading-bot-staging}"

compose_cmd=(docker compose -p "$STAGING_PROJECT_NAME" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

log() {
    printf '[staging] %s\n' "$*"
}

die() {
    printf '[staging] ERROR: %s\n' "$*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

secret_hex() {
    openssl rand -hex "${1:-32}"
}

env_value() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-
}

ensure_env() {
    if [[ -f "$ENV_FILE" ]]; then
        return
    fi

    require_cmd openssl
    local db_password jwt_secret sync_key dev_key observability_key hash_salt
    db_password="$(secret_hex 24)"
    jwt_secret="$(secret_hex 48)"
    sync_key="$(secret_hex 32)"
    dev_key="$(secret_hex 32)"
    observability_key="$(secret_hex 32)"
    hash_salt="$(secret_hex 32)"

    umask 077
    cat >"$ENV_FILE" <<EOF
ENVIRONMENT=staging
SERVER_MODE=foreign
FRONTEND_URL=$STAGING_FRONTEND_URL
BOT_USERNAME=staging_bot_placeholder

DATABASE_URL=postgresql+asyncpg://trading_bot_staging:$db_password@db:5432/trading_bot_staging
SYNC_DATABASE_URL=postgresql://trading_bot_staging:$db_password@db:5432/trading_bot_staging
POSTGRES_DB=trading_bot_staging
POSTGRES_USER=trading_bot_staging
POSTGRES_PASSWORD=$db_password

REDIS_URL=redis://redis:6379/0
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_APPENDONLY=yes
REDIS_APPENDFSYNC=everysec
REDIS_MAXMEMORY=0
REDIS_MAXMEMORY_POLICY=noeviction

JWT_SECRET_KEY=$jwt_secret
SYNC_API_KEY=$sync_key
DEV_API_KEY=$dev_key
OBSERVABILITY_API_KEY=$observability_key
OBSERVABILITY_TELEGRAM_USER_HASH_SALT=$hash_salt
STAGING_BASIC_AUTH_USER=staging
STAGING_BASIC_AUTH_PASSWORD=$(secret_hex 8)
STAGING_ENABLE_DEV_LOGIN=true

PEER_SERVER_URL=
FOREIGN_SERVER_URL=
IRAN_SERVER_URL=
GERMANY_SERVER_URL=
FOREIGN_SERVER_DOMAIN=$STAGING_DOMAIN
IRAN_SERVER_DOMAIN=
EXTRA_CORS_ORIGINS=$STAGING_FRONTEND_URL

BOT_TOKEN=
EOF
    log "created $ENV_FILE with staging-only secrets"
}

ensure_basic_auth_env() {
    ensure_env
    if ! grep -q '^STAGING_BASIC_AUTH_USER=' "$ENV_FILE"; then
        printf '\nSTAGING_BASIC_AUTH_USER=staging\n' >>"$ENV_FILE"
    fi
    if ! grep -q '^STAGING_BASIC_AUTH_PASSWORD=' "$ENV_FILE"; then
        printf 'STAGING_BASIC_AUTH_PASSWORD=%s\n' "$(secret_hex 8)" >>"$ENV_FILE"
    fi
}

release_sha() {
    local sha dirty
    sha="$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD 2>/dev/null || printf 'unknown')"
    dirty=""
    if [[ -n "$(git -C "$PROJECT_DIR" status --short 2>/dev/null || true)" ]]; then
        dirty="-dirty"
    fi
    printf '%s%s\n' "$sha" "$dirty"
}

build_frontend() {
    require_cmd npm
    local dev_login_enabled="${STAGING_ENABLE_DEV_LOGIN:-}"
    if [[ -z "$dev_login_enabled" && -f "$ENV_FILE" ]]; then
        dev_login_enabled="$(env_value STAGING_ENABLE_DEV_LOGIN || true)"
    fi
    dev_login_enabled="${dev_login_enabled:-true}"
    log "building frontend for $STAGING_FRONTEND_URL"
    (
        cd "$PROJECT_DIR/frontend"
        VITE_API_BASE_URL="${STAGING_VITE_API_BASE_URL:-}" \
        VITE_STAGING_DEV_LOGIN="$dev_login_enabled" \
        npm run build
    )
}

compose() {
    ensure_env
    STAGING_APP_PORT="$STAGING_APP_PORT" \
    STAGING_RELEASE_SHA="$(release_sha)" \
    "${compose_cmd[@]}" "$@"
}

install_nginx() {
    ensure_basic_auth_env
    [[ -f "$NGINX_TEMPLATE" ]] || die "missing $NGINX_TEMPLATE"
    local available="/etc/nginx/sites-available/$STAGING_NGINX_SITE"
    local enabled="/etc/nginx/sites-enabled/$STAGING_NGINX_SITE"
    local tmp basic_user basic_password basic_hash dev_key
    basic_user="$(env_value STAGING_BASIC_AUTH_USER)"
    basic_password="$(env_value STAGING_BASIC_AUTH_PASSWORD)"
    dev_key="$(env_value DEV_API_KEY)"
    [[ -n "$basic_user" ]] || die "STAGING_BASIC_AUTH_USER is empty"
    [[ -n "$basic_password" ]] || die "STAGING_BASIC_AUTH_PASSWORD is empty"
    [[ -n "$dev_key" ]] || die "DEV_API_KEY is empty"

    basic_hash="$(openssl passwd -apr1 "$basic_password")"
    printf '%s:%s\n' "$basic_user" "$basic_hash" >"$STAGING_BASIC_AUTH_FILE"
    chown root:www-data "$STAGING_BASIC_AUTH_FILE" 2>/dev/null || chown root:root "$STAGING_BASIC_AUTH_FILE"
    chmod 0640 "$STAGING_BASIC_AUTH_FILE" 2>/dev/null || chmod 0644 "$STAGING_BASIC_AUTH_FILE"

    tmp="$(mktemp)"
    sed \
        -e "s#__SERVER_NAME__#$STAGING_DOMAIN#g" \
        -e "s#__APP_ROOT__#$PROJECT_DIR#g" \
        -e "s#__APP_PORT__#$STAGING_APP_PORT#g" \
        -e "s#__BASIC_AUTH_FILE__#$STAGING_BASIC_AUTH_FILE#g" \
        -e "s#__DEV_API_KEY__#$dev_key#g" \
        "$NGINX_TEMPLATE" >"$tmp"

    install -m 0644 "$tmp" "$available"
    rm -f "$tmp"
    ln -sfn "$available" "$enabled"
    nginx -t
    nginx -s reload
    log "installed nginx site $STAGING_NGINX_SITE for $STAGING_DOMAIN"
}

health() {
    local base="${1:-$STAGING_FRONTEND_URL}"
    log "checking $base/api/config"
    ensure_basic_auth_env
    local basic_user basic_password
    basic_user="$(env_value STAGING_BASIC_AUTH_USER)"
    basic_password="$(env_value STAGING_BASIC_AUTH_PASSWORD)"
    if [[ "$base" == "http://$STAGING_DOMAIN" ]]; then
        curl -fsS --max-time 10 --user "$basic_user:$basic_password" --resolve "$STAGING_DOMAIN:80:127.0.0.1" "$base/api/config"
    else
        curl -fsS --max-time 10 --user "$basic_user:$basic_password" "$base/api/config"
    fi
    printf '\n'
}

wait_for_app_health() {
    local attempts="${STAGING_HEALTH_WAIT_ATTEMPTS:-30}"
    local delay="${STAGING_HEALTH_WAIT_DELAY:-2}"
    local cid status
    log "waiting for staging app health"
    cid="$(compose ps -q app)"
    [[ -n "$cid" ]] || die "staging app container was not created"
    for _ in $(seq 1 "$attempts"); do
        status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
        if [[ "$status" == "healthy" || "$status" == "running" ]]; then
            log "staging app is $status"
            return
        fi
        sleep "$delay"
    done
    compose ps
    die "staging app did not become healthy"
}

check() {
    require_cmd docker
    require_cmd curl
    require_cmd git
    require_cmd sed
    require_cmd nginx
    docker compose version >/dev/null
    [[ -f "$COMPOSE_FILE" ]] || die "missing $COMPOSE_FILE"
    [[ -f "$NGINX_TEMPLATE" ]] || die "missing $NGINX_TEMPLATE"
    log "domain=$STAGING_DOMAIN frontend_url=$STAGING_FRONTEND_URL app_port=$STAGING_APP_PORT project=$STAGING_PROJECT_NAME"
    getent hosts "$STAGING_DOMAIN" || true
}

deploy() {
    check
    ensure_env
    build_frontend
    if [[ "$STAGING_ENABLE_BOT" == "1" ]]; then
        compose --profile staging-bot up -d --build
    else
        compose up -d --build
    fi
    wait_for_app_health
    install_nginx
    compose ps
    health
}

case "${1:-deploy}" in
    check)
        check
        ;;
    ensure-env)
        ensure_env
        ;;
    build-frontend)
        build_frontend
        ;;
    nginx)
        install_nginx
        ;;
    up)
        shift
        compose up -d --build "$@"
        ;;
    deploy)
        deploy
        ;;
    ps)
        compose ps
        ;;
    health)
        health "${2:-}"
        ;;
    logs)
        shift
        compose logs --tail="${STAGING_LOG_TAIL:-200}" "$@"
        ;;
    down)
        compose down
        ;;
    *)
        cat <<EOF
Usage: scripts/deploy_staging.sh <command>

Commands:
  check           Validate local prerequisites and show staging settings
  ensure-env      Create .env.staging if missing
  build-frontend  Build frontend into mini_app_dist
  nginx           Install/reload the staging Nginx server block
  up              Compose up -d --build
  deploy          check + ensure-env + build frontend + nginx + compose up + health
  ps              Show staging compose services
  health          Check /api/config
  logs            Show recent staging logs
  down            Stop staging compose services without deleting volumes
EOF
        exit 2
        ;;
esac
