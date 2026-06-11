#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
SERVICE_NAME="${SERVICE_NAME:-trading-bot-production-alerts}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
STATE_DIR="${STATE_DIR:-/var/lib/trading-bot-observability}"
LOG_DIR="${LOG_DIR:-/var/log}"
INTERVAL="${INTERVAL:-5min}"
MANIFEST="${MANIFEST:-$ROOT_DIR/deploy/production/online.env}"
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME.service"
TIMER_PATH="$SYSTEMD_DIR/$SERVICE_NAME.timer"
OUTPUT_PATH="$STATE_DIR/production-alerts-latest.json"
LOG_PATH="$LOG_DIR/trading-bot-production-alerts.log"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "python3 was not found in PATH" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  install -d "$SYSTEMD_DIR" "$STATE_DIR" "$LOG_DIR"
  cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Trading Bot production alert sampler
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStart=/bin/sh -c '$PYTHON_BIN $ROOT_DIR/scripts/report_production_alerts.py --manifest "$MANIFEST" --json --fail-on never > "$OUTPUT_PATH"'
StandardOutput=append:$LOG_PATH
StandardError=append:$LOG_PATH
EOF

  cat >"$TIMER_PATH" <<EOF
[Unit]
Description=Run Trading Bot production alert sampler every $INTERVAL

[Timer]
OnBootSec=2min
OnUnitActiveSec=$INTERVAL
Unit=$SERVICE_NAME.service

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME.timer"
  echo "Installed and started $SERVICE_NAME.timer"
  echo "Latest JSON: $OUTPUT_PATH"
  echo "Log: $LOG_PATH"
  exit 0
fi

echo "systemd is not available. Add this cron entry instead:"
echo "*/5 * * * * cd $ROOT_DIR && $PYTHON_BIN scripts/report_production_alerts.py --manifest $MANIFEST --json --fail-on never > $OUTPUT_PATH 2>> $LOG_PATH"
