#!/usr/bin/env bash
set -u -o pipefail
umask 077

integration_root="/root/trading-bot/trading_bot/.local/two-site-refactor/integration"
state_root="$integration_root/.local/two-site-refactor/autopilot"
report_root="$integration_root/.local/two-site-refactor/monitor"
status_file="$state_root/status.json"
session="two-site-autopilot"
expected_main="cd86fb928dac3755fd25bd2af2245345d7979d7d"
expected_origin_main="4fcb319820986438f159635a339aec649aea371c"
expected_candidate="c3fe6ae72038c0059ef04474d908e819785bef07"
recovery_file="$report_root/recovery.json"
antigravity_cli="/root/trading-bot/trading_bot/.local/tools/agy-1.1.27-93eb2118b778a400.pin"
expected_antigravity_sha256="93eb2118b778a4005700b54cdd7e08b896fbe665d5ff338e38e9e53da9a091ea"

cd "$integration_root" || exit 1

mkdir -p -- "$report_root"
exec 9>"$report_root/monitor.lock"
if ! /usr/bin/flock -n 9; then
  exit 0
fi

timestamp="$(/usr/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
now_epoch="$(/usr/bin/date +%s)"

session_running=false
tmux_observation="error"
tmux_probe="$(/usr/bin/mktemp "$report_root/.tmux-probe.XXXXXX")"
if /usr/bin/timeout 5 /usr/bin/tmux has-session -t "=$session" 2>"$tmux_probe"; then
  session_running=true
  tmux_observation="present"
else
  tmux_exit="$?"
  tmux_error="$(/usr/bin/head -c 1024 "$tmux_probe" 2>/dev/null || true)"
  if [[ "$tmux_exit" == "1" ]] && {
      [[ "$tmux_error" == *"can't find session"* ]] ||
      [[ "$tmux_error" == *"no server running"* ]];
    }; then
    tmux_observation="absent"
  fi
fi
/usr/bin/unlink -- "$tmux_probe" 2>/dev/null || true

supervisor_count="$(/usr/bin/pgrep -fc "/usr/bin/python3 $integration_root/scripts/two_site_codex_autopilot.py --root $integration_root" 2>/dev/null || true)"
broker_count="$(/usr/bin/pgrep -fc "/usr/bin/python3 $integration_root/scripts/two_site_provider_broker.py --root $integration_root serve" 2>/dev/null || true)"

state="UNKNOWN"
cycle="-"
status_head="-"
status_age_seconds="-1"
blockers_text=""
status_dirty="false"
status_ambiguous="false"
if [[ -f "$status_file" && ! -L "$status_file" ]]; then
  status_size="$(/usr/bin/stat -c %s -- "$status_file" 2>/dev/null || printf '65537')"
  status_links="$(/usr/bin/stat -c %h -- "$status_file" 2>/dev/null || printf '2')"
  if [[ "$status_size" =~ ^[0-9]+$ && "$status_links" == "1" && "$status_size" -le 65536 ]]; then
    state="$(/usr/bin/jq -r '.state // "UNKNOWN"' "$status_file" 2>/dev/null || printf 'INVALID')"
    cycle="$(/usr/bin/jq -r '.cycle // "-"' "$status_file" 2>/dev/null || printf '-')"
    status_head="$(/usr/bin/jq -r '.head // "-"' "$status_file" 2>/dev/null || printf '-')"
    blockers_text="$(/usr/bin/jq -r '[.blockers[]? | select(type == "string")] | join(" ")' "$status_file" 2>/dev/null || true)"
    status_dirty="$(/usr/bin/jq -r '.dirty // false' "$status_file" 2>/dev/null || printf 'false')"
    status_ambiguous="$(/usr/bin/jq -r '.ambiguous_change // false' "$status_file" 2>/dev/null || printf 'false')"
    status_mtime="$(/usr/bin/stat -c %Y -- "$status_file" 2>/dev/null || printf '0')"
    if [[ "$status_mtime" =~ ^[0-9]+$ && "$status_mtime" -gt 0 ]]; then
      status_age_seconds="$((now_epoch - status_mtime))"
    fi
  fi
fi

phase="idle"
if /usr/bin/pgrep -f "$integration_root/scripts/two_site_proposal_pipeline.py" >/dev/null 2>&1; then
  phase="gemini_pipeline"
