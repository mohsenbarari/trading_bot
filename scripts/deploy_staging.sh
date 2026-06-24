#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/deploy/staging/docker-compose.staging.yml"
NGINX_TEMPLATE="$PROJECT_DIR/deploy/staging/nginx-staging.conf.template"
ENV_FILE="$PROJECT_DIR/.env.staging"
PRODUCTION_FRONTEND_DIST_DIR="$(realpath -m "$PROJECT_DIR/mini_app_dist")"

STAGING_DOMAIN="${STAGING_DOMAIN:-staging.362514.ir}"
STAGING_ENABLE_SSL="${STAGING_ENABLE_SSL:-auto}"
STAGING_SSL_CERT="${STAGING_SSL_CERT:-/etc/letsencrypt/live/$STAGING_DOMAIN/fullchain.pem}"
STAGING_SSL_KEY="${STAGING_SSL_KEY:-/etc/letsencrypt/live/$STAGING_DOMAIN/privkey.pem}"

staging_ssl_enabled() {
    if [[ "$STAGING_ENABLE_SSL" == "1" ]]; then
        if [[ -f "$STAGING_SSL_CERT" && -f "$STAGING_SSL_KEY" ]]; then
            return 0
        fi
        return 1
    fi
    if [[ "$STAGING_ENABLE_SSL" == "auto" && -f "$STAGING_SSL_CERT" && -f "$STAGING_SSL_KEY" ]]; then
        return 0
    fi
    return 1
}

require_staging_ssl_if_forced() {
    if [[ "$STAGING_ENABLE_SSL" == "1" && ! -f "$STAGING_SSL_CERT" ]]; then
        die "staging SSL enabled but $STAGING_SSL_CERT is missing"
    fi
    if [[ "$STAGING_ENABLE_SSL" == "1" && ! -f "$STAGING_SSL_KEY" ]]; then
        die "staging SSL enabled but $STAGING_SSL_KEY is missing"
    fi
}

default_staging_frontend_url() {
    if staging_ssl_enabled; then
        printf 'https://%s\n' "$STAGING_DOMAIN"
    else
        printf 'http://%s\n' "$STAGING_DOMAIN"
    fi
}

