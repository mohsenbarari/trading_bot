#!/usr/bin/env bash
set -euo pipefail
umask 077

session="two-site-autopilot"
broker_session="two-site-provider-broker"
script_path="$(realpath -e -- "${BASH_SOURCE[0]:-$0}" 2>/dev/null || true)"
if [[ -z "$script_path" ]]; then
  printf 'ERROR: not inside the integration repository\n' >&2
  exit 2
fi
script_dir="$(dirname -- "$script_path")"
root="$(realpath -e -- "$script_dir/.." 2>/dev/null || true)"
if [[ -z "$root" ]]; then
  printf 'ERROR: not inside the integration repository\n' >&2
  exit 2
fi
git_top="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)"
git_top="$(realpath -e -- "$git_top" 2>/dev/null || true)"
if [[ -z "$git_top" || "$git_top" != "$root" || "$(basename -- "$script_dir")" != "scripts" ]]; then
  printf 'ERROR: not inside the integration repository\n' >&2
  exit 2
fi

apparent_dir="$(dirname -- "$0")"
apparent_git="$(git -C "$apparent_dir" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$apparent_git" ]]; then
  apparent_git="$(realpath -e -- "$apparent_git" 2>/dev/null || true)"
  if [[ -n "$apparent_git" && "$apparent_git" != "$root" ]]; then
    printf 'ERROR: symlink alias selects ambiguous repository root\n' >&2
    exit 2
  fi
fi

branch="$(git -C "$root" symbolic-ref --short HEAD 2>/dev/null || git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ "$branch" != "refactor/two-site-architecture" ]]; then
  printf 'ERROR: branch must be refactor/two-site-architecture\n' >&2
  exit 2
fi

cd "$root"
state_root="$root/.local/two-site-refactor/autopilot"

usage() {
  printf 'Usage: %s {start|status|stop|stop-now|logs}\n' "$0"
}

case "${1:-}" in
  start)
    command -v tmux >/dev/null || { printf 'ERROR: tmux is unavailable\n' >&2; exit 2; }
    APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
      /usr/bin/python3 scripts/two_site_codex_autopilot.py --root "$root" --check
    if [[ -e "$state_root/STOP" || -L "$state_root/STOP" || -e "$root/STOP" || -L "$root/STOP" ]]; then
      printf 'ERROR: STOP file present\n' >&2
      exit 2
    fi
    if ! tmux has-session -t "=$broker_session" 2>/dev/null; then
      printf -v broker_command '%q ' \
        /usr/bin/env APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
        /usr/bin/python3 "$root/scripts/two_site_provider_broker.py" --root "$root" serve
      tmux new-session -d -s "$broker_session" -c "$root" "$broker_command"
    fi
    broker_ready=false
    for _attempt in {1..20}; do
      if /usr/bin/python3 scripts/two_site_provider_broker.py --root "$root" status >/dev/null 2>&1; then
        broker_ready=true
        break
      fi
      /usr/bin/sleep 0.25
    done
    if [[ "$broker_ready" != true ]]; then
      printf 'ERROR: provider broker did not become healthy\n' >&2
      exit 2
    fi
    if tmux has-session -t "=$session" 2>/dev/null; then
      printf 'PROVIDER_BROKER=RUNNING session=%s\n' "$broker_session"
      printf 'AUTOPILOT=ALREADY_RUNNING session=%s\n' "$session"
    else
      if [[ -e "$state_root/STOP" || -L "$state_root/STOP" || -e "$root/STOP" || -L "$root/STOP" ]]; then
        printf 'ERROR: STOP file present\n' >&2
        exit 2
      fi
      printf -v command '%q ' \
        /usr/bin/env APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
        /usr/bin/python3 "$root/scripts/two_site_codex_autopilot.py" --root "$root"
      tmux new-session -d -s "$session" -c "$root" "$command"
      printf 'PROVIDER_BROKER=RUNNING session=%s\n' "$broker_session"
      printf 'AUTOPILOT=STARTED session=%s\n' "$session"
    fi
    ;;
  status)
    if tmux has-session -t "=$session" 2>/dev/null; then
      printf 'AUTOPILOT=RUNNING session=%s\n' "$session"
    else
      printf 'AUTOPILOT=NOT_RUNNING session=%s\n' "$session"
    fi
    if [[ -f "$root/scripts/two_site_provider_broker.py" ]] \
        && /usr/bin/python3 scripts/two_site_provider_broker.py --root "$root" status; then
      printf 'PROVIDER_BROKER=RUNNING session=%s\n' "$broker_session"
    else
      printf 'PROVIDER_BROKER=NOT_RUNNING session=%s\n' "$broker_session"
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
    if tmux has-session -t "=$broker_session" 2>/dev/null; then
      tmux kill-session -t "=$broker_session"
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
