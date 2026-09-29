import json
from pathlib import Path
import subprocess
import pytest

MONITOR = Path("scripts/two_site_autopilot_monitor.sh")

def test_bash_syntax():
    subprocess.run(["bash", "-n", str(MONITOR)], check=True)


def test_monitor_requires_and_recovers_the_provider_broker():
    src = MONITOR.read_text()
    assert 'broker_count="$(/usr/bin/pgrep -fc "/usr/bin/python3 $integration_root/scripts/two_site_provider_broker.py' in src
    assert '&& "$broker_count" -eq 1 && "$state" == "RUNNING"' in src
    assert 'recovery_reason="provider_broker_missing"' in src
    assert 'broker=$broker_count' in src


def test_monitor_reports_liveness_without_restarting_a_live_stale_supervisor():
    src = MONITOR.read_text()
    assert 'running_heartbeat_max_age_seconds=90' in src
    assert 'liveness="STALE"' in src
    assert 'health="STALE"' in src
    assert 'action="liveness_stale_no_restart"' in src
    assert 'status_phase=$status_phase' in src
    assert 'phase_age_seconds=$phase_age_seconds' in src
    assert 'heartbeat_age_seconds=$heartbeat_age_seconds' in src
    assert 'liveness=$liveness' in src

CASES = [
    ("Assignment expired", False, "assignment_expired", "false"),
    ("approval denied", False, "authority_blocked", "false"),
    ("denied escalation", False, "authority_blocked", "false"),
    ("explicit HALT", False, "authority_blocked", "false"),
    ("source/task Antigravity/Gemini approval denied", True, "authority_blocked", "false"),
    ("DENIED_ESCALATION automatic approval review model was at capacity", False, "none", "true"),
    ("MODEL_OUTPUT_TRUNCATED", False, "none", "true"),
    ("bounded maximum cycle count reached", False, "none", "true"),
]

@pytest.mark.parametrize("blockers,standing,exp_action,exp_restart", CASES)
def test_blocked_branch(blockers, standing, exp_action, exp_restart):
    src = MONITOR.read_text()
    start = src.index('elif [[ "$state" == "BLOCKED" && "$status_dirty" == "false"')
    end = src.index('\nfi\n\nif [[ "$restart_eligible"')
    branch = src[start:end]
    standing_str = "true" if standing else "false"
    script = (
        'action="none"\nrestart_eligible=false\nprovider_probe=false\n'
        'state="BLOCKED"\nstatus_dirty="false"\nstatus_ambiguous="false"\n'
        f'standing_transfer_authorized={standing_str}\n'
        f'blockers_text="{blockers}"\n'
        f'if false; then :\n{branch}\n'
        'printf "action=%s restart_eligible=%s" "$action" "$restart_eligible"\n'
    )
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert res.stdout == f"action={exp_action} restart_eligible={exp_restart}"


def extract_preflight_validation_fragment() -> str:
    src = MONITOR.read_text()
    start = src.index('[[ "$(/usr/bin/stat -c %s -- "$probe_stdout_tmp"')
    end = src.index('&& [[ "$(/usr/bin/sha256sum -- "$antigravity_cli"', start)
    frag = src[start:end].rstrip()
    if frag.endswith("\\"):
        frag = frag[:-1].rstrip()
    return frag


def extract_publication_fragment() -> str:
    src = MONITOR.read_text()
    start = src.index('if ! /usr/bin/mv -f -- "$probe_stdout_tmp"')
    end = src.index('/usr/bin/unlink -- "$probe_stdout_tmp"', start)
    return src[start:end].strip()


def make_ndjson(events: list[dict]) -> str:
    return "\n".join(json.dumps(ev) for ev in events) + "\n"


def make_valid_events(
    root: str = "/root/test",
    conv_id: str = "conv-12345",
    model: str = "gemini-3.8-flash-high",
    permission_mode: str = "request-review",
    status: str = "SUCCESS",
    error: str | None = None,
    response: str = "AUTOPILOT_PROVIDER_OK",
) -> list[dict]:
    return [
        {
            "event": "init",
            "conversation_id": conv_id,
            "init": {
                "cwd": root,
                "model": model,
                "permission_mode": permission_mode,
            },
        },
        {
            "event": "result",
            "conversation_id": conv_id,
            "result": {
                "conversation_id": conv_id,
                "status": status,
                "error": error,
                "response": response,
            },
        },
    ]


