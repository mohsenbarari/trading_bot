from pathlib import Path
import subprocess
import pytest

MONITOR = Path("scripts/two_site_autopilot_monitor.sh")

def test_bash_syntax():
    subprocess.run(["bash", "-n", str(MONITOR)], check=True)

CASES = [
    ("Assignment expired", False, "assignment_expired", "false"),
    ("approval denied", False, "authority_blocked", "false"),
    ("denied escalation", False, "authority_blocked", "false"),
    ("explicit HALT", False, "authority_blocked", "false"),
    ("source/task Antigravity/Gemini approval denied", True, "authority_blocked", "false"),
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
