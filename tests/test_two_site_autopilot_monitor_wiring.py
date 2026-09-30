"""Hermetic behavioral regression tests for autopilot monitor shell wiring."""

import os
import stat
import subprocess
from pathlib import Path
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "two_site_autopilot_monitor.sh"
SCRIPT_TEXT = SCRIPT_PATH.read_text(encoding="utf-8")


def _extract_block(start_marker: str, end_marker: str) -> str:
    start = SCRIPT_TEXT.index(start_marker)
    end = SCRIPT_TEXT.index(end_marker, start)
    return SCRIPT_TEXT[start:end]


SUPERVISOR_BLOCK = _extract_block(
    'if [[ "$supervisor_count" -eq 0 && "$restart_eligible" == true ]]; then\n',
    '\nif [[ "$supervisor_count" -gt 0 && "$broker_count" -eq 0 && "$restart_eligible" == true ]]; then',
)

BROKER_BLOCK = _extract_block(
    'if [[ "$supervisor_count" -gt 0 && "$broker_count" -eq 0 && "$restart_eligible" == true ]]; then\n',
    '\nif [[ "$restart_eligible" == true ]]; then',
)


@pytest.fixture
def env_roots(tmp_path):
    integration_root = tmp_path / "integration"
    state_root = integration_root / ".local" / "two-site-refactor" / "autopilot"
    report_root = integration_root / ".local" / "two-site-refactor" / "monitor"
    scripts_dir = integration_root / "scripts"
    for d in (state_root, report_root, scripts_dir):
        d.mkdir(parents=True, exist_ok=True)
    return integration_root, state_root, report_root, scripts_dir


def write_fake_helper(script_path: Path, output_bytes: bytes, exit_code: int = 0):
    code = (
        "import sys\n"
        f"sys.stdout.buffer.write({repr(output_bytes)})\n"
        f"sys.exit({exit_code})\n"
    )
    script_path.write_text(code, encoding="utf-8")


def run_shell_block(
    block: str,
    integration_root: Path,
    state_root: Path,
    report_root: Path,
    supervisor_count: int = 0,
    broker_count: int = 0,
    restart_eligible: str = "true",
    provider_probe: str = "true",
    protected_refs_ok: str = "false",
    action: str = "none",
) -> dict:
    runner_script = f"""set -u -o pipefail
integration_root={repr(str(integration_root))}
state_root={repr(str(state_root))}
report_root={repr(str(report_root))}
supervisor_count={supervisor_count}
broker_count={broker_count}
restart_eligible={restart_eligible}
provider_probe={provider_probe}
protected_refs_ok={protected_refs_ok}
action={repr(action)}

{block}

printf 'protected_refs_ok=%s\\nrestart_eligible=%s\\nprovider_probe=%s\\naction=%s\\n' \\
  "$protected_refs_ok" "$restart_eligible" "$provider_probe" "$action"
"""
    proc = subprocess.run(
        ["bash", "-c", runner_script],
        capture_output=True,
        text=True,
        check=True,
    )
    result = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k] = v
    return result