elif /usr/bin/pgrep -f "(/codex|/codex-[^/ ]+\.pin) exec .*--cd $integration_root( |$)" >/dev/null 2>&1; then
  phase="codex_coordinator"
elif [[ "$supervisor_count" -gt 0 ]]; then
  phase="supervisor"
fi

git_clean="unknown"
git_head="-"
git_branch="-"
if [[ -d "$integration_root/.git" || -f "$integration_root/.git" ]]; then
  git_head="$(/usr/bin/timeout 20 /usr/bin/git -C "$integration_root" rev-parse --short=8 HEAD 2>/dev/null || printf '-')"
  git_branch="$(/usr/bin/timeout 20 /usr/bin/git -C "$integration_root" branch --show-current 2>/dev/null || printf '-')"
  tracked_changes="$(/usr/bin/timeout 20 /usr/bin/git -C "$integration_root" status --porcelain --untracked-files=normal 2>/dev/null || printf '__CHECK_FAILED__')"
  if [[ -z "$tracked_changes" ]]; then
    git_clean="true"
  elif [[ "$tracked_changes" == "__CHECK_FAILED__" ]]; then
    git_clean="unknown"
  else
    git_clean="false"
  fi
fi
short_status_head="${status_head:0:8}"

protected_refs_ok=false
current_main="$(/usr/bin/timeout 20 /usr/bin/git -C "$integration_root" show-ref --verify --hash refs/heads/main 2>/dev/null || true)"
current_origin_main="$(/usr/bin/timeout 20 /usr/bin/git -C "$integration_root" show-ref --verify --hash refs/remotes/origin/main 2>/dev/null || true)"
current_candidate="$(/usr/bin/timeout 20 /usr/bin/git -C "$integration_root" show-ref --verify --hash refs/heads/candidate/wa-ir-standby-v1 2>/dev/null || true)"
if [[ "$current_main" == "$expected_main" && "$current_origin_main" == "$expected_origin_main" && "$current_candidate" == "$expected_candidate" ]]; then
  protected_refs_ok=true
fi

health="DEGRADED"
if [[ "$tmux_observation" == "present" && "$supervisor_count" -gt 0 \
      && "$broker_count" -eq 1 && "$state" == "RUNNING" ]]; then
  health="HEALTHY"
elif [[ "$tmux_observation" == "absent" && "$state" == "COMPLETE" ]]; then
  health="COMPLETE"
elif [[ "$state" == "BLOCKED" ]]; then
  health="BLOCKED"
elif [[ "$state" == "STOPPED" ]]; then
  health="STOPPED"
fi

action="none"
recovery_attempts=0
next_attempt_epoch=0
recovery_reason="none"
if [[ -f "$recovery_file" && ! -L "$recovery_file" ]]; then
  recovery_size="$(/usr/bin/stat -c %s -- "$recovery_file" 2>/dev/null || printf '65537')"
  recovery_links="$(/usr/bin/stat -c %h -- "$recovery_file" 2>/dev/null || printf '2')"
  if [[ "$recovery_size" =~ ^[0-9]+$ && "$recovery_links" == "1" && "$recovery_size" -le 65536 ]]; then
    saved_head="$(/usr/bin/jq -r '.head // ""' "$recovery_file" 2>/dev/null || true)"
    saved_reason="$(/usr/bin/jq -r '.reason // ""' "$recovery_file" 2>/dev/null || true)"
    if [[ "$saved_head" == "$git_head" ]]; then
      recovery_attempts="$(/usr/bin/jq -r '.attempts // 0' "$recovery_file" 2>/dev/null || printf '0')"
      next_attempt_epoch="$(/usr/bin/jq -r '.next_attempt_epoch // 0' "$recovery_file" 2>/dev/null || printf '0')"
      recovery_reason="$saved_reason"
    fi
  fi
fi
if ! [[ "$recovery_attempts" =~ ^[0-9]+$ ]]; then recovery_attempts=0; fi
if ! [[ "$next_attempt_epoch" =~ ^[0-9]+$ ]]; then next_attempt_epoch=0; fi

