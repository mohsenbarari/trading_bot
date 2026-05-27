#!/bin/bash
set -e

# ==========================================
# 🚀 Deploy Script — Two-Server Architecture
# ==========================================
# Foreign Server (Germany): Bot + Sync + API
# Iran Server:              API + Nginx + Frontend
# ==========================================

PROJECT_DIR="/root/trading-bot/trading_bot"
FRONTEND_DIR="$PROJECT_DIR/frontend"
DIST_DIR="$PROJECT_DIR/mini_app_dist"
TIMEZONE_SCRIPT="$PROJECT_DIR/scripts/ensure_host_timezone.sh"
SYNC_RECOVERY_SCRIPT="$PROJECT_DIR/scripts/recover_cross_server_sync.sh"
FOREIGN_HOST_TIMEZONE="${FOREIGN_HOST_TIMEZONE:-UTC}"
IRAN_HOST_TIMEZONE="${IRAN_HOST_TIMEZONE:-UTC}"
AUTO_SYNC_RECOVERY_ON_FULL_DEPLOY="${AUTO_SYNC_RECOVERY_ON_FULL_DEPLOY:-1}"

IRAN_HOST="87.107.110.68"
IRAN_USER="root"
IRAN_PROJECT_DIR="/root/trading-bot/trading_bot"

# ==========================================
# Helper Functions
# ==========================================
ssh_iran() {
    ssh -o StrictHostKeyChecking=no "$IRAN_USER@$IRAN_HOST" "$@"
}

scp_iran() {
    scp -r -o StrictHostKeyChecking=no "$@"
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
    run_with_local_resource_guard "Frontend build" bash -lc "cd \"$FRONTEND_DIR\" && npm install --silent && NODE_OPTIONS=\"--max-old-space-size=1024\" npm run build"

    if [ ! -d "$DIST_DIR" ]; then
        echo "❌ Build directory ($DIST_DIR) not found!"
        exit 1
    fi

    chmod -R 755 "$DIST_DIR"
    echo "✅ Frontend build successful!"
    cd "$PROJECT_DIR"
}

# ==========================================
# 1.5. Prepare Pip Packages (Germany only)
# ==========================================
prepare_pip_packages() {
    print_header "📦 Checking pip dependencies"
    
    HASH_FILE="$PROJECT_DIR/pip_packages/.requirements_hash"
    CURRENT_HASH=$(md5sum "$PROJECT_DIR/requirements.txt" | cut -d' ' -f1)
    
    if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$CURRENT_HASH" ] || [ ! -d "$PROJECT_DIR/pip_packages" ]; then
        echo "🔄 requirements.txt changed or packages missing. Downloading..."
        mkdir -p "$PROJECT_DIR/pip_packages"
        
        # Download for Python 3.11 (Docker image version)
        pip download -r "$PROJECT_DIR/requirements.txt" \
            -d "$PROJECT_DIR/pip_packages/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            --platform manylinux2014_x86_64 \
            --platform manylinux_2_17_x86_64 \
            --platform manylinux_2_28_x86_64 \
            --platform linux_x86_64 \
            --platform any \
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
    ssh_iran "cd $IRAN_PROJECT_DIR && docker compose -f docker-compose.iran.yml up -d --wait --wait-timeout 180"

    echo "✅ Iran deployment complete!"
    ssh_iran "cd $IRAN_PROJECT_DIR && docker compose -f docker-compose.iran.yml ps"
    
    auto_cleanup_iran
}

# ==========================================
# 3. Deploy to Foreign Server (this machine)
# ==========================================
deploy_foreign() {
    print_header "🌍 Deploying Foreign Server (local)"
    local core_services=(db redis migration app bot)

    cd "$PROJECT_DIR"
    ensure_local_host_timezone

    echo "⏳ Building Docker image explicitly to prevent compose parallel export OOM..."
    run_with_local_resource_guard "Foreign Docker image build" env DOCKER_BUILDKIT=1 docker build -t trading_bot_base .

    echo "ℹ️ Standard foreign deploy only refreshes core services: ${core_services[*]}"
    echo "ℹ️ Optional support services (tileserver/adminer) are left untouched to avoid a cold-boot CPU spike after crashes or reboots."
    echo "⏳ Waiting for foreign core services to become ready..."
    run_with_local_resource_guard "Foreign core service startup" docker compose up -d --wait --wait-timeout 180 "${core_services[@]}"

    echo "✅ Foreign deployment complete!"
    docker compose ps

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
