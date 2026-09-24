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


def extract_intent_guard_fragment() -> str:
    src = MONITOR.read_text()
    start = src.index('if [[ "$supervisor_count" -eq 0 && "$restart_eligible" == true ]]; then')
    end = src.index('\n\nif [[ "$restart_eligible" == true ]]; then', start)
    return src[start:end].strip()


def run_intent_guard_fragment(
    state_root: Path,
    supervisor_count: int = 0,
    broker_count: int = 0,
    restart_eligible: bool = True,
    provider_probe: bool = False,
    initial_action: str = "none",
) -> tuple[str, bool, bool]:
    frag = extract_intent_guard_fragment()
    restart_str = "true" if restart_eligible else "false"
    probe_str = "true" if provider_probe else "false"
    script = (
        f'state_root="{state_root}"\n'
        f'supervisor_count={supervisor_count}\n'
        f'broker_count={broker_count}\n'
        f'restart_eligible={restart_str}\n'
        f'provider_probe={probe_str}\n'
        f'action="{initial_action}"\n'
        f'{frag}\n'
        'printf "action=%s restart_eligible=%s provider_probe=%s" "$action" "$restart_eligible" "$provider_probe"\n'
    )
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    parts = dict(item.split("=", 1) for item in res.stdout.split())
    return parts["action"], parts["restart_eligible"] == "true", parts["provider_probe"] == "true"


def test_intent_guard_no_intent_preserves_eligibility(tmp_path):
    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is True
    assert probe is True
    assert action == "none"

    epoch_hex = "a" * 64
    epoch_dir = tmp_path / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()
    (epoch_dir / "notes.txt").write_text("not an intent leaf\n")
    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is True
    assert probe is True
    assert action == "none"


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_intent_guard_suppresses_on_intent_attempts(tmp_path, attempt):
    epoch_hex = "0123456789abcdef" * 4
    epoch_dir = tmp_path / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()
    leaf = epoch_dir / f"rescope_intent_{attempt}"
    leaf.write_text(f"intent payload {attempt}\n")

    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_no_restart"


def test_intent_guard_suppresses_on_symlink_or_nonregular_leaf(tmp_path):
    epoch_hex = "b" * 64
    epoch_dir = tmp_path / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()

    symlink_target = tmp_path / "target_intent"
    symlink_target.write_text("target\n")
    leaf_symlink = epoch_dir / "rescope_intent_1"
    leaf_symlink.symlink_to(symlink_target)

    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_no_restart"

    leaf_symlink.unlink()

    import os
    fifo_leaf = epoch_dir / "rescope_intent_2"
    os.mkfifo(fifo_leaf)
    try:
        action, eligible, probe = run_intent_guard_fragment(
            tmp_path, restart_eligible=True, provider_probe=True
        )
        assert eligible is False
        assert probe is False
        assert action == "unresolved_model_intent_no_restart"
    finally:
        fifo_leaf.unlink()


FAIL_CLOSED_CASES = [
    ("symlink_epoch_dir", "unresolved_model_intent_scan_failed"),
    ("malformed_epoch_name_short", "unresolved_model_intent_scan_failed"),
    ("malformed_epoch_name_uppercase", "unresolved_model_intent_scan_failed"),
    ("unreadable_epoch_dir", "unresolved_model_intent_scan_failed"),
    ("non_directory_epoch_entry", "unresolved_model_intent_scan_failed"),
    ("epoch_nested_subdirectory", "unresolved_model_intent_scan_failed"),
    ("epoch_non_intent_symlink", "unresolved_model_intent_scan_failed"),
    ("epoch_malformed_intent_name", "unresolved_model_intent_scan_failed"),
    ("symlink_state_root", "unresolved_model_intent_scan_failed"),
    ("direct_intent_in_state_root", "unresolved_model_intent_no_restart"),
]


