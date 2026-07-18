#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/monitoring/docker-compose.monitoring.yml"
ENV_FILE="$REPO_ROOT/deploy/monitoring/.env.monitoring.local"
PROJECT_NAME="trading_bot_monitoring_dev"
MONITORING_BRANCH="feature/admin-market-monitoring-channel"
DEFAULT_APP_PORT="18100"
RESET_CONFIRMATION="RESET trading_bot_monitoring_dev"

log() {
    printf '[monitoring-dev] %s\n' "$*"
}

die() {
    printf '[monitoring-dev] ERROR: %s\n' "$*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

secret_hex() {
    openssl rand -hex "$1"
}

env_value() {
    local key="$1"
    awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

assert_feature_worktree() {
    local branch
    branch="$(git -C "$REPO_ROOT" branch --show-current)"
    [[ -n "$branch" ]] || die "detached HEAD is not allowed for this stack"
    [[ "$branch" == "$MONITORING_BRANCH" ]] || die "this stack may only run from $MONITORING_BRANCH (current: $branch)"
}

ensure_env() {
    [[ -f "$ENV_FILE" ]] && return

    require_cmd openssl
    local db_password jwt_secret sync_key dev_key observability_key hash_salt release_sha
    db_password="$(secret_hex 24)"
    jwt_secret="$(secret_hex 48)"
    sync_key="$(secret_hex 32)"
    dev_key="$(secret_hex 32)"
    observability_key="$(secret_hex 32)"
    hash_salt="$(secret_hex 32)"
    release_sha="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD)"

    umask 077
    install -m 0600 /dev/null "$ENV_FILE"
    {
        printf 'COMPOSE_PROJECT_NAME=%s\n' "$PROJECT_NAME"
        printf 'MONITORING_APP_PORT=%s\n' "$DEFAULT_APP_PORT"
        printf 'MONITORING_IMAGE_TAG=%s\n' "$release_sha"
        printf 'MONITORING_FRONTEND_DIST_DIR=mini_app_dist\n'
        printf '\n'
        printf 'ENVIRONMENT=development\n'
        printf 'SERVER_MODE=foreign\n'
        printf 'RELEASE_SHA=%s\n' "$release_sha"
        printf 'FRONTEND_URL=http://127.0.0.1:%s\n' "$DEFAULT_APP_PORT"
        printf 'PUBLIC_WEBAPP_URL=http://127.0.0.1:%s\n' "$DEFAULT_APP_PORT"
        printf 'EXTRA_CORS_ORIGINS=http://127.0.0.1:%s\n' "$DEFAULT_APP_PORT"
        printf 'TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.16.0.0/12\n'
        printf '\n'
        printf 'DATABASE_URL=postgresql+asyncpg://monitoring_dev:%s@db:5432/trading_bot_monitoring_dev\n' "$db_password"
        printf 'SYNC_DATABASE_URL=postgresql://monitoring_dev:%s@db:5432/trading_bot_monitoring_dev\n' "$db_password"
        printf 'POSTGRES_DB=trading_bot_monitoring_dev\n'
        printf 'POSTGRES_USER=monitoring_dev\n'
        printf 'POSTGRES_PASSWORD=%s\n' "$db_password"
        printf '\n'
        printf 'REDIS_URL=redis://redis:6379/0\n'
        printf 'REDIS_HOST=redis\n'
        printf 'REDIS_PORT=6379\n'
        printf 'REDIS_APPENDONLY=yes\n'
        printf 'REDIS_APPENDFSYNC=everysec\n'
        printf 'REDIS_MAXMEMORY=0\n'
        printf 'REDIS_MAXMEMORY_POLICY=noeviction\n'
        printf '\n'
        printf 'JWT_SECRET_KEY=%s\n' "$jwt_secret"
        printf 'SYNC_API_KEY=%s\n' "$sync_key"
        printf 'SYNC_VERIFY_TLS=false\n'
        printf 'DEV_API_KEY=%s\n' "$dev_key"
        printf 'OBSERVABILITY_API_KEY=%s\n' "$observability_key"
        printf 'OBSERVABILITY_TELEGRAM_USER_HASH_SALT=%s\n' "$hash_salt"
        printf 'AUDIT_TRAIL_PATH=/app/audit_trail/audit.jsonl\n'
        printf '\n'
        printf 'DB_POOL_SIZE=5\n'
        printf 'DB_MAX_OVERFLOW=5\n'
        printf 'API_WORKERS=1\n'
        printf 'BACKGROUND_JOBS_ENABLED=false\n'
        printf 'WEB_PUSH_ENABLED=false\n'
        printf 'TELEGRAM_DIRECT_REGISTRATION_ENABLED=false\n'
        printf 'TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED=false\n'
        printf 'TELEGRAM_LOGIN_OTP_ENABLED=false\n'
        printf 'OTP_SMS_AUTO_FALLBACK_ENABLED=false\n'
        printf 'INVITATION_SMS_STANDARD_ENABLED=false\n'
        printf 'INVITATION_SMS_CUSTOMER_TIER1_ENABLED=false\n'
        printf 'INVITATION_SMS_ACCOUNTANT_ENABLED=false\n'
        printf 'INVITATION_SMS_CUSTOMER_TIER2_ENABLED=false\n'
        printf 'ERROR_TRACKING_DSN=\n'
        printf 'PEER_SERVER_URL=\n'
        printf 'FOREIGN_SERVER_URL=\n'
        printf 'IRAN_SERVER_URL=\n'
        printf 'GERMANY_SERVER_URL=\n'
        printf '\n'
        printf 'BOT_TOKEN=\n'
        printf 'BOT_USERNAME=monitoring_dev_bot_placeholder\n'
        printf 'CHANNEL_ID=\n'
        printf 'TELEGRAM_MONITORING_CHANNEL_ENABLED=false\n'
        printf 'TELEGRAM_MONITORING_BOT_TOKEN=\n'
        printf 'TELEGRAM_MONITORING_CHANNEL_ID=\n'
        printf 'MONITORING_TELEGRAM_LIVE_ACK=\n'
    } >"$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    log "created isolated env file: $ENV_FILE"
}

validate_isolation_env() {
    [[ "$(env_value COMPOSE_PROJECT_NAME)" == "$PROJECT_NAME" ]] || die "unexpected Compose project"
    [[ "$(env_value ENVIRONMENT)" == "development" ]] || die "environment must remain development"
    [[ "$(env_value SERVER_MODE)" == "foreign" ]] || die "server mode must remain foreign"
    [[ "$(env_value POSTGRES_DB)" == "trading_bot_monitoring_dev" ]] || die "unexpected database name"
    [[ "$(env_value POSTGRES_USER)" == "monitoring_dev" ]] || die "unexpected database user"
    [[ "$(env_value DATABASE_URL)" == *"@db:5432/trading_bot_monitoring_dev" ]] || die "database URL is not isolated"
    [[ "$(env_value SYNC_DATABASE_URL)" == *"@db:5432/trading_bot_monitoring_dev" ]] || die "sync database URL is not isolated"
    [[ "$(env_value REDIS_URL)" == "redis://redis:6379/0" ]] || die "Redis URL is not isolated"
    [[ "$(env_value BACKGROUND_JOBS_ENABLED)" == "false" ]] || die "background jobs must be disabled by default"
}

validate_safe_default_env() {
    [[ "$(env_value TELEGRAM_MONITORING_CHANNEL_ENABLED)" == "false" ]] || die "Telegram monitoring must be disabled by default"
    [[ -z "$(env_value BOT_TOKEN)" ]] || die "default environment must not contain a bot token"
    [[ -z "$(env_value CHANNEL_ID)" ]] || die "default environment must not contain a primary channel ID"
    [[ -z "$(env_value TELEGRAM_MONITORING_BOT_TOKEN)" ]] || die "default environment must not contain a monitoring bot token"
    [[ -z "$(env_value TELEGRAM_MONITORING_CHANNEL_ID)" ]] || die "default environment must not contain a monitoring channel ID"
}

compose() {
    local release_sha image_tag
    assert_feature_worktree
    release_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    image_tag="${release_sha:0:12}"
    MONITORING_RELEASE_SHA="$release_sha" MONITORING_IMAGE_TAG="$image_tag" docker compose \
        --project-name "$PROJECT_NAME" \
        --env-file "$ENV_FILE" \
        --file "$COMPOSE_FILE" \
        "$@"
}

validate_compose_contract() {
    compose config --quiet
    ! grep -Eq '^[[:space:]]*container_name:' "$COMPOSE_FILE" || die "fixed container names are forbidden"
    ! grep -Eq '^[[:space:]]*external:[[:space:]]*true' "$COMPOSE_FILE" || die "external networks or volumes are forbidden"
    grep -Fq '127.0.0.1:${MONITORING_APP_PORT:-18100}:8000' "$COMPOSE_FILE" || die "API port must be loopback-only"
}

check_contract() {
    assert_feature_worktree
    ensure_env
    validate_isolation_env
    validate_safe_default_env
    validate_compose_contract
    log "isolation contract is valid"
}

build_stack() {
    check_contract
    # All application services share the same immutable branch image. Building
    # it once avoids concurrent writers racing to publish the same local tag.
    compose build app
}

migrate_stack() {
    check_contract
    compose up -d --wait db redis
    compose run --rm --no-deps migration
}

smoke_stack() {
    check_contract
    local expected_head actual_head api_status app_port
    expected_head="$(compose run --rm --no-deps migration sh -lc "python -m alembic heads | sed -n 's/ .*//p'")"
    actual_head="$(compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version ORDER BY version_num"')"
    [[ -n "$expected_head" && "$actual_head" == "$expected_head" ]] || die "migration head mismatch: expected=$expected_head actual=$actual_head"
    [[ "$(compose exec -T redis redis-cli ping)" == "PONG" ]] || die "isolated Redis did not answer PONG"
    api_status="$(compose exec -T app python -c "import urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=10); print(response.status)")"
    [[ "$api_status" == "200" ]] || die "isolated API health check failed: status=$api_status"
    app_port="$(env_value MONITORING_APP_PORT)"
    log "smoke passed: project=$PROJECT_NAME head=$actual_head internal_api=$api_status loopback_port=$app_port"
}

up_stack() {
    build_stack
    migrate_stack
    compose up -d --wait app
    smoke_stack
}

telegram_up() {
    assert_feature_worktree
    ensure_env
    validate_isolation_env
    validate_compose_contract
    [[ "$(env_value MONITORING_TELEGRAM_LIVE_ACK)" == "DEDICATED_NON_PRODUCTION_TELEGRAM" ]] || die "Telegram profile acknowledgement is missing"
    [[ -n "$(env_value BOT_TOKEN)" ]] || die "dedicated BOT_TOKEN is required"
    [[ -z "$(env_value CHANNEL_ID)" ]] || die "primary CHANNEL_ID must stay empty in the monitoring stack"
    [[ -n "$(env_value TELEGRAM_MONITORING_BOT_TOKEN)" ]] || die "dedicated monitoring bot token is required"
    [[ "$(env_value BOT_TOKEN)" != "$(env_value TELEGRAM_MONITORING_BOT_TOKEN)" ]] || die "primary and monitoring bot tokens must be different"
    [[ -n "$(env_value TELEGRAM_MONITORING_CHANNEL_ID)" ]] || die "dedicated monitoring channel ID is required"
    [[ "$(env_value TELEGRAM_MONITORING_CHANNEL_ENABLED)" == "true" ]] || die "monitoring channel flag must be true"
    compose build app
    compose up -d --wait db redis
    compose run --rm --no-deps migration
    compose up -d --wait app
    compose --profile monitoring-telegram up -d bot
}

usage() {
    cat <<EOF
Usage: scripts/monitoring_dev_stack.sh COMMAND

Commands:
  init          create the ignored 0600 environment file
  check         validate isolation and Compose configuration
  build         build a branch-specific application image
  migrate       start isolated DB/Redis and migrate the isolated database
  up            build, migrate, start the API, and run smoke checks
  smoke         verify Alembic head, Redis, and the internal API health path
  ps            show only this Compose project
  logs          follow logs for this Compose project
  down          stop this project and preserve its volumes
  telegram-up   opt-in start using dedicated non-production Telegram values
  purge PHRASE  delete only this project's containers and volumes

Purge phrase: $RESET_CONFIRMATION
EOF
}

command_name="${1:-}"
case "$command_name" in
    init)
        assert_feature_worktree
        ensure_env
        validate_isolation_env
        validate_safe_default_env
        ;;
    check)
        check_contract
        ;;
    build)
        build_stack
        ;;
    migrate)
        migrate_stack
        ;;
    up)
        up_stack
        ;;
    smoke)
        smoke_stack
        ;;
    ps)
        ensure_env
        compose ps
        ;;
    logs)
        ensure_env
        compose logs --tail=200 --follow
        ;;
    down)
        ensure_env
        compose down --remove-orphans
        ;;
    telegram-up)
        telegram_up
        ;;
    purge)
        ensure_env
        [[ "${2:-}" == "$RESET_CONFIRMATION" ]] || die "exact purge phrase required: $RESET_CONFIRMATION"
        compose down --volumes --remove-orphans
        ;;
    *)
        usage
        exit 2
        ;;
esac
