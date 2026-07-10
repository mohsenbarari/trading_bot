#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
SERVICE_NAME="${SERVICE_NAME:-trading-bot-sync-health-sampler}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME.service"
TIMER_PATH="$SYSTEMD_DIR/$SERVICE_NAME.timer"
SKIP_IRAN_ARG=""

case "${SYNC_HEALTH_MONITOR_SKIP_IRAN:-0}" in
  0) ;;
  1) SKIP_IRAN_ARG=" --skip-iran" ;;
  *)
    echo "SYNC_HEALTH_MONITOR_SKIP_IRAN must be 0 or 1" >&2
    exit 2
    ;;
esac

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "python3 was not found in PATH" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  install -d "$SYSTEMD_DIR"
  cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Trading Bot sync health sampler
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStart=$PYTHON_BIN $ROOT_DIR/scripts/sample_sync_health.py$SKIP_IRAN_ARG
EOF

  cat >"$TIMER_PATH" <<EOF
[Unit]
Description=Run Trading Bot sync health sampler every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=$SERVICE_NAME.service

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME.timer"
  echo "Installed and started $SERVICE_NAME.timer"
  exit 0
fi

echo "systemd is not available. Add this cron entry instead:"
echo "* * * * * cd $ROOT_DIR && $PYTHON_BIN scripts/sample_sync_health.py$SKIP_IRAN_ARG >> /var/log/trading-bot-sync-health.log 2>&1"
