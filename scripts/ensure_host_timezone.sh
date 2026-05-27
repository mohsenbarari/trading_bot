#!/bin/bash
set -euo pipefail

TARGET_TIMEZONE="${1:-UTC}"
DRY_RUN="${DEPLOY_TIMEZONE_DRY_RUN:-0}"

detect_current_timezone() {
    if command -v timedatectl >/dev/null 2>&1; then
        local timedatectl_timezone
        timedatectl_timezone="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
        if [ -n "$timedatectl_timezone" ]; then
            echo "$timedatectl_timezone"
            return 0
        fi
    fi

    if [ -f /etc/timezone ]; then
        local file_timezone
        file_timezone="$(tr -d '[:space:]' < /etc/timezone 2>/dev/null || true)"
        if [ -n "$file_timezone" ]; then
            echo "$file_timezone"
            return 0
        fi
    fi

    if [ -L /etc/localtime ]; then
        local link_target
        link_target="$(readlink -f /etc/localtime 2>/dev/null || true)"
        case "$link_target" in
            /usr/share/zoneinfo/*)
                echo "${link_target#/usr/share/zoneinfo/}"
                return 0
                ;;
        esac
    fi

    echo "unknown"
}

normalize_timezone_name() {
    case "$1" in
        UTC|Etc/UTC|Etc/GMT|GMT)
            echo "UTC"
            ;;
        *)
            echo "$1"
            ;;
    esac
}

require_root() {
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
        echo "❌ ensure_host_timezone.sh must run as root." >&2
        exit 1
    fi
}

ensure_timezone_exists() {
    if [ ! -f "/usr/share/zoneinfo/$TARGET_TIMEZONE" ]; then
        echo "❌ Target timezone '$TARGET_TIMEZONE' does not exist under /usr/share/zoneinfo." >&2
        exit 1
    fi
}

apply_timezone_change() {
    if command -v timedatectl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        timedatectl set-timezone "$TARGET_TIMEZONE"
        return 0
    fi

    ln -snf "/usr/share/zoneinfo/$TARGET_TIMEZONE" /etc/localtime
    echo "$TARGET_TIMEZONE" > /etc/timezone
}

CURRENT_TIMEZONE="$(detect_current_timezone)"
NORMALIZED_CURRENT_TIMEZONE="$(normalize_timezone_name "$CURRENT_TIMEZONE")"
NORMALIZED_TARGET_TIMEZONE="$(normalize_timezone_name "$TARGET_TIMEZONE")"

echo "🕒 Host timezone check: current='$CURRENT_TIMEZONE' target='$TARGET_TIMEZONE'"

if [ "$NORMALIZED_CURRENT_TIMEZONE" = "$NORMALIZED_TARGET_TIMEZONE" ]; then
    echo "✅ Host timezone already matches '$TARGET_TIMEZONE'. No change needed."
    exit 0
fi

ensure_timezone_exists
require_root

if [ "$DRY_RUN" = "1" ]; then
    echo "ℹ️ DRY RUN: timezone would change from '$CURRENT_TIMEZONE' to '$TARGET_TIMEZONE'."
    exit 0
fi

echo "🔄 Updating host timezone to '$TARGET_TIMEZONE'..."
apply_timezone_change

UPDATED_TIMEZONE="$(detect_current_timezone)"
NORMALIZED_UPDATED_TIMEZONE="$(normalize_timezone_name "$UPDATED_TIMEZONE")"
if [ "$NORMALIZED_UPDATED_TIMEZONE" != "$NORMALIZED_TARGET_TIMEZONE" ]; then
    echo "❌ Host timezone update did not converge. Current='$UPDATED_TIMEZONE' expected='$TARGET_TIMEZONE'." >&2
    exit 1
fi

echo "✅ Host timezone updated successfully to '$TARGET_TIMEZONE'."