@pytest.mark.parametrize("case_name,expected_action", FAIL_CLOSED_CASES)
def test_intent_guard_fail_closed_cases(tmp_path, case_name, expected_action):
    case_dir = tmp_path / case_name
    case_dir.mkdir()
    state_root = case_dir / "state"

    if case_name == "symlink_epoch_dir":
        state_root.mkdir()
        real_epoch = case_dir / "real_epoch"
        real_epoch.mkdir()
        (state_root / ("rescope-" + "c" * 64)).symlink_to(real_epoch)
    elif case_name == "malformed_epoch_name_short":
        state_root.mkdir()
        (state_root / "rescope-short").mkdir()
    elif case_name == "malformed_epoch_name_uppercase":
        state_root.mkdir()
        (state_root / ("rescope-" + "C" * 64)).mkdir()
    elif case_name == "unreadable_epoch_dir":
        state_root.mkdir()
        epoch = state_root / ("rescope-" + "d" * 64)
        epoch.mkdir()
        epoch.chmod(0o000)
    elif case_name == "non_directory_epoch_entry":
        state_root.mkdir()
        (state_root / ("rescope-" + "e" * 64)).write_text("regular file\n")
    elif case_name == "epoch_nested_subdirectory":
        state_root.mkdir()
        epoch = state_root / ("rescope-" + "f" * 64)
        epoch.mkdir()
        (epoch / "nested_dir").mkdir()
    elif case_name == "epoch_non_intent_symlink":
        state_root.mkdir()
        epoch = state_root / ("rescope-" + "1" * 64)
        epoch.mkdir()
        tgt = case_dir / "tgt"
        tgt.write_text("x")
        (epoch / "symlink_leaf").symlink_to(tgt)
    elif case_name == "epoch_malformed_intent_name":
        state_root.mkdir()
        epoch = state_root / ("rescope-" + "2" * 64)
        epoch.mkdir()
        (epoch / "rescope_intent_invalid").write_text("x")
    elif case_name == "symlink_state_root":
        real_state = case_dir / "real_state"
        real_state.mkdir()
        state_root.symlink_to(real_state)
    elif case_name == "direct_intent_in_state_root":
        state_root.mkdir()
        (state_root / "rescope_intent_1").write_text("x")

    try:
        action, eligible, probe = run_intent_guard_fragment(
            state_root, restart_eligible=True, provider_probe=True
        )
        assert eligible is False
        assert probe is False
        assert action == expected_action
    finally:
        if case_name == "unreadable_epoch_dir":
            epoch.chmod(0o700)


def test_intent_guard_broker_only_recovery_remains_eligible(tmp_path):
    epoch_hex = "3" * 64
    epoch_dir = tmp_path / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()
    (epoch_dir / "rescope_intent_1").write_text("intent\n")

    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, supervisor_count=1, restart_eligible=True, provider_probe=False
    )
    assert eligible is True
    assert probe is False
    assert action == "none"


def test_intent_guard_stop_still_wins(tmp_path):
    (tmp_path / "STOP").write_text("STOPPED\n")
    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False

    epoch_hex = "4" * 64
    epoch_dir = tmp_path / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()
    (epoch_dir / "rescope_intent_2").write_text("intent\n")

    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False


def test_intent_guard_newline_in_filename_suppresses(tmp_path):
    (tmp_path / "unsafe\nfile").write_text("x\n")
    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_scan_failed"

    epoch_hex = "5" * 64
    epoch_dir = tmp_path / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()
    (epoch_dir / "leaf\nname").write_text("x\n")
    action, eligible, probe = run_intent_guard_fragment(
        tmp_path, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_scan_failed"


def test_intent_guard_entry_limits_exceeded_suppresses(tmp_path):
    state_root = tmp_path / "state_root_large"
    state_root.mkdir()
    for i in range(513):
        (state_root / f"file_{i}").write_text("")
    action, eligible, probe = run_intent_guard_fragment(
        state_root, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_scan_failed"

    state_root_epoch = tmp_path / "state_root_epoch"
    state_root_epoch.mkdir()
    epoch_dir = state_root_epoch / ("rescope-" + "7" * 64)
    epoch_dir.mkdir()
    for i in range(17):
        (epoch_dir / f"note_{i}.txt").write_text("")
    action, eligible, probe = run_intent_guard_fragment(
        state_root_epoch, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_scan_failed"


def test_intent_guard_scanner_failure_suppresses(tmp_path):
    unreadable_root = tmp_path / "unreadable_state"
    unreadable_root.mkdir()
    unreadable_root.chmod(0o000)
    try:
        action, eligible, probe = run_intent_guard_fragment(
            unreadable_root, restart_eligible=True, provider_probe=True
        )
        assert eligible is False
        assert probe is False
        assert action == "unresolved_model_intent_scan_failed"
    finally:
        unreadable_root.chmod(0o700)

    nonexistent_root = tmp_path / "nonexistent_dir"
    action, eligible, probe = run_intent_guard_fragment(
        nonexistent_root, restart_eligible=True, provider_probe=True
    )
    assert eligible is False
    assert probe is False
    assert action == "unresolved_model_intent_scan_failed"


def test_intent_guard_rejects_unreadable_or_unsearchable_mode(tmp_path):
    state_root = tmp_path / "mode_test_state"
    state_root.mkdir()
    epoch_hex = "e" * 64
    epoch_dir = state_root / f"rescope-{epoch_hex}"
    epoch_dir.mkdir()

    for bad_mode in (0o000, 0o400, 0o100, 0o600):
        epoch_dir.chmod(bad_mode)
        try:
            action, eligible, probe = run_intent_guard_fragment(
                state_root, restart_eligible=True, provider_probe=True
            )
            assert eligible is False
            assert probe is False
            assert action == "unresolved_model_intent_scan_failed"
        finally:
            epoch_dir.chmod(0o700)

    for bad_mode in (0o000, 0o400, 0o100, 0o600):
        state_root.chmod(bad_mode)
        try:
            action, eligible, probe = run_intent_guard_fragment(
                state_root, restart_eligible=True, provider_probe=True
            )
            assert eligible is False
            assert probe is False
            assert action == "unresolved_model_intent_scan_failed"
        finally:
            state_root.chmod(0o700)
