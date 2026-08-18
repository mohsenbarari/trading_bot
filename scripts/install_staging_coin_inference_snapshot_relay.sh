#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/trading-bot/trading_bot}"
SOURCE_ROOT="${STAGING_COIN_INFERENCE_SOURCE_ROOT:-/srv/trading-bot/production-data/coin-intelligence/private-gold-live/staging}"
SOURCE_SNAPSHOT="${STAGING_COIN_INFERENCE_SOURCE_SNAPSHOT:-$SOURCE_ROOT/coin-rates.json}"
LOCAL_ROOT="${STAGING_COIN_INFERENCE_RUNTIME_ROOT:-/srv/trading-bot/staging-data/coin-intelligence}"
LOCAL_SNAPSHOT="${STAGING_COIN_INFERENCE_SNAPSHOT_HOST_PATH:-$LOCAL_ROOT/coin-rates.json}"
REMOTE_HOST="${STAGING_COIN_INFERENCE_REMOTE_HOST:-root@65.109.220.59}"
REMOTE_PORT="${STAGING_COIN_INFERENCE_REMOTE_PORT:-37067}"
REMOTE_ROOT="${STAGING_COIN_INFERENCE_REMOTE_RUNTIME_ROOT:-/srv/trading-bot/staging-data/coin-intelligence}"
REMOTE_SNAPSHOT="${STAGING_COIN_INFERENCE_REMOTE_SNAPSHOT:-$REMOTE_ROOT/coin-rates.json}"
REMOTE_PROJECT_DIR="${STAGING_COIN_INFERENCE_REMOTE_PROJECT_DIR:-/srv/trading-bot/staging-iran}"
SERVICE_PATH="/etc/systemd/system/coin-intelligence-staging-snapshot-relay.service"
TIMER_PATH="/etc/systemd/system/coin-intelligence-staging-snapshot-relay.timer"

[[ "$(id -u)" == "0" ]] || { printf 'root_required\n' >&2; exit 2; }
for required_command in python3 scp ssh systemctl; do
    command -v "$required_command" >/dev/null 2>&1 || {
        printf 'required_command_missing=%s\n' "$required_command" >&2
        exit 2
    }
done
[[ -f "$PROJECT_DIR/scripts/relay_staging_coin_inference_snapshot.py" ]] || {
    printf 'relay_script_missing\n' >&2
    exit 2
}
[[ -f "$SOURCE_SNAPSHOT" ]] || { printf 'source_snapshot_missing\n' >&2; exit 2; }

install -d -m 0755 -- "$LOCAL_ROOT"

tmp_service="$(mktemp)"
tmp_timer="$(mktemp)"
trap 'rm -f -- "$tmp_service" "$tmp_timer"' EXIT

cat >"$tmp_service" <<EOF
[Unit]
Description=Relay the validated coin-inference Snapshot to both staging peers
After=network-online.target coin-intelligence-staging-snapshot-publish.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $PROJECT_DIR/scripts/relay_staging_coin_inference_snapshot.py --environment staging --source-root $SOURCE_ROOT --source-snapshot $SOURCE_SNAPSHOT --local-runtime-root $LOCAL_ROOT --local-snapshot $LOCAL_SNAPSHOT --maximum-age-seconds 120 --remote-host $REMOTE_HOST --remote-port $REMOTE_PORT --remote-runtime-root $REMOTE_ROOT --remote-snapshot $REMOTE_SNAPSHOT --remote-project-dir $REMOTE_PROJECT_DIR
EOF

cat >"$tmp_timer" <<'EOF'
[Unit]
Description=Relay the staging coin-inference Snapshot every 30 seconds

[Timer]
OnBootSec=15s
OnCalendar=*-*-* *:*:10,40
AccuracySec=1s
RandomizedDelaySec=0
Persistent=true
Unit=coin-intelligence-staging-snapshot-relay.service

[Install]
WantedBy=timers.target
EOF

install -m 0644 -- "$tmp_service" "$SERVICE_PATH"
install -m 0644 -- "$tmp_timer" "$TIMER_PATH"
systemctl daemon-reload
systemctl start coin-intelligence-staging-snapshot-relay.service
systemctl enable --now coin-intelligence-staging-snapshot-relay.timer
systemctl is-active --quiet coin-intelligence-staging-snapshot-relay.timer
printf 'staging_coin_inference_snapshot_relay=active\n'
