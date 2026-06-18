#!/bin/bash
set -e

# ==========================================
# 🚀 Deploy Script — Two-Server Architecture
# ==========================================
# Foreign Server (Germany): Bot + Sync + API
# Iran Server:              API + Nginx + Frontend
# ==========================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
FRONTEND_DIR="$PROJECT_DIR/frontend"
DIST_DIR="$PROJECT_DIR/mini_app_dist"
DEPLOY_STATE_DIR="$PROJECT_DIR/tmp/deploy-state"
FRONTEND_SIGNATURE_FILE="$DEPLOY_STATE_DIR/frontend-build.signature"
FOREIGN_IMAGE_SIGNATURE_FILE="$DEPLOY_STATE_DIR/foreign-image.signature"
PIP_BOOTSTRAP_REQUIREMENTS="$PROJECT_DIR/deploy/production/pip-bootstrap-requirements.txt"
TIMEZONE_SCRIPT="$PROJECT_DIR/scripts/ensure_host_timezone.sh"
SYNC_RECOVERY_SCRIPT="$PROJECT_DIR/scripts/recover_cross_server_sync.sh"
DEPLOY_CONFIG_SCRIPT="$PROJECT_DIR/scripts/deploy_config.py"
FOREIGN_HOST_TIMEZONE="${FOREIGN_HOST_TIMEZONE:-UTC}"
IRAN_HOST_TIMEZONE="${IRAN_HOST_TIMEZONE:-UTC}"
AUTO_SYNC_RECOVERY_ON_FULL_DEPLOY="${AUTO_SYNC_RECOVERY_ON_FULL_DEPLOY:-1}"
DEPLOY_FORCE_REBUILD="${DEPLOY_FORCE_REBUILD:-${IRAN_FORCE_RELEASE_REFRESH:-0}}"
FOREIGN_COMPOSE_PROJECT_NAME="${FOREIGN_COMPOSE_PROJECT_NAME:-trading_bot}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$FOREIGN_COMPOSE_PROJECT_NAME}"
LOCAL_COMPOSE_CMD=""

normalize_arch() {
    case "${1:-}" in
        x86_64|amd64) printf 'amd64\n' ;;
        aarch64|arm64) printf 'arm64\n' ;;
        *) echo "Unsupported architecture: $1" >&2; exit 1 ;;
    esac
}

resolve_local_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        LOCAL_COMPOSE_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        LOCAL_COMPOSE_CMD="docker-compose"
    else
        echo "No Docker Compose command is available locally." >&2
        exit 1
    fi
}

