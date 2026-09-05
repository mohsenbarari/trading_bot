#!/usr/bin/env bash
set -euo pipefail
umask 077

session="two-site-autopilot"
root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  printf 'ERROR: not inside the integration repository\n' >&2
  exit 2
}
root="$(realpath -e "$root")"
state_root="$root/.local/two-site-refactor/autopilot"

usage() {
  printf 'Usage: %s {start|status|stop|stop-now|logs}\n' "$0"
}

case "${1:-}" in
  start)
    command -v tmux >/dev/null || { printf 'ERROR: tmux is unavailable\n' >&2; exit 2; }
    if tmux has-session -t "=$session" 2>/dev/null; then
      printf 'AUTOPILOT=ALREADY_RUNNING session=%s\n' "$session"
      exit 0
    fi
    APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
      /usr/bin/python3 scripts/two_site_codex_autopilot.py --root "$root" --check
    /usr/bin/rm -f -- "$state_root/STOP"
    printf -v command '%q ' \
      /usr/bin/env APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
      /usr/bin/python3 "$root/scripts/two_site_codex_autopilot.py" --root "$root"
    tmux new-session -d -s "$session" -c "$root" "$command"
    printf 'AUTOPILOT=STARTED session=%s\n' "$session"
    ;;
  status)
    if tmux has-session -t "=$session" 2>/dev/null; then
      printf 'AUTOPILOT=RUNNING session=%s\n' "$session"
    else
      printf 'AUTOPILOT=NOT_RUNNING session=%s\n' "$session"
    fi
    if [[ -s "$state_root/status.json" ]]; then
      jq . "$state_root/status.json"
    fi
    ;;
  stop)
    mkdir -p -- "$state_root"
    /usr/bin/touch "$state_root/STOP"
    printf 'AUTOPILOT=STOP_REQUESTED session=%s\n' "$session"
    ;;
  stop-now)
    mkdir -p -- "$state_root"
    /usr/bin/touch "$state_root/STOP"
    if tmux has-session -t "=$session" 2>/dev/null; then
      tmux kill-session -t "=$session"
    fi
    printf 'AUTOPILOT=STOPPED session=%s\n' "$session"
    ;;
  logs)
    tmux capture-pane -p -t "$session:0.0" -S -200
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
