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
STAGING_FOREIGN_APP_PORT="${STAGING_FOREIGN_APP_PORT:-8121}"
STAGING_PROJECT_NAME="${STAGING_PROJECT_NAME:-trading_bot_staging}"
STAGING_NGINX_SITE="${STAGING_NGINX_SITE:-trading-bot-staging}"
STAGING_ENABLE_BOT="${STAGING_ENABLE_BOT:-0}"
STAGING_FOREIGN_ONLY="${STAGING_FOREIGN_ONLY:-0}"
STAGING_BOT_USERNAME="${STAGING_BOT_USERNAME:-}"
STAGING_INTERNAL_IRAN_SERVER_URL="${STAGING_INTERNAL_IRAN_SERVER_URL:-http://app:8000}"
STAGING_PUBLIC_FOREIGN_SYNC_URL="${STAGING_PUBLIC_FOREIGN_SYNC_URL:-https://staging.362514.ir/foreign-sync}"
default_staging_internal_foreign_server_url() {
    if [[ "$STAGING_ENABLE_BOT" == "1" ]]; then
        printf 'http://foreign_app:8000\n'
    else
        printf '%s\n' "$STAGING_PUBLIC_FOREIGN_SYNC_URL"
    fi
}
STAGING_INTERNAL_FOREIGN_SERVER_URL="${STAGING_INTERNAL_FOREIGN_SERVER_URL:-$(default_staging_internal_foreign_server_url)}"
STAGING_FOREIGN_IRAN_SERVER_URL="${STAGING_FOREIGN_IRAN_SERVER_URL:-https://staging.gold-trade.ir}"
STAGING_FOREIGN_FRONTEND_URL="${STAGING_FOREIGN_FRONTEND_URL:-$STAGING_FOREIGN_IRAN_SERVER_URL}"
STAGING_FOREIGN_FOREIGN_SERVER_URL="${STAGING_FOREIGN_FOREIGN_SERVER_URL:-$STAGING_INTERNAL_FOREIGN_SERVER_URL}"
STAGING_IRAN_PUBLIC_DOMAIN="${STAGING_IRAN_PUBLIC_DOMAIN:-staging.gold-trade.ir}"
STAGING_IRAN_PUBLIC_IP="${STAGING_IRAN_PUBLIC_IP:-65.109.220.59}"
STAGING_FOREIGN_PUBLIC_DOMAIN="${STAGING_FOREIGN_PUBLIC_DOMAIN:-staging.362514.ir}"
STAGING_FOREIGN_PUBLIC_IP="${STAGING_FOREIGN_PUBLIC_IP:-65.109.216.187}"
STAGING_FOREIGN_PUBLIC_SURFACE_GUARD="${STAGING_FOREIGN_PUBLIC_SURFACE_GUARD:-$STAGING_ENABLE_BOT}"
STAGING_ENABLE_DEV_LOGIN="${STAGING_ENABLE_DEV_LOGIN:-}"
STAGING_WEB_PUSH_SUBJECT="${STAGING_WEB_PUSH_SUBJECT:-mailto:admin@362514.ir}"
STAGING_TRUSTED_PROXY_CIDRS="${STAGING_TRUSTED_PROXY_CIDRS:-127.0.0.1/32,::1/128,172.16.0.0/12}"
STAGING_BASIC_AUTH_FILE="${STAGING_BASIC_AUTH_FILE:-/etc/nginx/.htpasswd-trading-bot-staging}"
STAGING_NGINX_DEDUPLICATE="${STAGING_NGINX_DEDUPLICATE:-1}"
STAGING_FRONTEND_DIST_DIR="${STAGING_FRONTEND_DIST_DIR:-mini_app_dist_staging}"
case "$STAGING_FRONTEND_DIST_DIR" in
    /*) ;;
    *) STAGING_FRONTEND_DIST_DIR="$PROJECT_DIR/$STAGING_FRONTEND_DIST_DIR" ;;
esac
STAGING_FRONTEND_DIST_DIR="$(realpath -m "$STAGING_FRONTEND_DIST_DIR")"
export STAGING_IRAN_PUBLIC_DOMAIN STAGING_IRAN_PUBLIC_IP
export STAGING_FOREIGN_PUBLIC_DOMAIN STAGING_FOREIGN_PUBLIC_IP

compose_cmd=()

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

init_compose_cmd() {
    if [[ "${#compose_cmd[@]}" -gt 0 ]]; then
        return
    fi
    if docker compose version >/dev/null 2>&1; then
        compose_cmd=(docker compose -p "$STAGING_PROJECT_NAME" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
        return
    fi
    if command -v docker-compose >/dev/null 2>&1; then
        compose_cmd=(docker-compose -p "$STAGING_PROJECT_NAME" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
        return
    fi
    die "docker compose or docker-compose is required"
}

remove_legacy_compose_stateless_containers() {
    init_compose_cmd
    if [[ "${compose_cmd[0]}" != "docker-compose" ]]; then
        return
    fi

    # docker-compose 1.29 cannot recreate images produced by current Docker
    # when the legacy image metadata omits ContainerConfig. Remove only
    # stateless services; staging database and Redis containers/volumes remain.
    local service ids
    for service in migration app foreign_app bot sync_worker foreign_sync_worker; do
        ids="$(docker ps -aq \
            --filter "label=com.docker.compose.project=$STAGING_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=$service")"
        if [[ -n "$ids" ]]; then
            docker rm -f $ids >/dev/null
        fi
    done
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

require_staging_peer_url() {
    if [[ ! "$STAGING_INTERNAL_FOREIGN_SERVER_URL" =~ [^[:space:]] ]]; then
        die "staging peer URL is empty; set STAGING_INTERNAL_FOREIGN_SERVER_URL or STAGING_PUBLIC_FOREIGN_SYNC_URL"
    fi
    case "$STAGING_INTERNAL_FOREIGN_SERVER_URL" in
        http://*|https://*) ;;
        *) die "staging peer URL must start with http:// or https://" ;;
    esac
}

validate_staging_bot_username() {
    [[ -n "$STAGING_BOT_USERNAME" ]] || return 0
    if [[ ! "$STAGING_BOT_USERNAME" =~ ^[A-Za-z][A-Za-z0-9_]{1,28}[bB][oO][tT]$ ]]; then
        die "STAGING_BOT_USERNAME must be a valid public Telegram bot username ending in bot"
    fi
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
SERVER_MODE=iran
FRONTEND_URL=$STAGING_FRONTEND_URL
BOT_USERNAME=${STAGING_BOT_USERNAME:-staging_bot_placeholder}

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
SYNC_VERIFY_TLS=true
SYNC_CA_BUNDLE=
DEV_API_KEY=$dev_key
OBSERVABILITY_API_KEY=$observability_key
OBSERVABILITY_TELEGRAM_USER_HASH_SALT=$hash_salt
AUDIT_TRAIL_PATH=/app/audit_trail/audit.jsonl
STAGING_BASIC_AUTH_USER=staging
STAGING_BASIC_AUTH_PASSWORD=$(secret_hex 8)
STAGING_ENABLE_DEV_LOGIN=true
STAGING_LOG_OTP_CODES=false

PEER_SERVER_URL=
FOREIGN_SERVER_URL=
IRAN_SERVER_URL=$STAGING_INTERNAL_IRAN_SERVER_URL
GERMANY_SERVER_URL=
FOREIGN_SERVER_DOMAIN=$STAGING_DOMAIN
IRAN_SERVER_DOMAIN=
EXTRA_CORS_ORIGINS=$STAGING_FRONTEND_URL
TRUSTED_PROXY_CIDRS=$STAGING_TRUSTED_PROXY_CIDRS

BOT_TOKEN=
EOF
    log "created $ENV_FILE with staging-only secrets"
}

ensure_runtime_env_values() {
    ensure_env
    require_staging_peer_url
    validate_staging_bot_username
    if [[ -n "$STAGING_BOT_USERNAME" ]]; then
        set_env_value BOT_USERNAME "$STAGING_BOT_USERNAME"
    fi
    set_env_value FRONTEND_URL "$STAGING_FRONTEND_URL"
    set_env_value IRAN_SERVER_URL "$STAGING_INTERNAL_IRAN_SERVER_URL"
    set_env_value PEER_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"
    set_env_value FOREIGN_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"
    set_env_value GERMANY_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"
    set_env_value EXTRA_CORS_ORIGINS "$STAGING_FRONTEND_URL"
    set_env_value TRUSTED_PROXY_CIDRS "$STAGING_TRUSTED_PROXY_CIDRS"
    set_env_value AUDIT_TRAIL_PATH /app/audit_trail/audit.jsonl
    set_env_value STAGING_LOG_OTP_CODES false
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
    if [[ -n "${STAGING_RELEASE_SHA_OVERRIDE:-}" ]]; then
        printf '%s\n' "$STAGING_RELEASE_SHA_OVERRIDE"
        return
    fi
    local sha dirty
    sha="$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD 2>/dev/null || printf 'unknown')"
    dirty=""
    if [[ -n "$(git -C "$PROJECT_DIR" status --short 2>/dev/null || true)" ]]; then
        dirty="-dirty"
    fi
    printf '%s%s\n' "$sha" "$dirty"
}

staging_release_sha() {
    if [[ -n "${STAGING_RELEASE_SHA:-}" ]]; then
        printf '%s\n' "$STAGING_RELEASE_SHA"
        return
    fi
    release_sha
}

normalize_staging_frontend_permissions() {
    [[ -d "$STAGING_FRONTEND_DIST_DIR" ]] || die "staging frontend dist is missing"
    # Nginx serves this public build as an unprivileged user.
    chmod -R u=rwX,go=rX -- "$STAGING_FRONTEND_DIST_DIR"
}

build_frontend() {
    if [[ "${STAGING_SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
        [[ -f "$STAGING_FRONTEND_DIST_DIR/index.html" ]] || die "STAGING_SKIP_FRONTEND_BUILD=1 but staging frontend dist is missing"
        normalize_staging_frontend_permissions
        log "skipping frontend build; using existing $STAGING_FRONTEND_DIST_DIR"
        return
    fi
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
    normalize_staging_frontend_permissions
}

compose() {
    init_compose_cmd
    ensure_env
    assert_staging_frontend_dist_isolated
    local migration_server_mode="iran"
    if [[ "$STAGING_FOREIGN_ONLY" == "1" ]]; then
        migration_server_mode="foreign"
    fi
    STAGING_APP_PORT="$STAGING_APP_PORT" \
    STAGING_FOREIGN_APP_PORT="$STAGING_FOREIGN_APP_PORT" \
    STAGING_MIGRATION_SERVER_MODE="$migration_server_mode" \
    STAGING_FRONTEND_DOCKER_DIST_DIR="$(staging_frontend_dist_relpath)" \
    STAGING_RELEASE_SHA="$(staging_release_sha)" \
    STAGING_FOREIGN_IRAN_SERVER_URL="$STAGING_FOREIGN_IRAN_SERVER_URL" \
    STAGING_FOREIGN_FRONTEND_URL="$STAGING_FOREIGN_FRONTEND_URL" \
    STAGING_FOREIGN_FOREIGN_SERVER_URL="$STAGING_FOREIGN_FOREIGN_SERVER_URL" \
    "${compose_cmd[@]}" "$@"
}

foreign_public_surface_guard_nginx() {
    if [[ "$STAGING_FOREIGN_PUBLIC_SURFACE_GUARD" != "1" ]]; then
        return
    fi
    cat <<'NGINX'
    location = /api/config {
        auth_basic off;
        return 404;
    }
NGINX
}

render_nginx_template() {
    local redirect_server listen_directives ssl_directives foreign_public_surface_guard
    foreign_public_surface_guard="$(foreign_public_surface_guard_nginx)"
    if staging_ssl_enabled; then
        printf -v redirect_server '%s\n%s\n%s\n%s\n%s\n%s\n%s' \
            'server {' \
            '    listen 80;' \
            '    listen [::]:80;' \
            "    server_name $STAGING_DOMAIN;" \
            '    access_log off;' \
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
        -v ssl_directives="$ssl_directives" \
        -v foreign_public_surface_guard="$foreign_public_surface_guard" '
        $0 == "__HTTP_REDIRECT_SERVER__" { print redirect_server; next }
        $0 == "    __LISTEN_DIRECTIVES__" { print listen_directives; next }
        $0 == "    __SSL_DIRECTIVES__" { print ssl_directives; next }
        $0 == "    __FOREIGN_PUBLIC_SURFACE_GUARD__" { print foreign_public_surface_guard; next }
        { print }
    ' "$NGINX_TEMPLATE"
}

nginx_worker_group() {
    local worker_user
    worker_user="$(nginx -T 2>/dev/null | awk '$1 == "user" { gsub(";", "", $2); print $2; exit }' || true)"
    if [[ -n "$worker_user" ]] && getent group "$worker_user" >/dev/null 2>&1; then
        printf '%s\n' "$worker_user"
        return
    fi
    if getent group www-data >/dev/null 2>&1; then
        printf 'www-data\n'
    fi
}

install_basic_auth_file() {
    local basic_user="$1"
    local basic_password="$2"
    local basic_hash worker_group

    basic_hash="$(openssl passwd -apr1 "$basic_password")"
    printf '%s:%s\n' "$basic_user" "$basic_hash" >"$STAGING_BASIC_AUTH_FILE"

    worker_group="$(nginx_worker_group)"
    if [[ -n "$worker_group" ]]; then
        chown "root:$worker_group" "$STAGING_BASIC_AUTH_FILE"
        chmod 0640 "$STAGING_BASIC_AUTH_FILE"
    else
        chown root:root "$STAGING_BASIC_AUTH_FILE"
        chmod 0644 "$STAGING_BASIC_AUTH_FILE"
    fi

    [[ -s "$STAGING_BASIC_AUTH_FILE" ]] || die "staging Basic Auth file is empty: $STAGING_BASIC_AUTH_FILE"
    [[ -r "$STAGING_BASIC_AUTH_FILE" ]] || die "staging Basic Auth file is not readable by the deploying user: $STAGING_BASIC_AUTH_FILE"
}

deduplicate_staging_nginx_sites() {
    if [[ "$STAGING_NGINX_DEDUPLICATE" != "1" ]]; then
        return
    fi

    local enabled_dir="/etc/nginx/sites-enabled"
    local available="$1"
    local enabled="$2"
    local backup_dir="/etc/nginx/sites-disabled/trading-bot-staging-duplicates-$(date -u +%Y%m%dT%H%M%SZ)"
    local available_real enabled_real site site_real removed_any

    [[ -d "$enabled_dir" ]] || return
    available_real="$(readlink -f "$available" 2>/dev/null || printf '%s\n' "$available")"
    enabled_real="$(readlink -f "$enabled" 2>/dev/null || printf '%s\n' "$enabled")"
    removed_any=0

    for site in "$enabled_dir"/*; do
        [[ -e "$site" ]] || continue
        site_real="$(readlink -f "$site" 2>/dev/null || printf '%s\n' "$site")"
        if [[ "$site" == "$enabled" || "$site_real" == "$available_real" || "$site_real" == "$enabled_real" ]]; then
            continue
        fi
        if ! grep -q "server_name $STAGING_DOMAIN" "$site" 2>/dev/null; then
            continue
        fi

        mkdir -p "$backup_dir"
        if [[ -L "$site" ]]; then
            rm -f "$site"
            log "disabled duplicate staging Nginx symlink $site for $STAGING_DOMAIN"
        else
            mv "$site" "$backup_dir/$(basename "$site")"
            log "moved duplicate staging Nginx file $site to $backup_dir"
        fi
        removed_any=1
    done

    if [[ "$removed_any" == "1" ]]; then
        log "deduplicated Nginx staging server blocks for $STAGING_DOMAIN"
    fi
}

install_nginx() {
    require_staging_ssl_if_forced
    ensure_basic_auth_env
    [[ -f "$NGINX_TEMPLATE" ]] || die "missing $NGINX_TEMPLATE"
    local available="/etc/nginx/sites-available/$STAGING_NGINX_SITE"
    local enabled="/etc/nginx/sites-enabled/$STAGING_NGINX_SITE"
    local tmp basic_user basic_password dev_key
    basic_user="$(env_value STAGING_BASIC_AUTH_USER)"
    basic_password="$(env_value STAGING_BASIC_AUTH_PASSWORD)"
    dev_key="$(env_value DEV_API_KEY)"
    [[ -n "$basic_user" ]] || die "STAGING_BASIC_AUTH_USER is empty"
    [[ -n "$basic_password" ]] || die "STAGING_BASIC_AUTH_PASSWORD is empty"
    [[ -n "$dev_key" ]] || die "DEV_API_KEY is empty"

    install_basic_auth_file "$basic_user" "$basic_password"

    tmp="$(mktemp)"
    render_nginx_template | sed \
        -e "s#__SERVER_NAME__#$STAGING_DOMAIN#g" \
        -e "s#__APP_ROOT__#$PROJECT_DIR#g" \
        -e "s#__FRONTEND_ROOT__#$STAGING_FRONTEND_DIST_DIR#g" \
        -e "s#__APP_PORT__#$STAGING_APP_PORT#g" \
        -e "s#__FOREIGN_APP_PORT__#$STAGING_FOREIGN_APP_PORT#g" \
        -e "s#__BASIC_AUTH_FILE__#$STAGING_BASIC_AUTH_FILE#g" \
        -e "s#__DEV_API_KEY__#$dev_key#g" \
        >"$tmp"

    install -m 0644 "$tmp" "$available"
    rm -f "$tmp"
    ln -sfn "$available" "$enabled"
    deduplicate_staging_nginx_sites "$available" "$enabled"
    nginx -t
    nginx -s reload
    log "installed nginx site $STAGING_NGINX_SITE for $STAGING_DOMAIN"
}

health() {
    local base="${1:-$STAGING_FRONTEND_URL}"
    local path="/api/config"
    log "checking $base$path"
    ensure_basic_auth_env
    local basic_user basic_password
    basic_user="$(env_value STAGING_BASIC_AUTH_USER)"
    basic_password="$(env_value STAGING_BASIC_AUTH_PASSWORD)"
    if [[ "$STAGING_FOREIGN_PUBLIC_SURFACE_GUARD" == "1" && "$base" == "$STAGING_FRONTEND_URL" ]]; then
        local status_code
        if [[ "$base" == "http://$STAGING_DOMAIN" ]]; then
            status_code="$(curl -sS --max-time 10 --user "$basic_user:$basic_password" --resolve "$STAGING_DOMAIN:80:127.0.0.1" -o /dev/null -w '%{http_code}' "$base$path")"
        elif [[ "$base" == "https://$STAGING_DOMAIN" ]]; then
            status_code="$(curl -sS --max-time 10 --user "$basic_user:$basic_password" --resolve "$STAGING_DOMAIN:443:127.0.0.1" -o /dev/null -w '%{http_code}' "$base$path")"
        else
            status_code="$(curl -sS --max-time 10 --user "$basic_user:$basic_password" -o /dev/null -w '%{http_code}' "$base$path")"
        fi
        [[ "$status_code" == "404" ]] || die "foreign staging public /api/config guard returned HTTP $status_code instead of 404"
        printf 'foreign_public_surface_guard=404\n'
        return
    fi
    if [[ "$base" == "http://$STAGING_DOMAIN" ]]; then
        curl -fsS --max-time 10 --user "$basic_user:$basic_password" --resolve "$STAGING_DOMAIN:80:127.0.0.1" "$base$path"
    elif [[ "$base" == "https://$STAGING_DOMAIN" ]]; then
        curl -fsS --max-time 10 --user "$basic_user:$basic_password" --resolve "$STAGING_DOMAIN:443:127.0.0.1" "$base$path"
    else
        curl -fsS --max-time 10 --user "$basic_user:$basic_password" "$base$path"
    fi
    printf '\n'
}

wait_for_service_health() {
    local service="$1"
    local label="$2"
    local attempts="${STAGING_HEALTH_WAIT_ATTEMPTS:-30}"
    local delay="${STAGING_HEALTH_WAIT_DELAY:-2}"
    local cid status
    log "waiting for $label health"
    cid="$(compose ps -q "$service")"
    [[ -n "$cid" ]] || die "$label container was not created"
    for _ in $(seq 1 "$attempts"); do
        status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
        if [[ "$status" == "healthy" || "$status" == "running" ]]; then
            log "$label is $status"
            return
        fi
        sleep "$delay"
    done
    compose ps
    die "$label did not become healthy"
}

wait_for_app_health() {
    if [[ "$STAGING_FOREIGN_ONLY" == "1" ]]; then
        return
    fi
    wait_for_service_health app "staging app"
}

start_sync_worker() {
    if [[ "$STAGING_ENABLE_BOT" == "1" ]]; then
        compose --profile staging-bot --profile staging-sync up -d --build sync_worker foreign_sync_worker
        return
    fi
    compose up -d --build sync_worker
}

wait_for_foreign_app_health_if_enabled() {
    if [[ "$STAGING_ENABLE_BOT" != "1" ]]; then
        return
    fi
    wait_for_service_health foreign_app "staging foreign app"
}

check() {
    assert_staging_frontend_dist_isolated
    require_cmd docker
    require_cmd curl
    require_cmd git
    require_cmd sed
    require_cmd nginx
    init_compose_cmd
    [[ -f "$COMPOSE_FILE" ]] || die "missing $COMPOSE_FILE"
    [[ -f "$NGINX_TEMPLATE" ]] || die "missing $NGINX_TEMPLATE"
    log "domain=$STAGING_DOMAIN frontend_url=$STAGING_FRONTEND_URL ssl=$STAGING_ENABLE_SSL app_port=$STAGING_APP_PORT foreign_app_port=$STAGING_FOREIGN_APP_PORT project=$STAGING_PROJECT_NAME frontend_dist=$STAGING_FRONTEND_DIST_DIR foreign_iran_url=$STAGING_FOREIGN_IRAN_SERVER_URL foreign_public_guard=$STAGING_FOREIGN_PUBLIC_SURFACE_GUARD"
    getent hosts "$STAGING_DOMAIN" || true
}

deploy() {
    check
    ensure_env
    ensure_runtime_env_values
    build_frontend
    remove_legacy_compose_stateless_containers
    if [[ "$STAGING_ENABLE_BOT" == "1" && "$STAGING_FOREIGN_ONLY" == "1" ]]; then
        compose --profile staging-bot --profile staging-sync up -d --build foreign_app bot foreign_sync_worker
    elif [[ "$STAGING_ENABLE_BOT" == "1" ]]; then
        compose --profile staging-bot up -d --build
    else
        compose up -d --build
    fi
    wait_for_app_health
    if [[ "$STAGING_FOREIGN_ONLY" != "1" ]]; then
        start_sync_worker
    fi
    wait_for_foreign_app_health_if_enabled
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
        ensure_runtime_env_values
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
