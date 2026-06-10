#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-trading-bot-audit-anchor-export}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME.service"
TIMER_PATH="$SYSTEMD_DIR/$SERVICE_NAME.timer"
DOCKER_COMPOSE_BIN="${DOCKER_COMPOSE_BIN:-docker compose}"
CONTAINER_INPUT_PATH="${CONTAINER_INPUT_PATH:-/app/audit_trail/audit.jsonl}"
HOST_OUTPUT_PATH="${HOST_OUTPUT_PATH:-/var/lib/trading-bot-observability/audit-anchor.jsonl}"
HOST_RELEASE_ID="${HOST_RELEASE_ID:-unknown}"
HOST_SOURCE_NAME="${HOST_SOURCE_NAME:-foreign}"

if command -v systemctl >/dev/null 2>&1; then
  install -d "$SYSTEMD_DIR"
  cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Trading Bot audit anchor exporter
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStart=/bin/bash -lc 'install -d "$(dirname "$HOST_OUTPUT_PATH")" && cd "$ROOT_DIR" && $DOCKER_COMPOSE_BIN exec -T app python scripts/export_audit_anchor.py --input "$CONTAINER_INPUT_PATH" --output - --release-id "$HOST_RELEASE_ID" --host-id "$(hostname -f 2>/dev/null || hostname)" --source-name "$HOST_SOURCE_NAME" >> "$HOST_OUTPUT_PATH"'
EOF

  cat >"$TIMER_PATH" <<EOF
[Unit]
Description=Run Trading Bot audit anchor export every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
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
echo "*/5 * * * * install -d \"\$(dirname \"$HOST_OUTPUT_PATH\")\" && cd $ROOT_DIR && $DOCKER_COMPOSE_BIN exec -T app python scripts/export_audit_anchor.py --input \"$CONTAINER_INPUT_PATH\" --output - --release-id \"$HOST_RELEASE_ID\" --host-id \"\$(hostname -f 2>/dev/null || hostname)\" --source-name \"$HOST_SOURCE_NAME\" >> \"$HOST_OUTPUT_PATH\""