restart_eligible=false
provider_probe=false
status_head_compatible=false
standing_transfer_authorized=false
if [[ "$blockers_text" == *"source/task"* \
      && "$blockers_text" == *"Antigravity/Gemini"* \
      && -f "$integration_root/docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md" ]] \
    && /usr/bin/grep -Fq -- "permanent authorization, without per-transfer confirmation" \
      "$integration_root/docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md"; then
  standing_transfer_authorized=true
fi
if [[ "$git_head" == "$short_status_head" ]]; then
  status_head_compatible=true
elif [[ "$state" == "BLOCKED" && "$status_head" == "-"
        && "$blockers_text" == *"bounded maximum cycle count reached"* ]]; then
  # The current Supervisor omits HEAD only for its bounded cycle-budget stop.
  status_head_compatible=true
fi
if [[ "$tmux_observation" == "absent" && "$supervisor_count" -eq 0
      && "$git_clean" == true && "$git_branch" == "refactor/two-site-architecture"
      && "$status_head_compatible" == true && "$protected_refs_ok" == true
      && ! -e "$state_root/STOP" ]]; then
  if [[ "$state" == "RUNNING" || "$state" == "RETRYABLE_FAILURE" ]]; then
    restart_eligible=true
    recovery_reason="unexpected_process_exit"
  elif [[ "$state" == "BLOCKED" && "$status_dirty" == "false"
          && "$status_ambiguous" == "false" ]]; then
    blockers_lower="${blockers_text,,}"
    if [[ "$blockers_lower" =~ expired|expiry ]]; then
      action="assignment_expired"
    elif [[ "$blockers_lower" == *"approval review"* \
          && "$blockers_lower" == *"model"* && "$blockers_lower" == *"capacity"* ]]; then
      # Legacy nested-escalation blocker: the local provider broker removes this dependency.
      restart_eligible=true
      recovery_reason="legacy_nested_approval_capacity"
    elif [[ "$blockers_lower" =~ approval.*denied|denied.*approval|approval.*rejected|rejected.*approval|escalation.*denied|denied.*escalation|escalation.*rejected|rejected.*escalation|halt|external_gate|external.?gate ]]; then
      action="authority_blocked"
    elif [[ ! "$blockers_lower" =~ dirty|ambiguous|protected.?ref|ref.?drift|production|deploy|dns|web.?writer|secret|credential|authority ]] \
        && { [[ ! "$blockers_lower" =~ denied.?escalation|approval.?required|explicit.?authorization|insufficient.?egress.?authorization ]] \
          || [[ "$standing_transfer_authorized" == true ]]; }; then
      restart_eligible=true
      recovery_reason="clean_blocked_state"
      if [[ "$blockers_text" == *"MODEL_OUTPUT_TRUNCATED"* ]]; then
        recovery_reason="model_output_truncated"
      elif [[ "$blockers_text" == *"tool attempt invalidates a proposal-only run"* ]]; then
        recovery_reason="invalid_model_submission"
      elif [[ "$blockers_text" == *"bounded maximum cycle count reached"* ]]; then
        recovery_reason="cycle_budget_exhausted"
      fi
      if [[ "$blockers_lower" =~ antigravity|gemini|provider|authentication|cli.?did.?not.?complete ]]; then
        provider_probe=true
      fi
    fi
  fi
fi

if [[ "$restart_eligible" == false && "$tmux_observation" == "present"
      && "$supervisor_count" -gt 0 && "$broker_count" -eq 0
      && "$git_clean" == true && "$git_branch" == "refactor/two-site-architecture"
      && "$protected_refs_ok" == true && ! -e "$state_root/STOP" ]]; then
  restart_eligible=true
  recovery_reason="provider_broker_missing"
fi

