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
STAGING_OBJECT_STORAGE_ENDPOINT="${STAGING_OBJECT_STORAGE_ENDPOINT:-${ARVAN_OBJECT_STORAGE_ENDPOINT:-https://s3.ir-thr-at1.arvanstorage.ir}}"
STAGING_OBJECT_STORAGE_BUCKET="${STAGING_OBJECT_STORAGE_BUCKET:-${ARVAN_OBJECT_STORAGE_BUCKET:-}}"
STAGING_OBJECT_STORAGE_PREFIX="${STAGING_OBJECT_STORAGE_PREFIX:-${ARVAN_OBJECT_STORAGE_PREFIX:-staging/deploy-bridge}}"
STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV="${STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV:-ARVAN_OBJECT_STORAGE_ACCESS_KEY}"
STAGING_OBJECT_STORAGE_SECRET_KEY_ENV="${STAGING_OBJECT_STORAGE_SECRET_KEY_ENV:-ARVAN_OBJECT_STORAGE_SECRET_KEY}"
STAGING_OBJECT_STORAGE_ARTIFACT_DIR="${STAGING_OBJECT_STORAGE_ARTIFACT_DIR:-$PROJECT_DIR/tmp/staging-object-storage}"
STAGING_OBJECT_RELEASE_DIR="${STAGING_OBJECT_RELEASE_DIR:-$STAGING_OBJECT_STORAGE_ARTIFACT_DIR/releases}"
STAGING_OBJECT_RELEASE_DOWNLOAD_DIR="${STAGING_OBJECT_RELEASE_DOWNLOAD_DIR:-$STAGING_OBJECT_STORAGE_ARTIFACT_DIR/downloads}"
STAGING_OBJECT_RELEASE_INCLUDE_IMAGES="${STAGING_OBJECT_RELEASE_INCLUDE_IMAGES:-0}"
STAGING_OBJECT_RELEASE_INCLUDE_PIP="${STAGING_OBJECT_RELEASE_INCLUDE_PIP:-1}"
STAGING_OBJECT_RELEASE_INCLUDE_ENV="${STAGING_OBJECT_RELEASE_INCLUDE_ENV:-0}"
STAGING_OBJECT_RELEASE_ENV_KEY_ENV="${STAGING_OBJECT_RELEASE_ENV_KEY_ENV:-STAGING_OBJECT_RELEASE_ENV_KEY}"
STAGING_OBJECT_RELEASE_APPLY_DIR="${STAGING_OBJECT_RELEASE_APPLY_DIR:-$PROJECT_DIR}"
STAGING_OBJECT_RELEASE_APPLY_EXECUTE="${STAGING_OBJECT_RELEASE_APPLY_EXECUTE:-0}"

export STAGING_IRAN_PUBLIC_DOMAIN STAGING_IRAN_PUBLIC_IP
export STAGING_FOREIGN_PUBLIC_DOMAIN STAGING_FOREIGN_PUBLIC_IP
STAGING_OBJECT_RELEASE_DEPLOY_AFTER_APPLY="${STAGING_OBJECT_RELEASE_DEPLOY_AFTER_APPLY:-0}"
STAGING_OBJECT_RELEASE_CHANNEL="${STAGING_OBJECT_RELEASE_CHANNEL:-iran-staging}"

compose_cmd=()

STAGING_OBJECT_RELEASE_PROJECT_EXCLUDES=(
    '.git'
    '.github'
    '.agents'
    '.codex'
    '.venv'
    '.vscode'
    '.deploy_count'
    '__pycache__'
    '*/__pycache__'
    '*.pyc'
    'app_logs.txt'
    'repomix-output.xml'
    '.env'
    '.env.*'
    'docs'
    'frontend'
    'node_modules'
    'tests'
    'tmp'
    'uploads'
    'map_data'
    'pip_packages'
    'mini_app_dist'
    'mini_app_dist_staging'
)

STAGING_OBJECT_RELEASE_RECEIVER_PROTECTED=(
    '.git'
    '.agents'
    '.codex'
    '.env'
    '.env.*'
    'tmp'
    'uploads'
    'map_data'
    'postgres_data'
    'redis_data'
)

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

export_env_value_if_present() {
    local key="$1"
    local value
    if [[ -n "${!key-}" ]]; then
        return
    fi
    value="$(env_value "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
        export "$key=$value"
    fi
}

