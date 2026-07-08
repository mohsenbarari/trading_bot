#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
SERVICE_NAME="${SERVICE_NAME:-trading-bot-audit-anchor-shipper}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME.service"
TIMER_PATH="$SYSTEMD_DIR/$SERVICE_NAME.timer"
ANCHOR_INPUT_PATH="${ANCHOR_INPUT_PATH:-/var/lib/trading-bot-observability/audit-anchor.jsonl}"
ANCHOR_REMOTE_TARGET="${ANCHOR_REMOTE_TARGET:-}"
ANCHOR_REMOTE_SSH_PORT="${ANCHOR_REMOTE_SSH_PORT:-}"
ANCHOR_RELAY_OUTPUT_PATH="${ANCHOR_RELAY_OUTPUT_PATH:-}"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "python3 was not found in PATH" >&2
  exit 1
fi

if [[ -z "${ANCHOR_REMOTE_TARGET:-}" && -z "${ANCHOR_RELAY_OUTPUT_PATH:-}" ]]; then
  echo "Set ANCHOR_REMOTE_TARGET and/or ANCHOR_RELAY_OUTPUT_PATH before installing the shipper timer." >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  install -d "$SYSTEMD_DIR"
  cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Trading Bot audit anchor shipper
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
ExecStart=/bin/bash -lc 'cd "$ROOT_DIR" && exec "$PYTHON_BIN" scripts/ship_audit_anchor.py --input "$ANCHOR_INPUT_PATH" ${ANCHOR_REMOTE_TARGET:+--remote "$ANCHOR_REMOTE_TARGET"} ${ANCHOR_REMOTE_SSH_PORT:+--remote-port "$ANCHOR_REMOTE_SSH_PORT"} ${ANCHOR_RELAY_OUTPUT_PATH:+--output "$ANCHOR_RELAY_OUTPUT_PATH"}'
EOF

  cat >"$TIMER_PATH" <<EOF
[Unit]
Description=Run Trading Bot audit anchor shipper every 10 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
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
echo "*/10 * * * * cd $ROOT_DIR && $PYTHON_BIN scripts/ship_audit_anchor.py --input \"$ANCHOR_INPUT_PATH\" ${ANCHOR_REMOTE_TARGET:+--remote \"$ANCHOR_REMOTE_TARGET\"} ${ANCHOR_REMOTE_SSH_PORT:+--remote-port \"$ANCHOR_REMOTE_SSH_PORT\"} ${ANCHOR_RELAY_OUTPUT_PATH:+--output \"$ANCHOR_RELAY_OUTPUT_PATH\"}"
