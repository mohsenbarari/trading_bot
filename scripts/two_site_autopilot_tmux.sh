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
export PYTHONPATH="$root"

usage() {
  printf 'Usage: %s {start [--quota-wait-enabled]|status|stop|stop-now|logs}\n' "$0"
}

tmux_session_state() {
  local target="$1" status_path="$2" rc
  if tmux has-session -t "=$target" >/dev/null 2>&1; then
    printf 'PRESENT\n'
    return 0
  else
    rc=$?
  fi
  if [[ "$rc" -eq 1 && -f "$status_path" && ! -L "$status_path" ]] \
      && jq -e '.state == "STOPPED"' "$status_path" >/dev/null 2>&1; then
    printf 'ABSENT\n'
  else
    printf 'ERROR\n'
  fi
}

quota_args=()
broker_quota_args=()
if [[ "${1:-}" == "start" && "${2:-}" == "--quota-wait-enabled" && "$#" -eq 2 ]]; then
  quota_args=(--quota-wait-enabled)
  broker_quota_args=(--quota-continuation-enabled)
elif [[ "$#" -ne 1 ]]; then
  usage >&2
  exit 2
fi

case "${1:-}" in
  start)
    command -v tmux >/dev/null || { printf 'ERROR: tmux is unavailable\n' >&2; exit 2; }
    APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
      /usr/bin/python3 scripts/two_site_autopilot.py --root "$root" --check
    if [[ -e "$state_root/STOP" || -L "$state_root/STOP" || -e "$root/STOP" || -L "$root/STOP" ]]; then
      printf 'ERROR: STOP file present\n' >&2
      exit 2
    fi
    expected_broker_code_sha256="$(
      /usr/bin/python3 scripts/two_site_provider_broker.py --root "$root" code-identity \
        | /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); print(v.get("broker_code_sha256", ""))'
    )"
    if ! [[ "$expected_broker_code_sha256" =~ ^[0-9a-f]{64}$ ]]; then
      printf 'ERROR: current Broker code identity is invalid\n' >&2
      exit 2
    fi
    if tmux has-session -t "=$broker_session" 2>/dev/null; then
      existing_broker_status="$(/usr/bin/python3 scripts/two_site_provider_broker.py --root "$root" status)" || {
        printf 'ERROR: existing Broker status is unavailable\n' >&2
        exit 2
      }
      existing_broker_code_sha256="$(
        /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); print(v.get("broker_code_sha256", ""))' \
          <<<"$existing_broker_status"
      )"
      if [[ "$existing_broker_code_sha256" != "$expected_broker_code_sha256" ]]; then
        existing_broker_state="$(
          /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); print(v.get("state", ""))' \
            <<<"$existing_broker_status"
        )"
        if [[ "$existing_broker_state" == "IDLE" ]] \
            && ! tmux has-session -t "=$session" 2>/dev/null; then
          tmux kill-session -t "=$broker_session"
        else
          printf 'ERROR: stale Broker code cannot be reloaded outside an idle Supervisor boundary\n' >&2
          exit 2
        fi
      fi
    fi
    if ! tmux has-session -t "=$broker_session" 2>/dev/null; then
      printf -v broker_command '%q ' \
        /usr/bin/env APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
        TWO_SITE_QODER_FINAL_REVIEW_REQUIRED=1 \
        PYTHONPATH="$root" \
        /usr/bin/python3 "$root/scripts/two_site_provider_broker.py" --root "$root" serve "${broker_quota_args[@]}"
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
    quota_required=0
    if [[ "${#quota_args[@]}" -eq 1 ]]; then quota_required=1; fi
    broker_status_json="$(/usr/bin/python3 scripts/two_site_provider_broker.py --root "$root" status)" || {
      printf 'ERROR: Broker status is unavailable after startup\n' >&2
      exit 2
    }
    if ! /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); sys.exit(0 if v.get("state")=="IDLE" and v.get("qoder_final_review_required") is True else 2)' \
        <<<"$broker_status_json"; then
      printf 'ERROR: Broker policy, code identity, or idle-state validation failed\n' >&2
      exit 2
    fi
    if ! /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); sys.exit(0 if v.get("broker_code_sha256")==sys.argv[1] else 2)' \
        "$expected_broker_code_sha256" <<<"$broker_status_json"; then
      printf 'ERROR: Broker policy, code identity, or idle-state validation failed\n' >&2
      exit 2
    fi
    if [[ "$quota_required" == 1 ]] && ! /usr/bin/python3 -c 'import json,sys; v=json.load(sys.stdin); sys.exit(0 if v.get("quota_continuation_ready") is True else 2)' \
        <<<"$broker_status_json"; then
      printf 'ERROR: existing Broker is not quota-enabled; no live Broker will be replaced\n' >&2
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
        PYTHONPATH="$root" \
        /usr/bin/python3 "$root/scripts/two_site_autopilot.py" --root "$root" "${quota_args[@]}"
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
    /usr/bin/python3 "$root/scripts/two_site_controlled_stop.py" --root "$root"
    if [[ ! -f "$state_root/STOP" || -L "$state_root/STOP" ]]; then
      printf 'ERROR: durable STOP publication was not verified\n' >&2
      exit 2
    fi
    printf 'AUTOPILOT=STOP_REQUESTED session=%s\n' "$session"
    ;;
  stop-now)
    # Emergency requests must still preserve a completed Coordinator terminal
    # frame and Supervisor finalization.  Publish the same durable STOP used by
    # the controlled path and never tear down the live Supervisor session.
    /usr/bin/python3 "$root/scripts/two_site_controlled_stop.py" --root "$root"
    if [[ ! -f "$state_root/STOP" || -L "$state_root/STOP" ]]; then
      printf 'ERROR: durable STOP publication was not verified\n' >&2
      exit 2
    fi
    stopped=false
    for _attempt in {1..240}; do
      session_state="$(tmux_session_state "$session" "$state_root/status.json")"
      if [[ "$session_state" == "ERROR" ]]; then
        printf 'ERROR: Supervisor tmux state is unavailable\n' >&2
        exit 2
      fi
      if [[ "$session_state" == "ABSENT" ]]; then
        stopped=true
        break
      fi
      /usr/bin/sleep 0.5
    done
    if [[ "$stopped" != true ]]; then
      printf 'AUTOPILOT=STOP_REQUESTED session=%s pending=supervisor-safe-boundary\n' "$session"
      exit 2
    fi
    broker_state="$(tmux_session_state "$broker_session" "$root/.local/two-site-refactor/provider-broker/status.json")"
    if [[ "$broker_state" == "ERROR" ]]; then
      printf 'ERROR: Broker tmux state is unavailable\n' >&2
      exit 2
    fi
    if [[ "$broker_state" == "PRESENT" ]]; then
      # The Broker owns its STOPPED transition and exits naturally only after
      # observing both durable STOP and the Supervisor's STOPPED status.  Never
      # race that lock/state transition with an external check-then-kill.
      broker_stopped=false
      for _attempt in {1..80}; do
        broker_state="$(tmux_session_state "$broker_session" "$root/.local/two-site-refactor/provider-broker/status.json")"
        if [[ "$broker_state" == "ERROR" ]]; then
          printf 'ERROR: Broker tmux state is unavailable\n' >&2
          exit 2
        fi
        if [[ "$broker_state" == "ABSENT" ]]; then
          broker_stopped=true
          break
        fi
        /usr/bin/sleep 0.25
      done
      if [[ "$broker_stopped" != true ]]; then
        printf 'AUTOPILOT=STOP_REQUESTED session=%s pending=broker-safe-boundary\n' "$session"
        exit 2
      fi
    fi
    # Re-open and revalidate the complete root/.local/refactor/autopilot chain,
    # STOP inode, owner, mode and bytes after all waits; this also detects a
    # concurrently replaced parent rather than checking only the leaf name.
    /usr/bin/python3 "$root/scripts/two_site_controlled_stop.py" --root "$root"
    session_state="$(tmux_session_state "$session" "$state_root/status.json")"
    broker_state="$(tmux_session_state "$broker_session" "$root/.local/two-site-refactor/provider-broker/status.json")"
    if [[ "$session_state" == "ERROR" || "$broker_state" == "ERROR" ]]; then
      printf 'ERROR: final tmux state is unavailable\n' >&2
      exit 2
    fi
    if [[ "$session_state" != "ABSENT" || "$broker_state" != "ABSENT" ]]; then
      printf 'ERROR: automation sessions remain after safe stop\n' >&2
      exit 2
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