def test_absent_supervisor_preflight_positive_exact_ready(env_roots):
    integration_root, state_root, report_root, scripts_dir = env_roots
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_preflight.py",
        b"READY\n",
        exit_code=0,
    )
    res = run_shell_block(
        SUPERVISOR_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res == {
        "protected_refs_ok": "true",
        "restart_eligible": "true",
        "provider_probe": "true",
        "action": "none",
    }
    stdout_target = report_root / "preflight.latest.log"
    stderr_target = report_root / "preflight.latest.stderr.log"
    assert stdout_target.is_file() and not stdout_target.is_symlink()
    assert stderr_target.is_file() and not stderr_target.is_symlink()
    assert stdout_target.read_bytes() == b"READY\n"
    assert stat.S_IMODE(stdout_target.stat().st_mode) == 0o600
    assert stat.S_IMODE(stderr_target.stat().st_mode) == 0o600
    assert stdout_target.stat().st_size <= 65536
    assert stderr_target.stat().st_size <= 65536


def test_absent_supervisor_preflight_historical_intent_not_veto(env_roots):
    integration_root, state_root, report_root, scripts_dir = env_roots
    rescope_dir = state_root / f"rescope-{'a' * 64}"
    rescope_dir.mkdir(parents=True, exist_ok=True)
    rescope_dir.chmod(0o700)
    intent_file = rescope_dir / "rescope_intent_1"
    dummy_payload = b"dummy intent bytes\n"
    intent_file.write_bytes(dummy_payload)
    intent_file.chmod(0o600)
    old_log = report_root / "preflight.latest.log"
    old_stderr = report_root / "preflight.latest.stderr.log"
    old_log.write_bytes(b"HISTORICAL_FAILURE\n")
    old_stderr.write_bytes(b"OLD_ERROR\n")
    old_log.chmod(0o600)
    old_stderr.chmod(0o600)
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_preflight.py",
        b"READY\n",
        exit_code=0,
    )
    res = run_shell_block(
        SUPERVISOR_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res["protected_refs_ok"] == "true"
    assert res["restart_eligible"] == "true"
    assert res["provider_probe"] == "true"
    assert intent_file.is_file() and not intent_file.is_symlink()
    assert intent_file.read_bytes() == dummy_payload
    assert old_log.read_bytes() == b"READY\n"


@pytest.mark.parametrize(
    ("payload", "exit_code"),
    [
        (b"READY\nEXTRA", 0),
        (b"READY\nEXTRA\n", 0),
        (b"READY", 0),
        (b"READY\r\n", 0),
        (b"READY\n\x00", 0),
        (b"READY\x00\n", 0),
        (b"READY\n", 1),
    ],
)
def test_absent_supervisor_preflight_negative_payloads(env_roots, payload, exit_code):
    integration_root, state_root, report_root, scripts_dir = env_roots
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_preflight.py",
        payload,
        exit_code=exit_code,
    )
    res = run_shell_block(
        SUPERVISOR_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res["protected_refs_ok"] == "false"
    assert res["restart_eligible"] == "false"
    assert res["provider_probe"] == "false"
    assert res["action"] == "preflight_failed"


@pytest.mark.parametrize("stop_target", ["state", "integration"])
@pytest.mark.parametrize("is_dangling", [False, True])
def test_absent_supervisor_preflight_stop_blocking(env_roots, stop_target, is_dangling):
    integration_root, state_root, report_root, scripts_dir = env_roots
    stop_path = (state_root if stop_target == "state" else integration_root) / "STOP"
    if is_dangling:
        stop_path.symlink_to(integration_root / "nonexistent_target")
    else:
        stop_path.write_text("STOPPED\n", encoding="utf-8")
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_preflight.py",
        b"READY\n",
        exit_code=0,
    )
    res = run_shell_block(
        SUPERVISOR_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res["protected_refs_ok"] == "false"
    assert res["restart_eligible"] == "false"
    assert res["provider_probe"] == "false"
    assert res["action"] == "stop_present"
    assert os.path.lexists(stop_path)


def test_live_broker_preflight_positive(env_roots):
    integration_root, state_root, report_root, scripts_dir = env_roots
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_broker_preflight.py",
        b"READY\n",
        exit_code=0,
    )
    res = run_shell_block(
        BROKER_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=1,
        broker_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res == {
        "protected_refs_ok": "true",
        "restart_eligible": "true",
        "provider_probe": "true",
        "action": "none",
    }
    stdout_target = report_root / "broker-preflight.latest.log"
    stderr_target = report_root / "broker-preflight.latest.stderr.log"
    assert stdout_target.is_file() and not stdout_target.is_symlink()
    assert stderr_target.is_file() and not stderr_target.is_symlink()
    assert stdout_target.read_bytes() == b"READY\n"
    assert stat.S_IMODE(stdout_target.stat().st_mode) == 0o600
    assert stat.S_IMODE(stderr_target.stat().st_mode) == 0o600
    assert stdout_target.stat().st_size <= 65536
    assert stderr_target.stat().st_size <= 65536


@pytest.mark.parametrize(
    "payload",
    [b"READY\nEXTRA", b"READY", b"READY\r\n", b"READY\n\x00", b"FAIL\n"],
)
def test_live_broker_preflight_negative_bad_bytes(env_roots, payload):
    integration_root, state_root, report_root, scripts_dir = env_roots
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_broker_preflight.py",
        payload,
        exit_code=0,
    )
    res = run_shell_block(
        BROKER_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=1,
        broker_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res["protected_refs_ok"] == "false"
    assert res["restart_eligible"] == "false"
    assert res["provider_probe"] == "false"
    assert res["action"] == "preflight_failed"


@pytest.mark.parametrize("stop_target", ["state", "integration"])
@pytest.mark.parametrize("is_dangling", [False, True])
def test_live_broker_preflight_stop_negative(env_roots, stop_target, is_dangling):
    integration_root, state_root, report_root, scripts_dir = env_roots
    stop_path = (state_root if stop_target == "state" else integration_root) / "STOP"
    if is_dangling:
        stop_path.symlink_to(integration_root / "dangling_pointer")
    else:
        stop_path.write_text("STOPPED\n", encoding="utf-8")
    write_fake_helper(
        scripts_dir / "two_site_autopilot_monitor_broker_preflight.py",
        b"READY\n",
        exit_code=0,
    )
    res = run_shell_block(
        BROKER_BLOCK,
        integration_root,
        state_root,
        report_root,
        supervisor_count=1,
        broker_count=0,
        restart_eligible="true",
        provider_probe="true",
        protected_refs_ok="false",
        action="none",
    )
    assert res["protected_refs_ok"] == "false"
    assert res["restart_eligible"] == "false"
    assert res["provider_probe"] == "false"
    assert res["action"] == "stop_present"
    assert os.path.lexists(stop_path)


def test_broker_restart_wiring_static():
    assert 'broker_session="two-site-provider-broker"' in SCRIPT_TEXT
    start = SCRIPT_TEXT.index('if [[ "$recovery_reason" == "provider_broker_missing" ]]; then')
    end = SCRIPT_TEXT.index('\n      else\n        if /usr/bin/timeout 45', start)
    broker_branch = SCRIPT_TEXT[start:end]

    assert 'tmux new-session -d -s "$broker_session" -c "$integration_root" "$broker_command"' in broker_branch
    assert "two_site_autopilot_tmux.sh" not in broker_branch
    assert "new-window" not in broker_branch
    assert "respawn-window" not in broker_branch

    cmd_start = broker_branch.index("printf -v broker_command")
    cmd_end = broker_branch.index("if /usr/bin/timeout 15 /usr/bin/tmux new-session", cmd_start)
    broker_cmd_def = broker_branch[cmd_start:cmd_end]
    assert "two_site_provider_broker.py" in broker_cmd_def
    assert "two_site_codex_autopilot.py" not in broker_cmd_def

    status_poll = 'two_site_provider_broker.py" --root "$integration_root" status'
    action_started = 'action="restart_started"'
    assert status_poll in broker_branch
    assert action_started in broker_branch
    assert broker_branch.index(status_poll) < broker_branch.index(action_started)