if [[ "$restart_eligible" == true ]]; then
  if [[ "$now_epoch" -lt "$next_attempt_epoch" ]]; then
    action="backoff"
  else
    recovery_ok=true
    if [[ "$provider_probe" == true ]]; then
      probe_stdout_tmp="$(/usr/bin/mktemp "$report_root/.provider-preflight.stdout.XXXXXX")"
      probe_stderr_tmp="$(/usr/bin/mktemp "$report_root/.provider-preflight.stderr.XXXXXX")"
      /usr/bin/chmod 0600 "$probe_stdout_tmp" "$probe_stderr_tmp" 2>/dev/null || true
      probe_cli_dir="$(/usr/bin/mktemp -d "$report_root/.provider-cli.XXXXXX")"
      probe_cli="$probe_cli_dir/agy"
      cli_sha256="$(/usr/bin/sha256sum -- "$antigravity_cli" 2>/dev/null | /usr/bin/awk '{print $1}' || true)"
      if /usr/bin/cp --reflink=auto -- "$antigravity_cli" "$probe_cli" 2>/dev/null \
          && /usr/bin/chmod 700 "$probe_cli" \
          && [[ "$cli_sha256" == "$expected_antigravity_sha256" ]] \
          && [[ "$(/usr/bin/sha256sum -- "$probe_cli" 2>/dev/null | /usr/bin/awk '{print $1}' || true)" == "$expected_antigravity_sha256" ]] \
          && [[ "$(AGY_CLI_DISABLE_AUTO_UPDATE=true /usr/bin/timeout 10 "$probe_cli" --version 2>/dev/null || true)" == "1.1.27" ]] \
          && (ulimit -f 2048; AGY_CLI_DISABLE_AUTO_UPDATE=true /usr/bin/timeout 150 "$probe_cli" \
            --model gemini-3.8-flash-high --effort high --sandbox --mode plan \
            --disable-slash-commands --output-format stream-json --print-timeout 2m \
            -p 'Health probe only. Do not invoke tools and do not read or write files. Reply exactly AUTOPILOT_PROVIDER_OK' \
            >"$probe_stdout_tmp" 2>"$probe_stderr_tmp") \
          && [[ "$(/usr/bin/stat -c %s -- "$probe_stdout_tmp" 2>/dev/null || printf '1048577')" -le 1048576 ]] \
          && [[ "$(/usr/bin/stat -c %s -- "$probe_stderr_tmp" 2>/dev/null || printf '1048577')" -le 1048576 ]] \
          && /usr/bin/jq -s -e --arg root "$integration_root" '
            ([.[] | select(.event == "init")] | length) == 1 and
            ([.[] | select(.event == "result")] | length) == 1 and
            ([.[] | select(.event == "init")][0]) as $init |
            ([.[] | select(.event == "result")][0].result) as $result |
            $init.init.cwd == $root and
            $init.init.model == "gemini-3.8-flash-high" and
            $init.init.permission_mode == "request-review" and
            $result.conversation_id == $init.conversation_id and
            $result.status == "SUCCESS" and
            ($result.error == null or $result.error == "") and
            ($result.response | gsub("^[[:space:]]+|[[:space:]]+$"; "")) == "AUTOPILOT_PROVIDER_OK" and
            all(.[];
              (.step_update.tool_name? // "") == "" and
              (.step_update.tool_info? // "") == "" and
              (.step_update.subagent_info? // "") == ""
            )
          ' "$probe_stdout_tmp" >/dev/null \
          && [[ "$(/usr/bin/sha256sum -- "$antigravity_cli" 2>/dev/null | /usr/bin/awk '{print $1}' || true)" == "$expected_antigravity_sha256" ]]; then
        :
      else
        recovery_ok=false
      fi
      if ! /usr/bin/mv -f -- "$probe_stdout_tmp" "$report_root/provider-preflight.latest.log" 2>/dev/null; then
        recovery_ok=false
      fi
      if ! /usr/bin/mv -f -- "$probe_stderr_tmp" "$report_root/provider-preflight.latest.stderr.log" 2>/dev/null; then
        recovery_ok=false
      fi
      if ! /usr/bin/chmod 0600 "$report_root/provider-preflight.latest.log" 2>/dev/null; then
        recovery_ok=false
      fi
      if ! /usr/bin/chmod 0600 "$report_root/provider-preflight.latest.stderr.log" 2>/dev/null; then
        recovery_ok=false
      fi
      if [[ ! -f "$report_root/provider-preflight.latest.log" || -L "$report_root/provider-preflight.latest.log" ]] \
          || [[ "$(/usr/bin/stat -c %a -- "$report_root/provider-preflight.latest.log" 2>/dev/null || printf '0')" != "600" ]] \
          || [[ "$(/usr/bin/stat -c %s -- "$report_root/provider-preflight.latest.log" 2>/dev/null || printf '1048577')" -gt 1048576 ]]; then
        recovery_ok=false
      fi
      if [[ ! -f "$report_root/provider-preflight.latest.stderr.log" || -L "$report_root/provider-preflight.latest.stderr.log" ]] \
          || [[ "$(/usr/bin/stat -c %a -- "$report_root/provider-preflight.latest.stderr.log" 2>/dev/null || printf '0')" != "600" ]] \
          || [[ "$(/usr/bin/stat -c %s -- "$report_root/provider-preflight.latest.stderr.log" 2>/dev/null || printf '1048577')" -gt 1048576 ]]; then
        recovery_ok=false
      fi
      /usr/bin/unlink -- "$probe_stdout_tmp" 2>/dev/null || true
      /usr/bin/unlink -- "$probe_stderr_tmp" 2>/dev/null || true
      /usr/bin/unlink -- "$probe_cli" 2>/dev/null || true
      /usr/bin/rmdir -- "$probe_cli_dir" 2>/dev/null || true
    fi
    if [[ "$recovery_ok" == true ]]; then
      restart_tmp="$(/usr/bin/mktemp "$report_root/.restart.XXXXXX")"
      if /usr/bin/timeout 45 "$integration_root/scripts/two_site_autopilot_tmux.sh" start >"$restart_tmp" 2>&1; then
        action="restart_started"
        health="RECOVERING"
      else
        action="restart_failed"
      fi
      /usr/bin/mv -f -- "$restart_tmp" "$report_root/restart.latest.log"
      /usr/bin/chmod 600 "$report_root/restart.latest.log"
    else
      action="provider_preflight_failed"
    fi

    recovery_attempts="$((recovery_attempts + 1))"
    backoff_minutes=15
    if [[ "$recovery_attempts" -eq 2 ]]; then backoff_minutes=30; fi
    if [[ "$recovery_attempts" -eq 3 ]]; then backoff_minutes=60; fi
    if [[ "$recovery_attempts" -eq 4 ]]; then backoff_minutes=120; fi
    if [[ "$recovery_attempts" -ge 5 ]]; then backoff_minutes=240; fi
    next_attempt_epoch="$((now_epoch + backoff_minutes * 60))"
    recovery_tmp="$(/usr/bin/mktemp "$report_root/.recovery.XXXXXX")"
    /usr/bin/jq -n --arg head "$git_head" --arg reason "$recovery_reason" \
      --argjson attempts "$recovery_attempts" --argjson next "$next_attempt_epoch" \
      '{head:$head,reason:$reason,attempts:$attempts,next_attempt_epoch:$next}' >"$recovery_tmp"
    /usr/bin/chmod 600 "$recovery_tmp"
    /usr/bin/mv -f -- "$recovery_tmp" "$recovery_file"
  fi
fi

report="$timestamp health=$health state=$state cycle=$cycle phase=$phase session=$session_running tmux_observation=$tmux_observation supervisor=$supervisor_count broker=$broker_count git_branch=$git_branch git_head=$git_head status_head=$short_status_head git_clean=$git_clean protected_refs=$protected_refs_ok action=$action recovery_attempts=$recovery_attempts status_age_seconds=$status_age_seconds"

latest_tmp="$(/usr/bin/mktemp "$report_root/.latest.XXXXXX")"
/usr/bin/printf '%s\n' "$report" >"$latest_tmp"
/usr/bin/chmod 600 "$latest_tmp"
/usr/bin/mv -f -- "$latest_tmp" "$report_root/latest.txt"
/usr/bin/printf '%s\n' "$report" >>"$report_root/history.log"

history_size="$(/usr/bin/stat -c %s -- "$report_root/history.log" 2>/dev/null || printf '0')"
if [[ "$history_size" =~ ^[0-9]+$ && "$history_size" -gt 262144 ]]; then
  history_tmp="$(/usr/bin/mktemp "$report_root/.history.XXXXXX")"
  /usr/bin/tail -n 672 "$report_root/history.log" >"$history_tmp"
  /usr/bin/chmod 600 "$history_tmp"
  /usr/bin/mv -f -- "$history_tmp" "$report_root/history.log"
fi

/usr/bin/logger -t two-site-autopilot-monitor -- "$report" 2>/dev/null || true
/usr/bin/printf '%s\n' "$report"
