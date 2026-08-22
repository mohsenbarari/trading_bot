#!/bin/bash
set -e

# ==========================================
# 🚀 Deploy Script — Two-Server Architecture
# ==========================================
# Foreign Server (Germany): Bot + Sync + API
# Iran Server:              API + Nginx + Frontend
# ==========================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRODUCTION_CANONICAL_CHECKOUT="/root/trading-bot/trading_bot"
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
PRODUCTION_DEFER_FOREIGN_WRITER_START="${PRODUCTION_DEFER_FOREIGN_WRITER_START:-0}"
PRODUCTION_PREBUILD_ONLY="${PRODUCTION_PREBUILD_ONLY:-0}"
PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE="${PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE:-0}"
PRODUCTION_OFFICIAL_DEPLOY_AUTHORITY_PATH="${PRODUCTION_OFFICIAL_DEPLOY_AUTHORITY_PATH:-}"
PRODUCTION_RELEASE_LOCK_PATH="${PRODUCTION_RELEASE_LOCK_PATH:-/var/lib/trading-bot/production-release/production-release.lock}"
PRODUCTION_RELEASE_SHA="${PRODUCTION_RELEASE_SHA:-}"
PRODUCTION_RELEASE_TREE="${PRODUCTION_RELEASE_TREE:-}"
PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID="${PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID:-}"
PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE="${PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE:-}"
PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS="${PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS:-1800}"
[[ "$PRODUCTION_DEFER_FOREIGN_WRITER_START" == "0" \
    || "$PRODUCTION_DEFER_FOREIGN_WRITER_START" == "1" ]] || {
    echo "PRODUCTION_DEFER_FOREIGN_WRITER_START must be 0 or 1." >&2
    exit 1
}
[[ "$PRODUCTION_PREBUILD_ONLY" == "0" || "$PRODUCTION_PREBUILD_ONLY" == "1" ]] || {
    echo "PRODUCTION_PREBUILD_ONLY must be 0 or 1." >&2
    exit 1
}
[[ "$PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE" == "0" \
    || "$PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE" == "1" ]] || {
    echo "PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE must be 0 or 1." >&2
    exit 1
}
[[ "$PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS" =~ ^[0-9]+$ \
    && "$PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS" -ge 60 \
    && "$PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS" -le 3600 ]] || {
    echo "PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS must be between 60 and 3600." >&2
    exit 1
}
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
TARGET="${1:-all}"  # all | frontend | foreign | iran

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

is_production_deploy_surface() {
    # Environment variables are not sufficient evidence that a checkout is
    # non-production: this compose stack uses fixed production container names,
    # so a caller could otherwise spoof COMPOSE_PROJECT_NAME and still replace a
    # live container.  Treat every checkout carrying those production markers,
    # and the canonical production checkout itself, as an official-only surface.
    [[ "$SCRIPT_DIR" == "$PRODUCTION_CANONICAL_CHECKOUT" \
        || "$COMPOSE_PROJECT_NAME" == "trading_bot" ]] \
        && return 0
    local compose_file
    for compose_file in "$PROJECT_DIR/docker-compose.yml" "$PROJECT_DIR/docker-compose.iran.yml"; do
        [[ -f "$compose_file" ]] || continue
        if grep -Eq '^[[:space:]]*container_name:[[:space:]]*trading_bot_(app|bot|sync_worker|migration|db|redis)([[:space:]]|$)' "$compose_file"; then
            return 0
        fi
    done
    return 1
}