refresh_staging_object_storage_config() {
    ensure_env
    export_env_value_if_present ARVAN_OBJECT_STORAGE_ENDPOINT
    export_env_value_if_present ARVAN_OBJECT_STORAGE_BUCKET
    export_env_value_if_present ARVAN_OBJECT_STORAGE_PREFIX
    export_env_value_if_present STAGING_OBJECT_STORAGE_ENDPOINT
    export_env_value_if_present STAGING_OBJECT_STORAGE_BUCKET
    export_env_value_if_present STAGING_OBJECT_STORAGE_PREFIX
    export_env_value_if_present STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV
    export_env_value_if_present STAGING_OBJECT_STORAGE_SECRET_KEY_ENV

    STAGING_OBJECT_STORAGE_ENDPOINT="${STAGING_OBJECT_STORAGE_ENDPOINT:-${ARVAN_OBJECT_STORAGE_ENDPOINT:-https://s3.ir-thr-at1.arvanstorage.ir}}"
    STAGING_OBJECT_STORAGE_BUCKET="${STAGING_OBJECT_STORAGE_BUCKET:-${ARVAN_OBJECT_STORAGE_BUCKET:-}}"
    STAGING_OBJECT_STORAGE_PREFIX="${STAGING_OBJECT_STORAGE_PREFIX:-${ARVAN_OBJECT_STORAGE_PREFIX:-staging/deploy-bridge}}"
    STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV="${STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV:-ARVAN_OBJECT_STORAGE_ACCESS_KEY}"
    STAGING_OBJECT_STORAGE_SECRET_KEY_ENV="${STAGING_OBJECT_STORAGE_SECRET_KEY_ENV:-ARVAN_OBJECT_STORAGE_SECRET_KEY}"

    export_env_value_if_present "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV"
    export_env_value_if_present "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV"
}