def run_validation_fragment(
    tmp_path: Path,
    stdout_content: str | bytes,
    stderr_content: str | bytes = "",
    root: str = "/root/test",
) -> subprocess.CompletedProcess:
    tmp_path.mkdir(parents=True, exist_ok=True)
    stdout_file = tmp_path / "stdout.tmp"
    stderr_file = tmp_path / "stderr.tmp"
    if isinstance(stdout_content, str):
        stdout_file.write_text(stdout_content, encoding="utf-8")
    else:
        stdout_file.write_bytes(stdout_content)
    if isinstance(stderr_content, str):
        stderr_file.write_text(stderr_content, encoding="utf-8")
    else:
        stderr_file.write_bytes(stderr_content)

    frag = extract_preflight_validation_fragment()
    script = (
        f'integration_root="{root}"\n'
        f'probe_stdout_tmp="{stdout_file}"\n'
        f'probe_stderr_tmp="{stderr_file}"\n'
        f'if {frag}; then\n'
        '  exit 0\n'
        'else\n'
        '  exit 1\n'
        'fi\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def run_publication_fragment(
    report_root: Path,
    stdout_tmp: Path,
    stderr_tmp: Path,
) -> tuple[bool, subprocess.CompletedProcess]:
    frag = extract_publication_fragment()
    script = (
        'recovery_ok=true\n'
        f'report_root="{report_root}"\n'
        f'probe_stdout_tmp="{stdout_tmp}"\n'
        f'probe_stderr_tmp="{stderr_tmp}"\n'
        f'{frag}\n'
        'printf "recovery_ok=%s" "$recovery_ok"\n'
    )
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return ("recovery_ok=true" in res.stdout), res


def test_preflight_channel_separation_and_benign_warning(tmp_path):
    root = "/root/test-root"
    valid_stdout = make_ndjson(make_valid_events(root=root))
    benign_warning = "WARNING: Auto-update is disabled (AGY_CLI_DISABLE_AUTO_UPDATE=true)\n"

    clean_dir = tmp_path / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    res_clean = run_validation_fragment(
        clean_dir,
        stdout_content=valid_stdout,
        stderr_content=benign_warning,
        root=root,
    )
    assert res_clean.returncode == 0, f"Expected clean stdout + warning on stderr to pass: {res_clean.stderr}"

    polluted_dir = tmp_path / "polluted"
    polluted_dir.mkdir(parents=True, exist_ok=True)
    res_polluted = run_validation_fragment(
        polluted_dir,
        stdout_content=benign_warning + valid_stdout,
        stderr_content="",
        root=root,
    )
    assert res_polluted.returncode != 0, "Expected stdout prepended with warning to fail jq validation"


INVALID_PREFLIGHT_CASES = [
    ("malformed_stdout", "not json at all\n"),
    ("truncated_json", '{"event": "init", "conversation_id": "conv-1", "init":\n'),
    ("missing_init", make_ndjson(make_valid_events()[1:])),
    ("duplicate_init", make_ndjson([make_valid_events()[0], make_valid_events()[0], make_valid_events()[1]])),
    ("missing_result", make_ndjson(make_valid_events()[:1])),
    ("duplicate_result", make_ndjson([make_valid_events()[0], make_valid_events()[1], make_valid_events()[1]])),
    ("wrong_session", make_ndjson([
        make_valid_events(conv_id="conv-1")[0],
        make_valid_events(conv_id="conv-2")[1],
    ])),
    ("wrong_model", make_ndjson(make_valid_events(model="gemini-2.5-pro"))),
    ("wrong_cwd", make_ndjson(make_valid_events(root="/different/root"))),
    ("wrong_permission", make_ndjson(make_valid_events(permission_mode="bypass-review"))),
    ("wrong_status", make_ndjson(make_valid_events(status="ERROR"))),
    ("wrong_error", make_ndjson(make_valid_events(error="rate limit exceeded"))),
    ("wrong_response", make_ndjson(make_valid_events(response="UNEXPECTED_RESPONSE"))),
    ("empty_response", make_ndjson(make_valid_events(response=""))),
    ("tool_evidence_name", make_ndjson(make_valid_events() + [{"step_update": {"tool_name": "bash"}}])),
    ("tool_evidence_info", make_ndjson(make_valid_events() + [{"step_update": {"tool_info": "echo 1"}}])),
    ("subagent_evidence", make_ndjson(make_valid_events() + [{"step_update": {"subagent_info": "subagent_1"}}])),
]


