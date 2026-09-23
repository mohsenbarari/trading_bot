"""Tests for two-site cycle packet and autopilot control paths, monitor syntax, and pgrep filters."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    GEMINI_RUNTIME_CONTRACT_REL,
    PROMPT_REL,
    SCHEMA_REL,
)

ROOT = Path(__file__).resolve().parents[1]
MONITOR_PATH = ROOT / "scripts/two_site_autopilot_monitor.sh"


def test_control_paths_added_and_retained_without_duplicates() -> None:
    expected_added = {
        Path("scripts/two_site_cycle_packet.py"),
        Path("scripts/two_site_autopilot_tmux.sh"),
        Path("scripts/two_site_autopilot_monitor.sh"),
        Path("scripts/two_site_provider_broker.py"),
        Path("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md"),
    }
    expected_retained = {
        Path("scripts/two_site_codex_autopilot.py"),
        Path("scripts/two_site_cycle_tools.py"),
        Path("scripts/two_site_cycle_metrics.py"),
        Path("scripts/two_site_proposal_runner.py"),
        Path("scripts/two_site_assignment_scope.py"),
        Path("scripts/two_site_preparation_contract.py"),
        Path("scripts/two_site_preparation_io.py"),
        Path("scripts/two_site_preparation_boundary.py"),
        Path("scripts/two_site_preparation_runtime_gate.py"),
        GEMINI_RUNTIME_CONTRACT_REL,
        PROMPT_REL,
        SCHEMA_REL,
        Path(".agents/skills/two-site-refactor/SKILL.md"),
        Path(".agents/rules/two-site-refactor-safety.md"),
    }

    # No duplicates
    assert len(CONTROL_PATHS) == len(set(CONTROL_PATHS)), "CONTROL_PATHS must contain no duplicates"

    # Exact expected set
    assert set(CONTROL_PATHS) == (expected_added | expected_retained)

    for item in expected_added:
        assert item in CONTROL_PATHS, f"Missing added path: {item}"

    for item in expected_retained:
        assert item in CONTROL_PATHS, f"Missing retained path: {item}"


def test_monitor_bash_syntax_check() -> None:
    bash = shutil.which("bash") or "/bin/bash"
    result = subprocess.run(
        [bash, "-n", str(MONITOR_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_monitor_cd_occurs_before_first_filesystem_side_effect() -> None:
    content = MONITOR_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()

    cd_line_idx = None
    first_side_effect_idx = None
    side_effect_desc = None

    side_effect_patterns = [
        (re.compile(r"^\s*mkdir\b"), "mkdir"),
        (re.compile(r"^\s*exec\s+\d+>"), "lock descriptor redirection"),
        (re.compile(r"^\s*(?:/usr/bin/)?touch\b"), "touch"),
        (re.compile(r"^\s*(?:/usr/bin/)?rm(?:dir)?\b"), "rm"),
        (re.compile(r"^\s*(?:/usr/bin/)?mv\b"), "mv"),
        (re.compile(r"^\s*(?:/usr/bin/)?cp\b"), "cp"),
        (re.compile(r"^\s*(?:/usr/bin/)?chmod\b"), "chmod"),
    ]

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if re.match(r'^cd\s+"\$integration_root"\s*\|\|\s*exit', stripped):
            if cd_line_idx is None:
                cd_line_idx = idx

        for pattern, desc in side_effect_patterns:
            if pattern.search(stripped):
                if first_side_effect_idx is None:
                    first_side_effect_idx = idx
                    side_effect_desc = desc
                break

    assert cd_line_idx is not None, "Explicit cd to $integration_root with exit was not found"
    assert first_side_effect_idx is not None, "No filesystem side effect identified"
    assert cd_line_idx < first_side_effect_idx, (
        f"cd at line {cd_line_idx + 1} must occur before first side effect "
        f"({side_effect_desc}) at line {first_side_effect_idx + 1}"
    )


def test_monitor_codex_pgrep_regex_filtering() -> None:
    content = MONITOR_PATH.read_text(encoding="utf-8")

    root_match = re.search(r'^integration_root="([^"]+)"', content, re.MULTILINE)
    assert root_match is not None, "integration_root definition not found in monitor script"
    integration_root = root_match.group(1)

    pgrep_match = re.search(
        r'pgrep\s+-f\s+"([^"]*codex[^"]*)"',
        content,
    )
    assert pgrep_match is not None, "Codex coordinator pgrep pattern not found in monitor script"
    raw_pattern = pgrep_match.group(1)

    # Expand $integration_root as the shell does
    pattern = raw_pattern.replace("$integration_root", integration_root)

    def matches(cmdline: str) -> bool:
        res = subprocess.run(
            ["grep", "-E", pattern],
            input=cmdline,
            capture_output=True,
            text=True,
        )
        return res.returncode == 0

    # 1. Pinned executable matching integration_root
    pinned_cmd = (
        f"/root/trading-bot/trading_bot/.local/two-site-refactor/integration/.local/tools/"
        f"codex-0.153.4-56ef98ab4032d317.pin exec --model gpt-6-astra "
        f'--config model_reasoning_effort="xhigh" --cd {integration_root} --approve-for-me --ephemeral'
    )
    assert matches(pinned_cmd), "Must match pinned codex exec with matching integration_root"

    # 2. Legacy executable matching integration_root
    legacy_cmd = (
        f"/usr/local/bin/codex exec --model gpt-6-astra "
        f'--config model_reasoning_effort="xhigh" --cd {integration_root} --approve-for-me --ephemeral'
    )
    assert matches(legacy_cmd), "Must match legacy codex exec with matching integration_root"

    # 3. Different root
    diff_root_pinned = (
        f"/root/trading-bot/trading_bot/.local/two-site-refactor/integration/.local/tools/"
        f"codex-0.153.4-56ef98ab4032d317.pin exec --model gpt-6-astra --cd /other/path/to/repo"
    )
    assert not matches(diff_root_pinned), "Must not match pinned codex with different root"

    diff_root_legacy = (
        f"/usr/local/bin/codex exec --model gpt-6-astra --cd /other/path/to/repo"
    )
    assert not matches(diff_root_legacy), "Must not match legacy codex with different root"

    diff_root_standby = (
        f"/usr/local/bin/codex exec --model gpt-6-astra --cd {integration_root}-standby"
    )
    assert not matches(diff_root_standby), "Must not match prefix-colliding different root"

    # 4. Unrelated monitor command strings
    monitor_cmd_full = f"/bin/bash {integration_root}/scripts/two_site_autopilot_monitor.sh"
    assert not matches(monitor_cmd_full), "Must not match full monitor command invocation"

    monitor_cmd_rel = "bash scripts/two_site_autopilot_monitor.sh"
    assert not matches(monitor_cmd_rel), "Must not match relative monitor command invocation"

    monitor_name = "two-site-autopilot-monitor"
    assert not matches(monitor_name), "Must not match monitor process name"

    supervisor_cmd = f"/usr/bin/python3 {integration_root}/scripts/two_site_codex_autopilot.py --root {integration_root}"
    assert not matches(supervisor_cmd), "Must not match supervisor command"