require_staging_object_storage_config() {
    refresh_staging_object_storage_config
    [[ -n "$STAGING_OBJECT_STORAGE_ENDPOINT" ]] || die "STAGING_OBJECT_STORAGE_ENDPOINT is empty"
    [[ -n "$STAGING_OBJECT_STORAGE_BUCKET" ]] || die "STAGING_OBJECT_STORAGE_BUCKET or ARVAN_OBJECT_STORAGE_BUCKET is required"
    case "$STAGING_OBJECT_STORAGE_PREFIX" in
        staging/*|staging-probe/*) ;;
        *) die "STAGING_OBJECT_STORAGE_PREFIX must start with staging/ or staging-probe/" ;;
    esac
}

configure_staging_object_storage_env() {
    ensure_env
    refresh_staging_object_storage_config
    [[ -n "$STAGING_OBJECT_STORAGE_BUCKET" ]] || die "set STAGING_OBJECT_STORAGE_BUCKET or ARVAN_OBJECT_STORAGE_BUCKET before object-storage-configure"
    case "$STAGING_OBJECT_STORAGE_PREFIX" in
        staging/*|staging-probe/*) ;;
        *) die "STAGING_OBJECT_STORAGE_PREFIX must start with staging/ or staging-probe/" ;;
    esac

    set_env_value ARVAN_OBJECT_STORAGE_ENDPOINT "$STAGING_OBJECT_STORAGE_ENDPOINT"
    set_env_value ARVAN_OBJECT_STORAGE_BUCKET "$STAGING_OBJECT_STORAGE_BUCKET"
    set_env_value ARVAN_OBJECT_STORAGE_PREFIX "$STAGING_OBJECT_STORAGE_PREFIX"
    if [[ "${STAGING_OBJECT_STORAGE_PERSIST_SECRETS:-0}" == "1" ]]; then
        local access_value="${!STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV-}"
        local secret_value="${!STAGING_OBJECT_STORAGE_SECRET_KEY_ENV-}"
        [[ -n "$access_value" ]] || die "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV must be set when STAGING_OBJECT_STORAGE_PERSIST_SECRETS=1"
        [[ -n "$secret_value" ]] || die "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV must be set when STAGING_OBJECT_STORAGE_PERSIST_SECRETS=1"
        set_env_value "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV" "$access_value"
        set_env_value "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV" "$secret_value"
        log "stored staging object storage credentials in ignored $ENV_FILE"
    else
        log "stored staging object storage endpoint/bucket/prefix in $ENV_FILE; credentials were not persisted"
    fi
    chmod 0600 "$ENV_FILE"
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
SYNC_VERIFY_TLS=true
SYNC_CA_BUNDLE=
DEV_API_KEY=$dev_key
OBSERVABILITY_API_KEY=$observability_key
OBSERVABILITY_TELEGRAM_USER_HASH_SALT=$hash_salt
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
    set_env_value FRONTEND_URL "$STAGING_FRONTEND_URL"
    set_env_value IRAN_SERVER_URL "$STAGING_INTERNAL_IRAN_SERVER_URL"
    set_env_value PEER_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"
    set_env_value FOREIGN_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"
    set_env_value GERMANY_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"
    set_env_value EXTRA_CORS_ORIGINS "$STAGING_FRONTEND_URL"
    set_env_value TRUSTED_PROXY_CIDRS "$STAGING_TRUSTED_PROXY_CIDRS"
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

build_frontend() {
    if [[ "${STAGING_SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
        [[ -f "$STAGING_FRONTEND_DIST_DIR/index.html" ]] || die "STAGING_SKIP_FRONTEND_BUILD=1 but staging frontend dist is missing"
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
}

create_staging_object_storage_archive() {
    require_cmd tar
    assert_staging_frontend_dist_isolated
    mkdir -p "$STAGING_OBJECT_STORAGE_ARTIFACT_DIR"
    local sha archive
    sha="$(staging_release_sha)"
    archive="$STAGING_OBJECT_STORAGE_ARTIFACT_DIR/trading-bot-staging-$sha.tar.gz"
    log "packaging staging deploy artifact at $archive" >&2
    tar -C "$PROJECT_DIR" -czf "$archive" \
        --exclude='./.git' \
        --exclude='./.env' \
        --exclude='./.env.*' \
        --exclude='./frontend' \
        --exclude='./node_modules' \
        --exclude='./tmp' \
        --exclude='./uploads' \
        --exclude='./map_data' \
        --exclude='./mini_app_dist' \
        --exclude='./__pycache__' \
        --exclude='*.pyc' \
        .
    printf '%s\n' "$archive"
}

stage_object_storage_artifact() {
    local execute="${1:-0}"
    require_cmd python3
    require_staging_object_storage_config
    ensure_runtime_env_values
    build_frontend
    local archive manifest_args execute_args
    archive="$(create_staging_object_storage_archive)"
    manifest_args=(
        --artifact "$archive"
        --endpoint "$STAGING_OBJECT_STORAGE_ENDPOINT"
        --bucket "$STAGING_OBJECT_STORAGE_BUCKET"
        --prefix "$STAGING_OBJECT_STORAGE_PREFIX"
        --release-sha "$(staging_release_sha)"
        --access-key-env "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV"
        --secret-key-env "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV"
        --manifest-out "${archive%.tar.gz}.manifest.json"
    )
    execute_args=()
    if [[ "$execute" == "1" ]]; then
        execute_args=(--execute)
    fi
    python3 "$PROJECT_DIR/scripts/staging_object_storage_artifact.py" "${manifest_args[@]}" "${execute_args[@]}"
}

staging_object_storage_probe() {
    require_cmd python3
    require_staging_object_storage_config
    python3 "$PROJECT_DIR/scripts/arvan_object_storage_probe.py" \
        --endpoint "$STAGING_OBJECT_STORAGE_ENDPOINT" \
        --bucket "$STAGING_OBJECT_STORAGE_BUCKET" \
        --prefix "$STAGING_OBJECT_STORAGE_PREFIX/probe" \
        --access-key-env "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV" \
        --secret-key-env "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV" \
        --execute
}

object_release_exclude_args() {
    local exclude
    for exclude in "${STAGING_OBJECT_RELEASE_PROJECT_EXCLUDES[@]}"; do
        case "$exclude" in
            __pycache__) printf '%s\n' "--exclude=__pycache__" ;;
            *'*'*|*'?'*|*'['*) printf '%s\n' "--exclude=$exclude" ;;
            *) printf '%s\n' "--exclude=./$exclude" ;;
        esac
    done
    local frontend_rel
    frontend_rel="$(staging_frontend_dist_relpath)"
    if [[ "$frontend_rel" != "mini_app_dist_staging" ]]; then
        printf '%s\n' "--exclude=./$frontend_rel"
    fi
}

object_release_rsync_exclude_args() {
    local exclude
    for exclude in "${STAGING_OBJECT_RELEASE_PROJECT_EXCLUDES[@]}" "${STAGING_OBJECT_RELEASE_RECEIVER_PROTECTED[@]}"; do
        printf '%s\n' "--exclude=$exclude"
    done
    local frontend_rel
    frontend_rel="$(staging_frontend_dist_relpath)"
    if [[ "$frontend_rel" != "mini_app_dist_staging" ]]; then
        printf '%s\n' "--exclude=$frontend_rel"
    fi
}

object_release_contract_args() {
    local item
    for item in "${STAGING_OBJECT_RELEASE_PROJECT_EXCLUDES[@]}"; do
        printf '%s\n' "--project-exclude=$item"
    done
    local frontend_rel
    frontend_rel="$(staging_frontend_dist_relpath)"
    if [[ "$frontend_rel" != "mini_app_dist_staging" ]]; then
        printf '%s\n' "--project-exclude=$frontend_rel"
    fi
    for item in "${STAGING_OBJECT_RELEASE_RECEIVER_PROTECTED[@]}"; do
        printf '%s\n' "--receiver-protect=$item"
    done
    printf '%s\n' "--transfer-note=Mirrors the old staging/direct deploy split: project payload, frontend dist, optional pip wheelhouse, optional docker image bundle, and receiver-local env by default."
    printf '%s\n' "--transfer-note=Runtime sync is intentionally not routed through Object Storage."
}

object_release_channel_slug() {
    python3 - "$1" <<'PY'
import re
import sys

slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(sys.argv[1] or "").strip())
print(slug.strip(".-") or "unknown")
PY
}

object_release_latest_pointer_path() {
    local channel="${1:-$STAGING_OBJECT_RELEASE_CHANNEL}"
    printf '%s/channels/%s/latest.json\n' "$STAGING_OBJECT_RELEASE_DOWNLOAD_DIR" "$(object_release_channel_slug "$channel")"
}

object_release_latest_release_sha() {
    local channel="${1:-$STAGING_OBJECT_RELEASE_CHANNEL}"
    local pointer
    pointer="$(object_release_latest_pointer_path "$channel")"
    [[ -f "$pointer" ]] || die "latest release pointer is missing; run object-release-fetch-latest first: $pointer"
    python3 - "$pointer" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
release_sha = payload.get("release_sha")
if not release_sha:
    raise SystemExit("latest release pointer is missing release_sha")
print(release_sha)
PY
}

staging_object_release_dir() {
    printf '%s/%s\n' "$STAGING_OBJECT_RELEASE_DIR" "$(release_sha)"
}

create_staging_object_release_project_archive() {
    require_cmd tar
    assert_staging_frontend_dist_isolated
    local release_dir archive
    release_dir="$(staging_object_release_dir)"
    mkdir -p "$release_dir"
    archive="$release_dir/trading-bot-staging-$(release_sha)-project-payload.tar.gz"
    log "packaging staging project payload at $archive" >&2
    mapfile -t exclude_args < <(object_release_exclude_args)
    tar -C "$PROJECT_DIR" -czf "$archive" "${exclude_args[@]}" .
    printf '%s\n' "$archive"
}

create_staging_object_release_frontend_archive() {
    require_cmd tar
    assert_staging_frontend_dist_isolated
    local release_dir archive frontend_rel
    release_dir="$(staging_object_release_dir)"
    mkdir -p "$release_dir"
    frontend_rel="$(staging_frontend_dist_relpath)"
    [[ -d "$PROJECT_DIR/$frontend_rel" ]] || die "staging frontend dist is missing: $PROJECT_DIR/$frontend_rel"
    archive="$release_dir/trading-bot-staging-$(release_sha)-frontend-dist.tar.gz"
    log "packaging staging frontend dist at $archive" >&2
    tar -C "$PROJECT_DIR" -czf "$archive" "$frontend_rel"
    printf '%s\n' "$archive"
}

create_staging_object_release_pip_archive() {
    require_cmd tar
    local pip_dir="$PROJECT_DIR/pip_packages"
    if [[ "$STAGING_OBJECT_RELEASE_INCLUDE_PIP" != "1" ]]; then
        return 0
    fi
    if [[ ! -d "$pip_dir" ]] || [[ -z "$(find "$pip_dir" -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' -o -name '.requirements_hash' \) -print -quit)" ]]; then
        log "skipping pip wheelhouse artifact because pip_packages is empty" >&2
        return 0
    fi
    local release_dir archive
    release_dir="$(staging_object_release_dir)"
    mkdir -p "$release_dir"
    archive="$release_dir/trading-bot-staging-$(release_sha)-pip-packages.tar.gz"
    log "packaging staging pip wheelhouse at $archive" >&2
    tar -C "$PROJECT_DIR" -czf "$archive" pip_packages
    printf '%s\n' "$archive"
}

create_staging_object_release_image_bundle() {
    if [[ "$STAGING_OBJECT_RELEASE_INCLUDE_IMAGES" != "1" ]]; then
        return 0
    fi
    require_cmd docker
    local release_dir archive image_tag
    release_dir="$(staging_object_release_dir)"
    mkdir -p "$release_dir"
    archive="$release_dir/trading-bot-staging-$(release_sha)-docker-images.tar"
    image_tag="${STAGING_IMAGE_TAG:-latest}"
    log "building staging app image and packaging docker image bundle at $archive" >&2
    compose build app >&2
    docker pull postgres:15-alpine >/dev/null
    docker pull redis:7-alpine >/dev/null
    docker save "trading_bot_staging_app:$image_tag" postgres:15-alpine redis:7-alpine -o "$archive"
    printf '%s\n' "$archive"
}

create_staging_object_release_env_artifact() {
    if [[ "$STAGING_OBJECT_RELEASE_INCLUDE_ENV" != "encrypted" ]]; then
        return 0
    fi
    require_cmd openssl
    local encryption_key="${!STAGING_OBJECT_RELEASE_ENV_KEY_ENV-}"
    [[ -n "$encryption_key" ]] || die "$STAGING_OBJECT_RELEASE_ENV_KEY_ENV is required when STAGING_OBJECT_RELEASE_INCLUDE_ENV=encrypted"
    local release_dir archive
    release_dir="$(staging_object_release_dir)"
    mkdir -p "$release_dir"
    archive="$release_dir/trading-bot-staging-$(release_sha)-env.staging.enc"
    log "encrypting staging runtime env at $archive" >&2
    STAGING_OBJECT_RELEASE_ENV_KEY="$encryption_key" \
        openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:STAGING_OBJECT_RELEASE_ENV_KEY -in "$ENV_FILE" -out "$archive"
    printf '%s\n' "$archive"
}

stage_object_storage_release() {
    local execute="${1:-0}"
    require_cmd python3
    require_staging_object_storage_config
    ensure_runtime_env_values
    build_frontend

    local project_archive frontend_archive pip_archive image_bundle env_artifact manifest_path release_dir
    release_dir="$(staging_object_release_dir)"
    mkdir -p "$release_dir"
    project_archive="$(create_staging_object_release_project_archive)"
    frontend_archive="$(create_staging_object_release_frontend_archive)"
    pip_archive="$(create_staging_object_release_pip_archive)"
    image_bundle="$(create_staging_object_release_image_bundle)"
    env_artifact="$(create_staging_object_release_env_artifact)"
    manifest_path="$release_dir/manifest.json"

    local manifest_args execute_args
    manifest_args=(
        upload
        --endpoint "$STAGING_OBJECT_STORAGE_ENDPOINT"
        --bucket "$STAGING_OBJECT_STORAGE_BUCKET"
        --prefix "$STAGING_OBJECT_STORAGE_PREFIX"
        --release-sha "$(release_sha)"
        --access-key-env "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV"
        --secret-key-env "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV"
        --manifest-out "$manifest_path"
        --publish-channel "$STAGING_OBJECT_RELEASE_CHANNEL"
        --artifact "project_payload=$project_archive"
        --artifact "frontend_dist=$frontend_archive"
    )
    if [[ -n "$pip_archive" ]]; then
        manifest_args+=(--artifact "pip_packages=$pip_archive")
    fi
    if [[ -n "$image_bundle" ]]; then
        manifest_args+=(--artifact "docker_images=$image_bundle")
    fi
    if [[ -n "$env_artifact" ]]; then
        manifest_args+=(--artifact "runtime_env_encrypted=$env_artifact")
    fi
    mapfile -t contract_args < <(object_release_contract_args)
    manifest_args+=("${contract_args[@]}")
    execute_args=()
    if [[ "$execute" == "1" ]]; then
        execute_args=(--execute)
    fi
    python3 "$PROJECT_DIR/scripts/staging_object_storage_release.py" "${manifest_args[@]}" "${execute_args[@]}"
}

stage_object_storage_release_fetch() {
    local release="${1:-$(release_sha)}"
    require_cmd python3
    require_staging_object_storage_config
    python3 "$PROJECT_DIR/scripts/staging_object_storage_release.py" \
        fetch \
        --endpoint "$STAGING_OBJECT_STORAGE_ENDPOINT" \
        --bucket "$STAGING_OBJECT_STORAGE_BUCKET" \
        --prefix "$STAGING_OBJECT_STORAGE_PREFIX" \
        --release-sha "$release" \
        --access-key-env "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV" \
        --secret-key-env "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV" \
        --download-dir "$STAGING_OBJECT_RELEASE_DOWNLOAD_DIR" \
        --execute
}

stage_object_storage_release_fetch_latest() {
    local channel="${1:-$STAGING_OBJECT_RELEASE_CHANNEL}"
    require_cmd python3
    require_staging_object_storage_config
    python3 "$PROJECT_DIR/scripts/staging_object_storage_release.py" \
        fetch-latest \
        --endpoint "$STAGING_OBJECT_STORAGE_ENDPOINT" \
        --bucket "$STAGING_OBJECT_STORAGE_BUCKET" \
        --prefix "$STAGING_OBJECT_STORAGE_PREFIX" \
        --channel "$channel" \
        --access-key-env "$STAGING_OBJECT_STORAGE_ACCESS_KEY_ENV" \
        --secret-key-env "$STAGING_OBJECT_STORAGE_SECRET_KEY_ENV" \
        --download-dir "$STAGING_OBJECT_RELEASE_DOWNLOAD_DIR" \
        --execute
}

manifest_artifact_field() {
    local manifest="$1"
    local artifact="$2"
    local field="$3"
    python3 - "$manifest" "$artifact" "$field" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
artifact = manifest.get("artifacts", {}).get(sys.argv[2])
if not artifact:
    raise SystemExit(3)
print(artifact.get(sys.argv[3], ""))
PY
}

verify_downloaded_artifact() {
    local manifest="$1"
    local artifact="$2"
    local path="$3"
    [[ -f "$path" ]] || die "downloaded artifact is missing: $path"
    local expected_sha expected_size actual_sha actual_size
    expected_sha="$(manifest_artifact_field "$manifest" "$artifact" sha256)"
    expected_size="$(manifest_artifact_field "$manifest" "$artifact" size_bytes)"
    actual_sha="$(sha256sum "$path" | awk '{print $1}')"
    actual_size="$(wc -c <"$path" | tr -d ' ')"
    [[ "$actual_sha" == "$expected_sha" ]] || die "$artifact sha256 mismatch"
    [[ "$actual_size" == "$expected_size" ]] || die "$artifact size mismatch"
}

stage_object_storage_release_apply() {
    local release="${1:-$(release_sha)}"
    require_cmd python3
    require_cmd tar
    require_cmd rsync
    local release_download_dir="$STAGING_OBJECT_RELEASE_DOWNLOAD_DIR/$release"
    local manifest="$release_download_dir/manifest.json"
    [[ -f "$manifest" ]] || die "release manifest is missing; run object-release-fetch first: $manifest"
    local project_payload frontend_dist pip_packages docker_images apply_dir
    project_payload="$release_download_dir/$(manifest_artifact_field "$manifest" project_payload filename)"
    frontend_dist="$release_download_dir/$(manifest_artifact_field "$manifest" frontend_dist filename)"
    pip_packages="$(manifest_artifact_field "$manifest" pip_packages filename 2>/dev/null || true)"
    docker_images="$(manifest_artifact_field "$manifest" docker_images filename 2>/dev/null || true)"
    apply_dir="$(realpath -m "$STAGING_OBJECT_RELEASE_APPLY_DIR")"
    verify_downloaded_artifact "$manifest" project_payload "$project_payload"
    verify_downloaded_artifact "$manifest" frontend_dist "$frontend_dist"
    if [[ -n "$pip_packages" ]]; then
        pip_packages="$release_download_dir/$pip_packages"
        verify_downloaded_artifact "$manifest" pip_packages "$pip_packages"
    fi
    if [[ -n "$docker_images" ]]; then
        docker_images="$release_download_dir/$docker_images"
        verify_downloaded_artifact "$manifest" docker_images "$docker_images"
    fi
    if [[ "$STAGING_OBJECT_RELEASE_APPLY_EXECUTE" != "1" ]]; then
        log "dry-run object release apply release=$release apply_dir=$apply_dir"
        log "would rsync project payload with direct-deploy excludes and protected receiver paths"
        log "would replace frontend dist at $STAGING_FRONTEND_DIST_DIR"
        [[ -n "$pip_packages" ]] && log "would replace pip wheelhouse from $pip_packages"
        [[ -n "$docker_images" ]] && log "would docker load image bundle from $docker_images"
        return 0
    fi

    local extract_dir project_extract frontend_extract pip_extract frontend_rel
    extract_dir="$release_download_dir/apply"
    project_extract="$extract_dir/project"
    frontend_extract="$extract_dir/frontend"
    pip_extract="$extract_dir/pip"
    rm -rf "$extract_dir"
    mkdir -p "$project_extract" "$frontend_extract"
    tar -xzf "$project_payload" -C "$project_extract"
    mapfile -t rsync_exclude_args < <(object_release_rsync_exclude_args)
    rsync -a --delete "${rsync_exclude_args[@]}" "$project_extract/." "$apply_dir/"

    tar -xzf "$frontend_dist" -C "$frontend_extract"
    frontend_rel="$(staging_frontend_dist_relpath)"
    mkdir -p "$apply_dir/$frontend_rel"
    rsync -a --delete "$frontend_extract/$frontend_rel/" "$apply_dir/$frontend_rel/"

    if [[ -n "$pip_packages" ]]; then
        mkdir -p "$pip_extract"
        tar -xzf "$pip_packages" -C "$pip_extract"
        mkdir -p "$apply_dir/pip_packages"
        rsync -a --delete "$pip_extract/pip_packages/" "$apply_dir/pip_packages/"
    fi
    if [[ -n "$docker_images" ]]; then
        require_cmd docker
        docker load -i "$docker_images"
    fi
    if [[ "$STAGING_OBJECT_RELEASE_DEPLOY_AFTER_APPLY" == "1" ]]; then
        compose up -d --no-build
        wait_for_app_health
        compose ps
    fi
    log "object release $release applied to $apply_dir"
}

stage_object_storage_release_apply_latest() {
    local channel="${1:-$STAGING_OBJECT_RELEASE_CHANNEL}"
    local release
    stage_object_storage_release_fetch_latest "$channel"
    release="$(object_release_latest_release_sha "$channel")"
    stage_object_storage_release_apply "$release"
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
        compose --profile staging-bot up -d --build foreign_sync_worker
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
    object-storage-configure)
        configure_staging_object_storage_env
        ;;
    object-storage-probe)
        staging_object_storage_probe
        ;;
    object-storage-package)
        stage_object_storage_artifact 0
        ;;
    object-storage-upload)
        stage_object_storage_artifact 1
        ;;
    object-release-package)
        stage_object_storage_release 0
        ;;
    object-release-upload)
        stage_object_storage_release 1
        ;;
    object-release-fetch)
        stage_object_storage_release_fetch "${2:-$(release_sha)}"
        ;;
    object-release-fetch-latest)
        stage_object_storage_release_fetch_latest "${2:-$STAGING_OBJECT_RELEASE_CHANNEL}"
        ;;
    object-release-apply)
        stage_object_storage_release_apply "${2:-$(release_sha)}"
        ;;
    object-release-apply-latest)
        stage_object_storage_release_apply_latest "${2:-$STAGING_OBJECT_RELEASE_CHANNEL}"
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
  object-storage-configure
                  Store staging Object Storage endpoint/bucket/prefix in .env.staging
  object-storage-probe
                  Run staging-only Object Storage PUT/GET/LIST/DELETE probe
  object-storage-package
                  Build staging frontend, package deploy artifact, and write local manifest
  object-storage-upload
                  Build/package and upload staging deploy artifact to Object Storage
  object-release-package
                  Build staging release artifacts and write a multi-artifact manifest
  object-release-upload
                  Build/upload project, frontend, optional pip/image/env artifacts and manifest
  object-release-fetch [release]
                  Download and verify a release manifest/artifacts from Object Storage
  object-release-fetch-latest [channel]
                  Resolve the channel latest pointer, then download and verify that release
  object-release-apply [release]
                  Dry-run apply a fetched release; set STAGING_OBJECT_RELEASE_APPLY_EXECUTE=1 to apply
  object-release-apply-latest [channel]
                  Fetch latest for a channel, then dry-run/apply that release
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