consume_official_production_deploy_authority() {
    [[ "$TARGET" == "foreign" ]] || {
        echo "Production targets may only be deployed by the official two-host wrapper." >&2
        exit 1
    }
    [[ -n "$PRODUCTION_OFFICIAL_DEPLOY_AUTHORITY_PATH" \
        && -n "$PRODUCTION_RELEASE_SHA" \
        && -n "$PRODUCTION_RELEASE_TREE" ]] || {
        echo "Official production deploy authority is missing." >&2
        exit 1
    }
    python3 - "$PRODUCTION_OFFICIAL_DEPLOY_AUTHORITY_PATH" \
        "$PRODUCTION_RELEASE_LOCK_PATH" "$TARGET" "$PPID" \
        "$PRODUCTION_RELEASE_SHA" "$PRODUCTION_RELEASE_TREE" "$PROJECT_DIR" <<'PY'
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

authority = Path(sys.argv[1])
lock = Path(sys.argv[2])
target, parent_pid, release_sha, release_tree = sys.argv[3:7]
project = Path(sys.argv[7])
production_project = Path("/root/trading-bot/trading_bot")
canonical = Path("/var/lib/trading-bot/production-release/deploy-sh-authority.json")
if authority != canonical or authority.is_symlink() or not authority.is_file():
    raise SystemExit("Official production deploy authority path/type is invalid.")
parent_st = authority.parent.stat()
if authority.parent.is_symlink() or stat.S_IMODE(parent_st.st_mode) != 0o700 or parent_st.st_uid != os.getuid():
    raise SystemExit("Official production deploy authority parent is invalid.")
st = authority.stat()
if stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.getuid() or st.st_nlink != 1:
    raise SystemExit("Official production deploy authority ownership/mode is invalid.")
if lock.is_symlink() or not lock.is_file():
    raise SystemExit("Official production release lock is not active.")
lock_st = lock.stat()
if stat.S_IMODE(lock_st.st_mode) != 0o600 or lock_st.st_uid != os.getuid() or lock_st.st_nlink != 1:
    raise SystemExit("Official production release lock ownership/mode is invalid.")
payload = json.loads(authority.read_text(encoding="utf-8"))
required = {
    "schema_version", "environment", "target", "parent_pid", "release_sha",
    "release_tree", "release_lock_device", "release_lock_inode", "secrets_disclosed",
}
if set(payload) != required:
    raise SystemExit("Official production deploy authority schema is invalid.")
if payload != {
    "schema_version": 1,
    "environment": "production",
    "target": target,
    "parent_pid": int(parent_pid),
    "release_sha": release_sha,
    "release_tree": release_tree,
    "release_lock_device": lock_st.st_dev,
    "release_lock_inode": lock_st.st_ino,
    "secrets_disclosed": False,
}:
    raise SystemExit("Official production deploy authority binding is invalid.")
if not re.fullmatch(r"[0-9a-f]{40}", release_sha) or not re.fullmatch(r"[0-9a-f]{40}", release_tree):
    raise SystemExit("Official production Git identity is invalid.")
if project.resolve(strict=True) != production_project:
    raise SystemExit("Official production deploy must run from the canonical checkout.")
actual_sha = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
actual_tree = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD^{tree}"], text=True).strip()
if (actual_sha, actual_tree) != (release_sha, release_tree):
    raise SystemExit("Official production Git identity drifted before deploy.")
status_output = subprocess.check_output(
    ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
    text=True,
)
if status_output:
    raise SystemExit("Official production deploy requires a clean immutable checkout.")
upstream = subprocess.check_output(
    ["git", "-C", str(project), "rev-parse", "@{u}"], text=True
).strip()
if upstream != release_sha:
    raise SystemExit("Official production deploy requires the pushed upstream release commit.")
before = authority.stat()
if (before.st_dev, before.st_ino) != (st.st_dev, st.st_ino):
    raise SystemExit("Official production deploy authority changed during verification.")
authority.unlink()
directory = os.open(authority.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
    PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED=1
}

PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED=0
if is_production_deploy_surface; then
    consume_official_production_deploy_authority
fi

verify_official_source_still_frozen() {
    [ "$PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED" = "1" ] || return 0
    [ "$(git -C "$PROJECT_DIR" rev-parse HEAD)" = "$PRODUCTION_RELEASE_SHA" ] \
        && [ "$(git -C "$PROJECT_DIR" rev-parse 'HEAD^{tree}')" = "$PRODUCTION_RELEASE_TREE" ] \
        && [ "$(git -C "$PROJECT_DIR" rev-parse '@{u}')" = "$PRODUCTION_RELEASE_SHA" ] \
        && [ -z "$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=all)" ] || {
            echo "Official production source drifted after authority verification." >&2
            exit 1
        }
}

# ==========================================
# Helper Functions
# ==========================================
ssh_iran() {
    ssh -o StrictHostKeyChecking=accept-new -p "$IRAN_SSH_PORT" "$IRAN_USER@$IRAN_HOST" "$@"
}

scp_iran() {
    scp -r -P "$IRAN_SSH_PORT" -o StrictHostKeyChecking=accept-new "$@"
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

guarded_process_is_live() {
    local pid="$1"
    local state
    state="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$state" && "$state" != Z* ]]
}