append_pip_platform_args() {
    case "$(normalize_arch "$1")" in
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

hash_release_inputs() {
    sha256sum | cut -d' ' -f1
}

IRAN_HOST="${IRAN_HOST:-}"
IRAN_USER="${IRAN_USER:-}"
IRAN_SSH_PORT="${IRAN_SSH_PORT:-}"
IRAN_PROJECT_DIR="${IRAN_PROJECT_DIR:-}"

load_shared_deploy_surface() {
    if [[ -f "$DEPLOY_CONFIG_SCRIPT" ]]; then
        local explicit_iran_user="${IRAN_USER:-}"
        local shell_exports
        shell_exports="$(python3 "$DEPLOY_CONFIG_SCRIPT" --format shell 2>/dev/null || true)"
        if [[ -n "$shell_exports" ]]; then
            # shellcheck disable=SC1090
            eval "$shell_exports"
            IRAN_USER="${explicit_iran_user:-${IRAN_SSH_USER:-${IRAN_USER:-}}}"
            IRAN_PROJECT_DIR="${IRAN_PROJECT_DIR:-${IRAN_DIR:-}}"
        fi
    fi
    : "${IRAN_HOST:?IRAN_HOST is required. Define it in DEPLOY_MANIFEST or environment.}"
    : "${IRAN_USER:?IRAN_USER/IRAN_SSH_USER is required. Define it in DEPLOY_MANIFEST or environment.}"
    : "${IRAN_SSH_PORT:?IRAN_SSH_PORT is required. Define it in DEPLOY_MANIFEST or environment.}"
    : "${IRAN_PROJECT_DIR:?IRAN_PROJECT_DIR is required. Define it in DEPLOY_MANIFEST or environment.}"
}

load_shared_deploy_surface

# ==========================================
# Helper Functions
# ==========================================
ssh_iran() {
    ssh -o StrictHostKeyChecking=no -p "$IRAN_SSH_PORT" "$IRAN_USER@$IRAN_HOST" "$@"
}

scp_iran() {
    scp -r -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=no "$@"
}

ensure_local_host_timezone() {
    print_header "🕒 Ensuring local host timezone (${FOREIGN_HOST_TIMEZONE})"
    bash "$TIMEZONE_SCRIPT" "$FOREIGN_HOST_TIMEZONE"
}

ensure_iran_host_timezone() {
    print_header "🕒 Ensuring Iran host timezone (${IRAN_HOST_TIMEZONE})"
    ssh_iran "bash -s -- '$IRAN_HOST_TIMEZONE'" < "$TIMEZONE_SCRIPT"
}

print_header() {
    echo ""
    echo "============================================"
    echo "  $1"
    echo "============================================"
}

resource_guard_enabled() {
    [ "${DEPLOY_RESOURCE_GUARD_ENABLED:-1}" != "0" ]
}

sample_cpu_usage() {
    read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
    local total=$((user + nice + system + idle + iowait + irq + softirq + steal))
    local idle_all=$((idle + iowait))
    echo "$total $idle_all"
}

sample_memory_usage() {
    awk '
        /^MemTotal:/ {mt=$2}
        /^MemAvailable:/ {ma=$2}
        /^SwapTotal:/ {st=$2}
        /^SwapFree:/ {sf=$2}
        END {
            mu=mt-ma
            mp=(mt>0)?(mu*100/mt):0
            su=st-sf
            sp=(st>0)?(su*100/st):0
            printf "%d %d %d %d %d %d\n", mt, ma, mu, mp, st, sp
        }
    ' /proc/meminfo
}

terminate_guarded_process() {
    local cmd_pid="$1"
    pkill -TERM -P "$cmd_pid" 2>/dev/null || true
    kill -TERM "$cmd_pid" 2>/dev/null || true
    sleep 5
    pkill -KILL -P "$cmd_pid" 2>/dev/null || true
    kill -KILL "$cmd_pid" 2>/dev/null || true
}

run_with_local_resource_guard() {
    local label="$1"
    shift

    if ! resource_guard_enabled; then
        "$@"
        return $?
    fi

    local sample_seconds="${DEPLOY_RESOURCE_GUARD_SAMPLE_SECONDS:-5}"
    local max_streak="${DEPLOY_RESOURCE_GUARD_MAX_STREAK:-4}"
    local max_mem_percent="${DEPLOY_RESOURCE_GUARD_MAX_MEM_PERCENT:-95}"
    local max_swap_percent="${DEPLOY_RESOURCE_GUARD_MAX_SWAP_PERCENT:-70}"
    local min_mem_available_kb="${DEPLOY_RESOURCE_GUARD_MIN_MEM_AVAILABLE_KB:-262144}"
    local cpu_with_high_mem_percent="${DEPLOY_RESOURCE_GUARD_CPU_WITH_HIGH_MEM_PERCENT:-97}"
    local cpu_only_percent="${DEPLOY_RESOURCE_GUARD_CPU_ONLY_PERCENT:-99}"
    local cpu_only_max_streak="${DEPLOY_RESOURCE_GUARD_CPU_ONLY_MAX_STREAK:-12}"
    local sample_index=0
    local pressure_streak=0
    local cpu_only_streak=0
    local prev_total prev_idle

    print_header "🛡️ Resource Guard: $label"
    echo "   sample=${sample_seconds}s mem>=${max_mem_percent}% swap>=${max_swap_percent}% cpu>=${cpu_only_percent}%"

    "$@" &
    local cmd_pid=$!
    read -r prev_total prev_idle <<EOF
$(sample_cpu_usage)
EOF

    while kill -0 "$cmd_pid" 2>/dev/null; do
        sleep "$sample_seconds"
        sample_index=$((sample_index + 1))

        local total idle total_delta idle_delta cpu_percent
        read -r total idle <<EOF
$(sample_cpu_usage)
EOF
        total_delta=$((total - prev_total))
        idle_delta=$((idle - prev_idle))
        prev_total=$total
        prev_idle=$idle
        if [ "$total_delta" -le 0 ]; then
            cpu_percent=0
        else
            cpu_percent=$(((1000 * (total_delta - idle_delta) / total_delta + 5) / 10))
        fi

        local mem_total mem_available mem_used mem_percent swap_total swap_percent
        read -r mem_total mem_available mem_used mem_percent swap_total swap_percent <<EOF
$(sample_memory_usage)
EOF

        echo "   [guard] t=$((sample_index * sample_seconds))s cpu=${cpu_percent}% mem=${mem_percent}% avail=$((mem_available / 1024))MB swap=${swap_percent}%"

        if [ "$mem_available" -lt "$min_mem_available_kb" ] \
            || [ "$mem_percent" -ge "$max_mem_percent" ] \
            || [ "$swap_percent" -ge "$max_swap_percent" ] \
            || { [ "$cpu_percent" -ge "$cpu_with_high_mem_percent" ] && [ "$mem_percent" -ge "$((max_mem_percent - 2))" ]; }; then
            pressure_streak=$((pressure_streak + 1))
        else
            pressure_streak=0
        fi

        if [ "$cpu_percent" -ge "$cpu_only_percent" ]; then
            cpu_only_streak=$((cpu_only_streak + 1))
        else
            cpu_only_streak=0
        fi

        if [ "$pressure_streak" -ge "$max_streak" ] || [ "$cpu_only_streak" -ge "$cpu_only_max_streak" ]; then
            echo "❌ Resource guard triggered for '$label'. Stopping the running command to protect the server."
            terminate_guarded_process "$cmd_pid"
            wait "$cmd_pid"
            return 124
        fi
    done

    wait "$cmd_pid"
}

hash_file_or_dir() {
    local rel="$1"
    local path="$PROJECT_DIR/$rel"
    if [[ -f "$path" ]]; then
        sha256sum "$path" | sed "s#  $PROJECT_DIR/#  #"
    elif [[ -d "$path" ]]; then
        (cd "$PROJECT_DIR" && find "$rel" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum)
    fi
}

frontend_build_signature() {
    {
        printf 'node=%s\n' "$(node -p 'process.versions.node' 2>/dev/null || true)"
        printf 'npm=%s\n' "$(npm --version 2>/dev/null || true)"
        env | LC_ALL=C sort | grep -E '^(VITE_|BASE_URL=|NODE_ENV=)' || true
        local rel
        for rel in \
            frontend/package.json \
            frontend/package-lock.json \
            frontend/vite.config.ts \
            frontend/tsconfig.json \
            frontend/tsconfig.app.json \
            frontend/tsconfig.node.json \
            frontend/postcss.config.js \
            frontend/tailwind.config.js \
            frontend/index.html \
            frontend/public \
            frontend/src
        do
            hash_file_or_dir "$rel"
        done
    } | hash_release_inputs
}

foreign_image_signature() {
    {
        printf 'docker_image=%s\n' "trading_bot_base"
        local rel
        for rel in \
            Dockerfile \
            .dockerignore \
            requirements.txt \
            pip_packages \
            api \
            bot \
            core \
            src \
            migrations \
            models \
            templates \
            fonts \
            alembic.ini \
            main.py \
            manage.py \
            run_bot.py \
            schemas.py \
            seed_fake_data.py \
            scripts \
            mini_app_dist
        do
            hash_file_or_dir "$rel"
        done
    } | hash_release_inputs
}

# ==========================================
# Auto Cleanup Logic (Every 10 deploys)
# ==========================================
auto_cleanup_local() {
    COUNT_FILE="$PROJECT_DIR/.deploy_count"
    COUNT=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
    COUNT=$((COUNT + 1))
    
    if [ "$COUNT" -ge 10 ]; then
        print_header "🧹 Auto-cleanup: Reclaiming local space"
        docker system prune -f
        echo 0 > "$COUNT_FILE"
    else
        echo "$COUNT" > "$COUNT_FILE"
        echo "📊 Local Deployment count: $COUNT/10 (next cleanup in $((10 - COUNT)) builds)"
    fi
}

auto_cleanup_iran() {
    print_header "🧹 Checking Iran server for auto-cleanup"
    ssh_iran "cd $IRAN_PROJECT_DIR && \
        COUNT=\$(cat .deploy_count 2>/dev/null || echo 0); \
        COUNT=\$((COUNT + 1)); \
        if [ \"\$COUNT\" -ge 10 ]; then \
            echo 'Reclaiming space on Iran server...'; \
            docker system prune -f; \
            echo 0 > .deploy_count; \
        else \
            echo \$COUNT > .deploy_count; \
            echo \"Iran Deployment count: \$COUNT/10\"; \
        fi"
}

# ==========================================
# Parse Arguments
# ==========================================
TARGET="${1:-all}"  # all | frontend | foreign | iran

print_header "🚀 Deploy: $TARGET"

# ==========================================
# 1. Frontend Build (shared step)
# ==========================================
build_frontend() {
    print_header "📦 Building Frontend"
    mkdir -p "$DEPLOY_STATE_DIR"
    local frontend_signature
    frontend_signature="$(frontend_build_signature)"
    if [ "$DEPLOY_FORCE_REBUILD" != "1" ] && [ -f "$FRONTEND_SIGNATURE_FILE" ] && [ "$(cat "$FRONTEND_SIGNATURE_FILE")" = "$frontend_signature" ] && [ -f "$DIST_DIR/index.html" ]; then
        echo "✅ Frontend build inputs unchanged. Skipping npm install/build."
        chmod -R 755 "$DIST_DIR"
        cd "$PROJECT_DIR"
        return 0
    fi
    run_with_local_resource_guard "Frontend build" bash -lc "cd \"$FRONTEND_DIR\" && if [ -f package-lock.json ]; then npm ci --silent; else npm install --silent; fi && NODE_OPTIONS=\"--max-old-space-size=1024\" npm run build"

    if [ ! -d "$DIST_DIR" ]; then
        echo "❌ Build directory ($DIST_DIR) not found!"
        exit 1
    fi

    chmod -R 755 "$DIST_DIR"
    echo "$frontend_signature" > "$FRONTEND_SIGNATURE_FILE"
    echo "✅ Frontend build successful!"
    cd "$PROJECT_DIR"
}

# ==========================================
# 1.5. Prepare Pip Packages (Germany only)
# ==========================================
prepare_pip_packages() {
    print_header "📦 Checking pip dependencies"
    
    HASH_FILE="$PROJECT_DIR/pip_packages/.requirements_hash"
    LOCAL_ARCH="$(normalize_arch "$(dpkg --print-architecture)")"
    CURRENT_HASH="$(
        {
            md5sum "$PROJECT_DIR/requirements.txt"
            if [ -f "$PIP_BOOTSTRAP_REQUIREMENTS" ]; then
                md5sum "$PIP_BOOTSTRAP_REQUIREMENTS"
            fi
        } | md5sum | cut -d' ' -f1
    )-$LOCAL_ARCH"
    
    if [ "$DEPLOY_FORCE_REBUILD" = "1" ] || [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$CURRENT_HASH" ] || [ ! -d "$PROJECT_DIR/pip_packages" ]; then
        echo "🔄 requirements.txt changed or packages missing. Downloading..."
        mkdir -p "$PROJECT_DIR/pip_packages"
        rm -f "$PROJECT_DIR"/pip_packages/*.whl "$PROJECT_DIR"/pip_packages/*.tar.gz "$PROJECT_DIR"/pip_packages/*.zip "$PROJECT_DIR/pip_packages/.requirements_hash" 2>/dev/null || true
        mapfile -t PIP_PLATFORM_ARGS < <(append_pip_platform_args "$LOCAL_ARCH")

        if [ -f "$PIP_BOOTSTRAP_REQUIREMENTS" ]; then
            python3 -m pip download -r "$PIP_BOOTSTRAP_REQUIREMENTS" \
                -d "$PROJECT_DIR/pip_packages/" \
                --python-version 311 \
                --implementation cp \
                --abi cp311 \
                "${PIP_PLATFORM_ARGS[@]}" \
                --only-binary=:all:
        fi

        # http-ece does not publish wheels, but the built wheel is pure Python.
        # Build it locally first so the platform-restricted binary download can
        # resolve pywebpush without using the pip-conflicting --no-binary flag.
        python3 -m pip wheel --no-deps "http-ece==1.2.1" \
            -w "$PROJECT_DIR/pip_packages/"
        
        # Download for Python 3.11 (Docker image version)
        python3 -m pip download -r "$PROJECT_DIR/requirements.txt" \
            -d "$PROJECT_DIR/pip_packages/" \
            --find-links "$PROJECT_DIR/pip_packages/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            "${PIP_PLATFORM_ARGS[@]}" \
            --only-binary=:all:
            
        echo "$CURRENT_HASH" > "$HASH_FILE"
        echo "✅ Pip packages updated successfully!"
    else
        echo "✅ Pip packages are up to date (hash: $CURRENT_HASH)."
    fi
}

# ==========================================
# 2. Deploy to Iran Server
# ==========================================
deploy_iran() {
    print_header "🇮🇷 Deploying to Iran Server ($IRAN_HOST)"

    cd "$PROJECT_DIR"
    ensure_iran_host_timezone

    # 2a. Check for uncommitted changes & push to GitHub
    echo "📤 Syncing code via git..."
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "⚠️  Uncommitted changes detected!"
        echo "   Please commit your changes with a proper message first:"
        echo "   git add -A && git commit -m \"your message here\""
        exit 1
    fi
    git push 2>/dev/null || echo "  (nothing to push)"

    # 2b. Sync backend code to Iran via rsync
    echo "📥 Syncing code to Iran server via rsync..."
    rsync -avz --delete \
        --exclude '.git' \
        --exclude 'frontend' \
        --exclude 'mini_app_dist' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude 'node_modules' \
        -e "ssh -o StrictHostKeyChecking=no" \
        "$PROJECT_DIR/" "$IRAN_USER@$IRAN_HOST:$IRAN_PROJECT_DIR/"

    # 2c. Upload built frontend assets
    echo "📤 Uploading frontend assets..."
    rsync -avz --delete \
        -e "ssh -o StrictHostKeyChecking=no" \
        "$DIST_DIR/" "$IRAN_USER@$IRAN_HOST:$IRAN_PROJECT_DIR/mini_app_dist/"

    # 2d. Rebuild Docker containers on Iran
    echo "🐳 Building Docker image on Iran explicitly..."
    ssh_iran "cd $IRAN_PROJECT_DIR && DOCKER_BUILDKIT=1 docker build -f Dockerfile.iran -t trading_bot_base_iran ."

    echo "🐳 Recreating Docker services on Iran..."
    echo "⏳ Waiting for Iran services to become ready..."
    ssh_iran "set -e; \
        if docker compose version >/dev/null 2>&1; then COMPOSE_CMD='docker compose'; \
        elif command -v docker-compose >/dev/null 2>&1; then COMPOSE_CMD='docker-compose'; \
        else echo 'No Docker Compose command is available on Iran host.' >&2; exit 1; fi; \
        cd $IRAN_PROJECT_DIR; \
        for service in app sync_worker migration; do \
            ids=\$(docker ps -aq --filter label=com.docker.compose.service=\$service --filter label=com.docker.compose.project=current); \
            if [ -n \"\$ids\" ]; then docker rm -f \$ids >/dev/null 2>&1 || true; fi; \
        done; \
        for container_name in trading_bot_app trading_bot_sync_worker trading_bot_migration; do \
            docker rm -f \"\$container_name\" >/dev/null 2>&1 || true; \
        done; \
        wait_args=''; \
        if [ \"\$COMPOSE_CMD\" = 'docker compose' ]; then wait_args='--wait --wait-timeout 180'; fi; \
        eval \"\$COMPOSE_CMD -f docker-compose.iran.yml up -d --no-recreate db redis\"; \
        for attempt in \$(seq 1 60); do \
            db_id=\$(docker ps -q --filter label=com.docker.compose.service=db --filter label=com.docker.compose.project=current | head -n 1); \
            db_health=''; \
            if [ -n \"\$db_id\" ]; then db_health=\$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \"\$db_id\" 2>/dev/null || true); fi; \
            if [ \"\$db_health\" = 'healthy' ] || [ \"\$db_health\" = 'running' ]; then break; fi; \
            if [ \"\$attempt\" -eq 60 ]; then echo 'Iran database did not become healthy before migration.' >&2; exit 1; fi; \
            sleep 2; \
        done; \
        eval \"\$COMPOSE_CMD -f docker-compose.iran.yml run --rm --no-deps migration\"; \
        docker rm -f trading_bot_migration >/dev/null 2>&1 || true; \
        eval \"\$COMPOSE_CMD -f docker-compose.iran.yml up -d --no-deps \$wait_args app sync_worker\""

    echo "✅ Iran deployment complete!"
    ssh_iran "set -e; \
        if docker compose version >/dev/null 2>&1; then COMPOSE_CMD='docker compose'; \
        elif command -v docker-compose >/dev/null 2>&1; then COMPOSE_CMD='docker-compose'; \
        else echo 'No Docker Compose command is available on Iran host.' >&2; exit 1; fi; \
        cd $IRAN_PROJECT_DIR && eval \"\$COMPOSE_CMD -f docker-compose.iran.yml ps\""
    
    auto_cleanup_iran
}

# ==========================================
# 3. Deploy to Foreign Server (this machine)
# ==========================================
deploy_foreign() {
    print_header "🌍 Deploying Foreign Server (local)"
    local core_services=(db redis migration app bot sync_worker)

    cd "$PROJECT_DIR"
    ensure_local_host_timezone
    resolve_local_compose_cmd

    mkdir -p "$DEPLOY_STATE_DIR"
    local image_signature
    image_signature="$(foreign_image_signature)"
    if [ "$DEPLOY_FORCE_REBUILD" != "1" ] && [ -f "$FOREIGN_IMAGE_SIGNATURE_FILE" ] && [ "$(cat "$FOREIGN_IMAGE_SIGNATURE_FILE")" = "$image_signature" ] && docker image inspect trading_bot_base >/dev/null 2>&1; then
        echo "✅ Foreign Docker image inputs unchanged. Skipping docker build."
    else
        echo "⏳ Building Docker image explicitly to prevent compose parallel export OOM..."
        run_with_local_resource_guard "Foreign Docker image build" env DOCKER_BUILDKIT=1 docker build -t trading_bot_base .
        echo "$image_signature" > "$FOREIGN_IMAGE_SIGNATURE_FILE"
    fi

    echo "ℹ️ Standard foreign deploy only refreshes core services: ${core_services[*]}"
    echo "ℹ️ Optional support services (tileserver) are left untouched to avoid a cold-boot CPU spike after crashes or reboots."
    echo "⏳ Waiting for foreign core services to become ready..."
    run_with_local_resource_guard "Foreign core service startup" bash -lc "$LOCAL_COMPOSE_CMD up -d --wait --wait-timeout 180 ${core_services[*]}"

    echo "✅ Foreign deployment complete!"
    bash -lc "$LOCAL_COMPOSE_CMD ps"

    auto_cleanup_local
}

run_post_full_deploy_sync_recovery() {
    if [ "$AUTO_SYNC_RECOVERY_ON_FULL_DEPLOY" = "0" ]; then
        print_header "⏭️ Skipping automatic sync recovery"
        echo "AUTO_SYNC_RECOVERY_ON_FULL_DEPLOY=0"
        return 0
    fi

    if [ ! -x "$SYNC_RECOVERY_SCRIPT" ]; then
        echo "❌ Sync recovery script is missing or not executable: $SYNC_RECOVERY_SCRIPT"
        exit 1
    fi

    print_header "🔄 Running automatic cross-server sync recovery"
    "$SYNC_RECOVERY_SCRIPT"
}

# ==========================================
# Execute based on target
# ==========================================
case "$TARGET" in
    frontend)
        build_frontend
        deploy_iran  # frontend only goes to Iran
        ;;
    iran)
        prepare_pip_packages
        build_frontend
        deploy_iran
        ;;
    foreign)
        prepare_pip_packages
        build_frontend
        deploy_foreign
        ;;
    all)
        prepare_pip_packages
        build_frontend
        deploy_iran
        deploy_foreign
        run_post_full_deploy_sync_recovery
        ;;
    *)
        echo "Usage: ./deploy.sh [all|frontend|iran|foreign]"
        echo ""
        echo "  all       - Build frontend + deploy to both servers (default)"
        echo "  frontend  - Build frontend + deploy to Iran only"
        echo "  iran      - Build frontend + deploy Iran server"
        echo "  foreign   - Rebuild Docker on foreign server only"
        exit 1
        ;;
esac

print_header "🎉 Deployment Complete!"