STAGING_FRONTEND_URL="${STAGING_FRONTEND_URL:-$(default_staging_frontend_url)}"
STAGING_APP_PORT="${STAGING_APP_PORT:-8100}"
STAGING_PROJECT_NAME="${STAGING_PROJECT_NAME:-trading_bot_staging}"
STAGING_NGINX_SITE="${STAGING_NGINX_SITE:-trading-bot-staging}"
STAGING_ENABLE_BOT="${STAGING_ENABLE_BOT:-0}"
STAGING_ENABLE_DEV_LOGIN="${STAGING_ENABLE_DEV_LOGIN:-}"
STAGING_WEB_PUSH_SUBJECT="${STAGING_WEB_PUSH_SUBJECT:-mailto:admin@362514.ir}"
STAGING_BASIC_AUTH_FILE="${STAGING_BASIC_AUTH_FILE:-/etc/nginx/.htpasswd-trading-bot-staging}"
STAGING_FRONTEND_DIST_DIR="${STAGING_FRONTEND_DIST_DIR:-mini_app_dist_staging}"
case "$STAGING_FRONTEND_DIST_DIR" in
    /*) ;;
    *) STAGING_FRONTEND_DIST_DIR="$PROJECT_DIR/$STAGING_FRONTEND_DIST_DIR" ;;
esac
STAGING_FRONTEND_DIST_DIR="$(realpath -m "$STAGING_FRONTEND_DIST_DIR")"

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

staging_frontend_dist_relpath() {
    case "$STAGING_FRONTEND_DIST_DIR" in
        "$PROJECT_DIR"/*)
            printf '%s\n' "${STAGING_FRONTEND_DIST_DIR#"$PROJECT_DIR"/}"
            ;;
        *)
            die "STAGING_FRONTEND_DIST_DIR must stay inside $PROJECT_DIR so the staging Docker build can copy it"
            ;;
    esac
}

assert_staging_frontend_dist_isolated() {
    if [[ "$STAGING_FRONTEND_DIST_DIR" == "$PRODUCTION_FRONTEND_DIST_DIR" ]]; then
        die "staging frontend dist must not share production mini_app_dist"
    fi
}

secret_hex() {
    openssl rand -hex "${1:-32}"
}

env_value() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-
}

set_env_value() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        $0 ~ "^" key "=" {
            print key "=" value
            found = 1
            next
        }
        { print }
        END {
            if (!found) {
                print key "=" value
            }
        }
    ' "$ENV_FILE" >"$tmp"
    install -m 0600 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
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
STAGING_LOG_OTP_CODES=true

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

ensure_runtime_env_values() {
    ensure_env
    set_env_value FRONTEND_URL "$STAGING_FRONTEND_URL"
    set_env_value EXTRA_CORS_ORIGINS "$STAGING_FRONTEND_URL"
    set_env_value STAGING_LOG_OTP_CODES true
    ensure_web_push_env
}

ensure_web_push_env() {
    ensure_env
    local public_key private_key subject tmp key value
    public_key="$(env_value WEB_PUSH_VAPID_PUBLIC_KEY || true)"
    private_key="$(env_value WEB_PUSH_VAPID_PRIVATE_KEY || true)"
    subject="$(env_value WEB_PUSH_VAPID_SUBJECT || true)"

    set_env_value WEB_PUSH_ENABLED true

    if [[ -n "$public_key" && -n "$private_key" && -n "$subject" ]]; then
        return
    fi

    require_cmd python3
    tmp="$(mktemp)"
    python3 "$PROJECT_DIR/scripts/generate_vapid_keys.py" --subject "$STAGING_WEB_PUSH_SUBJECT" >"$tmp"
    while IFS='=' read -r key value; do
        case "$key" in
            WEB_PUSH_VAPID_PUBLIC_KEY|WEB_PUSH_VAPID_PRIVATE_KEY|WEB_PUSH_VAPID_SUBJECT)
                set_env_value "$key" "$value"
                ;;
        esac
    done <"$tmp"
    rm -f "$tmp"
    log "generated staging Web Push VAPID keys in $ENV_FILE"
}

ensure_basic_auth_env() {
    ensure_env
    ensure_runtime_env_values
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
    assert_staging_frontend_dist_isolated
    local dev_login_enabled="${STAGING_ENABLE_DEV_LOGIN:-}"
    if [[ -z "$dev_login_enabled" && -f "$ENV_FILE" ]]; then
        dev_login_enabled="$(env_value STAGING_ENABLE_DEV_LOGIN || true)"
    fi
    dev_login_enabled="${dev_login_enabled:-true}"
    log "building frontend for $STAGING_FRONTEND_URL into $STAGING_FRONTEND_DIST_DIR"
    (
        cd "$PROJECT_DIR/frontend"
        FRONTEND_BUILD_OUT_DIR="$STAGING_FRONTEND_DIST_DIR" \
        VITE_API_BASE_URL="${STAGING_VITE_API_BASE_URL:-}" \
        VITE_STAGING_DEV_LOGIN="$dev_login_enabled" \
        npm run build
    )
}

compose() {
    ensure_env
    assert_staging_frontend_dist_isolated
    STAGING_APP_PORT="$STAGING_APP_PORT" \
    STAGING_FRONTEND_DOCKER_DIST_DIR="$(staging_frontend_dist_relpath)" \
    STAGING_RELEASE_SHA="$(release_sha)" \
    "${compose_cmd[@]}" "$@"
}

render_nginx_template() {
    local redirect_server listen_directives ssl_directives
    if staging_ssl_enabled; then
        printf -v redirect_server '%s\n%s\n%s\n%s\n%s\n%s' \
            'server {' \
            '    listen 80;' \
            '    listen [::]:80;' \
            "    server_name $STAGING_DOMAIN;" \
            '    return 301 https://$host$request_uri;' \
            '}'
        printf -v listen_directives '%s\n%s' \
            '    listen 443 ssl http2;' \
            '    listen [::]:443 ssl http2;'
        printf -v ssl_directives '%s\n%s\n%s\n%s' \
            "    ssl_certificate $STAGING_SSL_CERT;" \
            "    ssl_certificate_key $STAGING_SSL_KEY;" \
            '    include /etc/letsencrypt/options-ssl-nginx.conf;' \
            '    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;'
    else
        redirect_server=""
        printf -v listen_directives '%s\n%s' \
            '    listen 80;' \
            '    listen [::]:80;'
        ssl_directives=""
    fi

    awk \
        -v redirect_server="$redirect_server" \
        -v listen_directives="$listen_directives" \
        -v ssl_directives="$ssl_directives" '
        $0 == "__HTTP_REDIRECT_SERVER__" { print redirect_server; next }
        $0 == "    __LISTEN_DIRECTIVES__" { print listen_directives; next }
        $0 == "    __SSL_DIRECTIVES__" { print ssl_directives; next }
        { print }
    ' "$NGINX_TEMPLATE"
}

install_nginx() {
    require_staging_ssl_if_forced
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
    render_nginx_template | sed \
        -e "s#__SERVER_NAME__#$STAGING_DOMAIN#g" \
        -e "s#__APP_ROOT__#$PROJECT_DIR#g" \
        -e "s#__FRONTEND_ROOT__#$STAGING_FRONTEND_DIST_DIR#g" \
        -e "s#__APP_PORT__#$STAGING_APP_PORT#g" \
        -e "s#__BASIC_AUTH_FILE__#$STAGING_BASIC_AUTH_FILE#g" \
        -e "s#__DEV_API_KEY__#$dev_key#g" \
        >"$tmp"

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
    elif [[ "$base" == "https://$STAGING_DOMAIN" ]]; then
        curl -fsS --max-time 10 --user "$basic_user:$basic_password" --resolve "$STAGING_DOMAIN:443:127.0.0.1" "$base/api/config"
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
    assert_staging_frontend_dist_isolated
    require_cmd docker
    require_cmd curl
    require_cmd git
    require_cmd sed
    require_cmd nginx
    docker compose version >/dev/null
    [[ -f "$COMPOSE_FILE" ]] || die "missing $COMPOSE_FILE"
    [[ -f "$NGINX_TEMPLATE" ]] || die "missing $NGINX_TEMPLATE"
    log "domain=$STAGING_DOMAIN frontend_url=$STAGING_FRONTEND_URL ssl=$STAGING_ENABLE_SSL app_port=$STAGING_APP_PORT project=$STAGING_PROJECT_NAME frontend_dist=$STAGING_FRONTEND_DIST_DIR"
    getent hosts "$STAGING_DOMAIN" || true
}

deploy() {
    check
    ensure_env
    ensure_runtime_env_values
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
        ensure_runtime_env_values
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
  build-frontend  Build frontend into mini_app_dist_staging
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