guarded_process_group_has_live_members() {
    local process_group="$1"
    ps -eo pgid=,stat= 2>/dev/null | awk -v expected="$process_group" '
        $1 == expected && $2 !~ /^Z/ { found=1 }
        END { exit(found ? 0 : 1) }
    '
}

wait_for_guarded_process_stop() {
    local cmd_pid="$1"
    local process_group="$2"
    local maximum_seconds="$3"
    local started_seconds=$SECONDS
    while guarded_process_is_live "$cmd_pid" \
        || guarded_process_group_has_live_members "$process_group"; do
        if (( SECONDS - started_seconds >= maximum_seconds )); then
            return 1
        fi
        sleep 0.1
    done
    return 0
}

terminate_guarded_process() {
    local cmd_pid="$1"
    local process_group="$2"
    local grace_seconds="${DEPLOY_RESOURCE_GUARD_TERMINATION_GRACE_SECONDS:-5}"
    local verification_seconds="${DEPLOY_RESOURCE_GUARD_KILL_VERIFY_SECONDS:-5}"
    [[ "$grace_seconds" =~ ^[0-9]+$ && "$grace_seconds" -le 30 ]] || grace_seconds=5
    [[ "$verification_seconds" =~ ^[1-9][0-9]*$ \
        && "$verification_seconds" -le 30 ]] || verification_seconds=5
    kill -TERM -- "-$process_group" 2>/dev/null || true
    if ! wait_for_guarded_process_stop "$cmd_pid" "$process_group" "$grace_seconds"; then
        kill -KILL -- "-$process_group" 2>/dev/null || true
        kill -KILL "$cmd_pid" 2>/dev/null || true
        if ! wait_for_guarded_process_stop "$cmd_pid" "$process_group" "$verification_seconds"; then
            echo "Guarded process group $process_group did not stop after bounded TERM/KILL cleanup." >&2
            return 1
        fi
    fi
    # The leader is already dead or a zombie, so this reap cannot block.
    wait "$cmd_pid" 2>/dev/null || true
    ! guarded_process_group_has_live_members "$process_group"
}

