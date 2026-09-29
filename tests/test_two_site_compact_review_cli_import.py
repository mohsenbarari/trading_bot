"""Hermetic CLI import and help tests for two_site_proposal_runner."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()


def test_direct_script_cli_help():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "two_site_proposal_runner.py"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    res = subprocess.run(
        [sys.executable, "-B", str(script_path), "--help"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    stdout = res.stdout.lower()
    assert "usage" in stdout
    assert "worker" in stdout
    assert "verifier" in stdout
    assert "check" in stdout


def test_package_cli_help():
    repo_root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    res = subprocess.run(
        [sys.executable, "-B", "-m", "scripts.two_site_proposal_runner", "--help"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    stdout = res.stdout.lower()
    assert "usage" in stdout
    assert "worker" in stdout
    assert "verifier" in stdout
    assert "check" in stdout


def test_isolated_explicit_syspath_import():
    repo_root = Path(__file__).resolve().parent.parent
    code = (
        "import os\n"
        "import sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "os.environ['APP_ENV_FILE'] = 'config/unit-test.env.example'\n"
        "from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence\n"
        "install_p1_00_network_fence()\n"
        "import scripts.two_site_proposal_runner as runner\n"
        "assert hasattr(runner, 'ProposalError')\n"
    )
    res = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
