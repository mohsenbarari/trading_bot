"""Real-worktree baseline for canonical evidence inspection."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from scripts.two_site_cycle_tools import inspect_canonical_evidence


def test_real_worktree_baseline() -> None:
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, text=True, capture_output=True)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("Git metadata unavailable in the hermetic snapshot")
    behavior = "BOT-TG-CALLBACK-QUERY-PANEL-BACK-TO-USER-PANEL-FROM-BLOCKED-L640"
    result = inspect_canonical_evidence(root, behavior)
    assert result["status"] == "VALID", result
    assert result["expected_seed_count"] == result["seed_count"] == 465
    assert result["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"