run_with_local_resource_guard() {
    local label="$1"
    shift

    local sample_seconds="${DEPLOY_RESOURCE_GUARD_SAMPLE_SECONDS:-5}"
    local maximum_seconds="${DEPLOY_RESOURCE_GUARD_MAX_SECONDS:-7200}"
    local max_streak="${DEPLOY_RESOURCE_GUARD_MAX_STREAK:-4}"
    local max_mem_percent="${DEPLOY_RESOURCE_GUARD_MAX_MEM_PERCENT:-95}"
    local max_swap_percent="${DEPLOY_RESOURCE_GUARD_MAX_SWAP_PERCENT:-70}"
    local min_mem_available_kb="${DEPLOY_RESOURCE_GUARD_MIN_MEM_AVAILABLE_KB:-262144}"
    local cpu_with_high_mem_percent="${DEPLOY_RESOURCE_GUARD_CPU_WITH_HIGH_MEM_PERCENT:-97}"
    local cpu_only_percent="${DEPLOY_RESOURCE_GUARD_CPU_ONLY_PERCENT:-99}"
    local cpu_only_max_streak="${DEPLOY_RESOURCE_GUARD_CPU_ONLY_MAX_STREAK:-12}"
    local monitor_resources=0
    local sample_index=0
    local pressure_streak=0
    local cpu_only_streak=0
    local prev_total prev_idle

    [[ "$sample_seconds" =~ ^[1-9][0-9]*$ && "$sample_seconds" -le 60 ]] || {
        echo "DEPLOY_RESOURCE_GUARD_SAMPLE_SECONDS must be between 1 and 60." >&2
        return 2
    }
    [[ "$maximum_seconds" =~ ^[1-9][0-9]*$ \
        && "$maximum_seconds" -le 14400 ]] || {
        echo "DEPLOY_RESOURCE_GUARD_MAX_SECONDS must be between 1 and 14400." >&2
        return 2
    }
    if resource_guard_enabled; then
        monitor_resources=1
    fi

    print_header "🛡️ Resource Guard: $label"
    echo "   deadline=${maximum_seconds}s resource_sampling=${monitor_resources} sample=${sample_seconds}s mem>=${max_mem_percent}% swap>=${max_swap_percent}% cpu>=${cpu_only_percent}%"

    command -v setsid >/dev/null 2>&1 || {
        echo "setsid is required for guarded process-group containment." >&2
        return 1
    }
    setsid --wait "$@" &
    local cmd_pid=$!
    # A non-interactive background job is not a process-group leader, so
    # `setsid` makes this exact PID the new session/process-group leader. Keep
    # that identity even when a short leader exits before the first `ps`.
    local process_group="$cmd_pid"
    local observed_process_group
    observed_process_group="$(ps -o pgid= -p "$cmd_pid" | tr -d '[:space:]')"
    [[ -z "$observed_process_group" || "$observed_process_group" == "$process_group" ]] || {
        echo "Could not isolate the guarded command in its own process group." >&2
        kill -TERM "$cmd_pid" 2>/dev/null || true
        sleep 0.1
        kill -KILL "$cmd_pid" 2>/dev/null || true
        if ! wait_for_guarded_process_stop "$cmd_pid" "$cmd_pid" 5; then
            echo "Could not stop the non-isolated guarded command within the safety bound." >&2
            return 1
        fi
        wait "$cmd_pid" 2>/dev/null || true
        return 1
    }
    local started_seconds=$SECONDS
    if [ "$monitor_resources" = "1" ]; then
        read -r prev_total prev_idle <<EOF
$(sample_cpu_usage)
EOF
    fi

    while kill -0 "$cmd_pid" 2>/dev/null; do
        local elapsed_seconds=$((SECONDS - started_seconds))
        if [ "$elapsed_seconds" -ge "$maximum_seconds" ]; then
            echo "❌ Wall-clock deadline reached for '$label' after ${maximum_seconds}s. Stopping the entire process group."
            if ! terminate_guarded_process "$cmd_pid" "$process_group"; then
                return 125
            fi
            return 124
        fi
        local remaining_seconds=$((maximum_seconds - elapsed_seconds))
        local sleep_seconds="$sample_seconds"
        if [ "$sleep_seconds" -gt "$remaining_seconds" ]; then
            sleep_seconds="$remaining_seconds"
        fi
        sleep "$sleep_seconds"
        sample_index=$((sample_index + 1))

        if ! kill -0 "$cmd_pid" 2>/dev/null; then
            break
        fi
        elapsed_seconds=$((SECONDS - started_seconds))
        if [ "$elapsed_seconds" -ge "$maximum_seconds" ]; then
            echo "❌ Wall-clock deadline reached for '$label' after ${maximum_seconds}s. Stopping the entire process group."
            if ! terminate_guarded_process "$cmd_pid" "$process_group"; then
                return 125
            fi
            return 124
        fi
        if [ "$monitor_resources" != "1" ]; then
            continue
        fi

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
            if ! terminate_guarded_process "$cmd_pid" "$process_group"; then
                return 125
            fi
            return 124
        fi
    done

    local command_status=0
    wait "$cmd_pid" || command_status=$?
    if guarded_process_group_has_live_members "$process_group"; then
        echo "❌ Guarded command '$label' exited while live process-group members remained."
        if ! terminate_guarded_process "$cmd_pid" "$process_group"; then
            return 125
        fi
        return 125
    fi
    return "$command_status"
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
            trading_settings.json \
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
        -e "ssh -o StrictHostKeyChecking=accept-new -p $IRAN_SSH_PORT" \
        "$PROJECT_DIR/" "$IRAN_USER@$IRAN_HOST:$IRAN_PROJECT_DIR/"

    # 2c. Upload built frontend assets
    echo "📤 Uploading frontend assets..."
    rsync -avz --delete \
        -e "ssh -o StrictHostKeyChecking=accept-new -p $IRAN_SSH_PORT" \
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
    local core_services=(app bot sync_worker)

    cd "$PROJECT_DIR"
    verify_official_source_still_frozen
    ensure_local_host_timezone
    resolve_local_compose_cmd

    mkdir -p "$DEPLOY_STATE_DIR"
    local image_signature
    image_signature="$(foreign_image_signature)"
    if [ "$PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE" = "1" ]; then
        [ "$PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED" = "1" ] \
            && [[ "$PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] \
            && [[ "$PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE" =~ ^[0-9a-f]{64}$ ]] \
            && [ -f "$FOREIGN_IMAGE_SIGNATURE_FILE" ] \
            && [ "$(cat "$FOREIGN_IMAGE_SIGNATURE_FILE")" = "$image_signature" ] \
            && [ "$image_signature" = "$PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE" ] \
            && [ "$(docker image inspect --format '{{.Id}}' trading_bot_base)" = "$PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID" ] \
            && [ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' trading_bot_base)" = "$PRODUCTION_RELEASE_SHA" ] \
            && [ "$(docker image inspect --format '{{index .Config.Labels "io.gold-trade.release.tree"}}' trading_bot_base)" = "$PRODUCTION_RELEASE_TREE" ] \
            && [ "$(docker image inspect --format '{{index .Config.Labels "io.gold-trade.release.input-signature"}}' trading_bot_base)" = "$image_signature" ] \
            && docker image inspect trading_bot_base >/dev/null 2>&1 || {
                echo "The official production migration requires the exact receipt-bound foreign image to be prebuilt before writer quiescence." >&2
                exit 1
            }
        echo "✅ Exact prebuilt foreign Docker image verified; no post-quiesce build is allowed."
    elif [ "$DEPLOY_FORCE_REBUILD" != "1" ] \
        && [ -f "$FOREIGN_IMAGE_SIGNATURE_FILE" ] \
        && [ "$(cat "$FOREIGN_IMAGE_SIGNATURE_FILE")" = "$image_signature" ] \
        && docker image inspect trading_bot_base >/dev/null 2>&1 \
        && { [ "$PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED" != "1" ] \
            || { [ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' trading_bot_base)" = "$PRODUCTION_RELEASE_SHA" ] \
                && [ "$(docker image inspect --format '{{index .Config.Labels "io.gold-trade.release.tree"}}' trading_bot_base)" = "$PRODUCTION_RELEASE_TREE" ] \
                && [ "$(docker image inspect --format '{{index .Config.Labels "io.gold-trade.release.input-signature"}}' trading_bot_base)" = "$image_signature" ]; }; }; then
        echo "✅ Foreign Docker image inputs unchanged. Skipping docker build."
    else
        echo "⏳ Building Docker image explicitly to prevent compose parallel export OOM..."
        local -a image_label_args=()
        if [ "$PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED" = "1" ]; then
            image_label_args=(
                --label "org.opencontainers.image.revision=$PRODUCTION_RELEASE_SHA"
                --label "io.gold-trade.release.tree=$PRODUCTION_RELEASE_TREE"
                --label "io.gold-trade.release.input-signature=$image_signature"
            )
        fi
        run_with_local_resource_guard "Foreign Docker image build" \
            env DOCKER_BUILDKIT=1 docker build "${image_label_args[@]}" -t trading_bot_base .
        echo "$image_signature" > "$FOREIGN_IMAGE_SIGNATURE_FILE"
    fi

    if [ "$PRODUCTION_PREBUILD_ONLY" = "1" ]; then
        verify_official_source_still_frozen
        [ "$(foreign_image_signature)" = "$image_signature" ] || {
            echo "Foreign image inputs drifted during the official prebuild." >&2
            exit 1
        }
        echo "✅ Foreign production image prebuild passed; no service or database was touched."
        return 0
    fi

    verify_official_source_still_frozen
    [ "$(foreign_image_signature)" = "$image_signature" ] || {
        echo "Foreign image/runtime inputs drifted before Compose startup." >&2
        exit 1
    }

    echo "ℹ️ Standard foreign deploy only refreshes core services: ${core_services[*]}"
    echo "ℹ️ Optional support services (tileserver) are left untouched to avoid a cold-boot CPU spike after crashes or reboots."
    echo "⏳ Starting stateful dependencies without recreating them..."
    run_with_local_resource_guard "Foreign stateful dependencies startup" bash -lc "$LOCAL_COMPOSE_CMD up -d --no-recreate --wait --wait-timeout 180 db redis"
    echo "⏳ Running migrations and validating the trade-number sequence..."
    DEPLOY_RESOURCE_GUARD_MAX_SECONDS="$PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS" \
        run_with_local_resource_guard "Foreign database migration" \
        bash -lc "$LOCAL_COMPOSE_CMD run --rm --no-deps migration"
    if [ "$PRODUCTION_DEFER_FOREIGN_WRITER_START" = "1" ]; then
        echo "⏸️ Foreign writer startup is deferred until the official two-host schema gate passes."
    else
        echo "⏳ Waiting for foreign core services to become ready..."
        run_with_local_resource_guard "Foreign core service startup" bash -lc "$LOCAL_COMPOSE_CMD up -d --wait --wait-timeout 180 ${core_services[*]}"
    fi

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