@pytest.mark.parametrize("case_name,stdout_content", INVALID_PREFLIGHT_CASES)
def test_preflight_validation_invalid_cases(tmp_path, case_name, stdout_content):
    case_dir = tmp_path / case_name
    case_dir.mkdir()
    res = run_validation_fragment(case_dir, stdout_content=stdout_content, stderr_content="")
    assert res.returncode != 0, f"Expected {case_name} to fail jq/size validation"


BOUND_CASES = [
    ("stdout_at_bound", 1048576, 0, True),
    ("stdout_exceeds_bound", 1048577, 0, False),
    ("stderr_at_bound", 0, 1048576, True),
    ("stderr_exceeds_bound", 0, 1048577, False),
]


@pytest.mark.parametrize("case_name,stdout_len,stderr_len,expected_pass", BOUND_CASES)
def test_preflight_independent_1mib_bound(tmp_path, case_name, stdout_len, stderr_len, expected_pass):
    case_dir = tmp_path / case_name
    case_dir.mkdir()
    base_stdout_bytes = make_ndjson(make_valid_events(root="/root/test")).encode("utf-8")
    if stdout_len == 0:
        stdout_bytes = base_stdout_bytes
    else:
        assert stdout_len >= len(base_stdout_bytes)
        stdout_bytes = base_stdout_bytes + b" " * (stdout_len - len(base_stdout_bytes))

    stderr_bytes = b"W" * stderr_len

    res = run_validation_fragment(
        case_dir,
        stdout_content=stdout_bytes,
        stderr_content=stderr_bytes,
        root="/root/test",
    )
    if expected_pass:
        assert res.returncode == 0, f"Expected {case_name} to pass size bound: {res.stderr}"
    else:
        assert res.returncode != 0, f"Expected {case_name} to fail size bound"


def test_monitor_script_preflight_channel_separation_and_publication_invariants():
    src = MONITOR.read_text()

    # Channels redirected separately in probe invocation
    assert '>"$probe_stdout_tmp" 2>"$probe_stderr_tmp"' in src

    # Both latest evidence logs published
    assert '"$report_root/provider-preflight.latest.log"' in src
    assert '"$report_root/provider-preflight.latest.stderr.log"' in src

    # mv failure sets recovery_ok=false
    assert 'if ! /usr/bin/mv -f -- "$probe_stdout_tmp" "$report_root/provider-preflight.latest.log"' in src
    assert 'if ! /usr/bin/mv -f -- "$probe_stderr_tmp" "$report_root/provider-preflight.latest.stderr.log"' in src

    # chmod 0600 on published files with failure handling
    assert 'if ! /usr/bin/chmod 0600 "$report_root/provider-preflight.latest.log"' in src
    assert 'if ! /usr/bin/chmod 0600 "$report_root/provider-preflight.latest.stderr.log"' in src

    # Mode 600 and size <= 1048576 verified fail-closed on published evidence
    assert 'stat -c %a -- "$report_root/provider-preflight.latest.log"' in src
    assert 'stat -c %a -- "$report_root/provider-preflight.latest.stderr.log"' in src
    assert '"$report_root/provider-preflight.latest.log" 2>/dev/null || printf \'1048577\')" -gt 1048576' in src
    assert '"$report_root/provider-preflight.latest.stderr.log" 2>/dev/null || printf \'1048577\')" -gt 1048576' in src


def test_preflight_publication_success(tmp_path):
    report_root = tmp_path / "monitor"
    report_root.mkdir()
    stdout_tmp = tmp_path / ".provider-preflight.stdout.123"
    stderr_tmp = tmp_path / ".provider-preflight.stderr.123"
    stdout_tmp.write_text("valid stdout evidence\n")
    stderr_tmp.write_text("valid stderr evidence\n")

    ok, res = run_publication_fragment(report_root, stdout_tmp, stderr_tmp)
    assert ok is True, f"Expected successful publication: {res.stderr}"

    published_stdout = report_root / "provider-preflight.latest.log"
    published_stderr = report_root / "provider-preflight.latest.stderr.log"
    assert published_stdout.is_file() and not published_stdout.is_symlink()
    assert published_stderr.is_file() and not published_stderr.is_symlink()
    assert oct(published_stdout.stat().st_mode)[-3:] == "600"
    assert oct(published_stderr.stat().st_mode)[-3:] == "600"
    assert published_stdout.read_text() == "valid stdout evidence\n"
    assert published_stderr.read_text() == "valid stderr evidence\n"


PUBLICATION_FAIL_CASES = [
    "missing_stdout_tmp",
    "missing_stderr_tmp",
    "stdout_symlink",
    "stderr_symlink",
    "stdout_oversized",
    "stderr_oversized",
]


@pytest.mark.parametrize("fail_mode", PUBLICATION_FAIL_CASES)
def test_preflight_publication_failures(tmp_path, fail_mode):
    case_dir = tmp_path / fail_mode
    case_dir.mkdir()
    report_root = case_dir / "monitor"
    report_root.mkdir()
    stdout_tmp = case_dir / ".provider-preflight.stdout.123"
    stderr_tmp = case_dir / ".provider-preflight.stderr.123"

    if fail_mode == "missing_stdout_tmp":
        stderr_tmp.write_text("stderr\n")
    elif fail_mode == "missing_stderr_tmp":
        stdout_tmp.write_text("stdout\n")
    elif fail_mode == "stdout_symlink":
        target = case_dir / "real_stdout.log"
        target.write_text("stdout\n")
        stdout_tmp.symlink_to(target)
        stderr_tmp.write_text("stderr\n")
    elif fail_mode == "stderr_symlink":
        target = case_dir / "real_stderr.log"
        target.write_text("stderr\n")
        stdout_tmp.write_text("stdout\n")
        stderr_tmp.symlink_to(target)
    elif fail_mode == "stdout_oversized":
        stdout_tmp.write_bytes(b"A" * 1048577)
        stderr_tmp.write_text("stderr\n")
    elif fail_mode == "stderr_oversized":
        stdout_tmp.write_text("stdout\n")
        stderr_tmp.write_bytes(b"B" * 1048577)

    ok, _ = run_publication_fragment(report_root, stdout_tmp, stderr_tmp)
    assert ok is False, f"Expected publication to fail closed for {fail_mode}"


def test_preflight_restart_guard_transitional_contract():
    src = MONITOR.read_text()
    if "two_site_autopilot_monitor_preflight.py" not in src:
        # Current branch: verify the old inline scanner is still present so transition cannot silently skip.
        assert "os.scandir(state_root)" in src
        assert "unresolved_model_intent_scan_failed" in src
        assert "unresolved_model_intent_no_restart" in src
    else:
        # Future branch: current-epoch preflight helpers are wired and obsolete all-history inline scan is gone.
        assert "os.scandir(state_root)" not in src

        # Supervisor helper is invoked under timeout and exact READY\n byte comparison before provider probe/start
        preflight_pos = src.index("two_site_autopilot_monitor_preflight.py")
        preflight_window = src[max(0, preflight_pos - 300):preflight_pos + 600]
        assert "timeout" in preflight_window
        assert "READY" in preflight_window
        assert any(tok in preflight_window for tok in ('"READY"', "'READY'", "'READY\\n'", '"READY\\n"', "$'READY\\n'"))

        probe_or_start_pos = min(
            src.index(needle)
            for needle in ('if [[ "$provider_probe" == true ]]; then', "two_site_autopilot_tmux.sh")
            if needle in src
        )
        assert preflight_pos < probe_or_start_pos

        # STOP check is present including -L
        assert any(
            pat in src
            for pat in (
                '-L "$state_root/STOP"',
                "-L '$state_root/STOP'",
                '-L "${state_root}/STOP"',
            )
        ) or ("-L" in src and "STOP" in src)

        # Broker-only helper is invoked only for a live Supervisor with no Broker
        assert "two_site_autopilot_monitor_broker_preflight.py" in src
        broker_pos = src.index("two_site_autopilot_monitor_broker_preflight.py")
        broker_window = src[max(0, broker_pos - 500):broker_pos + 500]
        assert any(
            cond in broker_window
            for cond in ('provider_broker_missing', '"$broker_count" -eq 0', '$broker_count -eq 0')
        )
