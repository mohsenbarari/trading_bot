"""Tests for proposal-only automation shell script source scope."""
from __future__ import annotations

import os
from pathlib import Path
import pytest

try:
    from scripts.two_site_proposal_runner import (
        ALLOWED_SHELL_SCRIPTS,
        PROTECTED,
        ProposalError,
        bounded_file_bytes,
        relative_file,
    )
except ImportError:
    from two_site_proposal_runner import (
        ALLOWED_SHELL_SCRIPTS,
        PROTECTED,
        ProposalError,
        bounded_file_bytes,
        relative_file,
    )


def test_allowed_shell_scripts_constant() -> None:
    """Verify ALLOWED_SHELL_SCRIPTS contains exactly the two trusted scripts."""
    assert ALLOWED_SHELL_SCRIPTS == frozenset({
        "scripts/two_site_autopilot_tmux.sh",
        "scripts/two_site_autopilot_monitor.sh",
    })


def test_exact_trusted_shell_paths_accepted(tmp_path: Path) -> None:
    """Both exact trusted shell paths must be accepted by relative_file."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    tmux_path = scripts_dir / "two_site_autopilot_tmux.sh"
    monitor_path = scripts_dir / "two_site_autopilot_monitor.sh"
    tmux_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    monitor_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

    resolved_tmux = relative_file(tmp_path, "scripts/two_site_autopilot_tmux.sh")
    assert resolved_tmux == tmux_path
    assert resolved_tmux.is_file()

    resolved_monitor = relative_file(tmp_path, "scripts/two_site_autopilot_monitor.sh")
    assert resolved_monitor == monitor_path
    assert resolved_monitor.is_file()


def test_exact_trusted_shell_path_pre_creation(tmp_path: Path) -> None:
    """Allowed new scripts before creation must be accepted if root confinement holds."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    target = relative_file(tmp_path, "scripts/two_site_autopilot_monitor.sh")
    assert target == scripts_dir / "two_site_autopilot_monitor.sh"
    assert not target.exists()


@pytest.mark.parametrize("arbitrary_path", [
    "scripts/two_site_autopilot_cron.sh",
    "scripts/autopilot_tmux.sh",
    "scripts/autopilot_monitor.sh",
    "scripts/tmux.sh",
    "scripts/monitor.sh",
    "scripts/deploy.sh",
    "scripts/run.sh",
    "scripts/setup.sh",
    "scripts/other.sh",
    "deploy.sh",
    "monitor.sh",
    "tmux.sh",
    "two_site_autopilot_tmux.sh",
    "two_site_autopilot_monitor.sh",
    "bin/two_site_autopilot_tmux.sh",
    "bin/two_site_autopilot_monitor.sh",
    "tools/two_site_autopilot_tmux.sh",
    "nested/scripts/two_site_autopilot_tmux.sh",
    "nested/scripts/two_site_autopilot_monitor.sh",
    "scripts/nested/two_site_autopilot_tmux.sh",
    "scripts/nested/two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.sh.bak",
    "scripts/two_site_autopilot_tmux.bash",
    "scripts/two_site_autopilot_tmux2.sh",
    "scripts/two_site_autopilot_monitor2.sh",
    "scripts/two_site_autopilot_monitor.bash",
    "scripts/two_site_autopilot_monitor.sh.orig",
    "scripts/two_site_autopilot_monitor_local.sh",
    "scripts/my_two_site_autopilot_tmux.sh",
])
def test_arbitrary_shell_scripts_rejected(tmp_path: Path, arbitrary_path: str) -> None:
    """Arbitrary shell scripts, similar basenames, and wrong folders must be rejected."""
    with pytest.raises(ProposalError, match="file outside source/document scope"):
        relative_file(tmp_path, arbitrary_path)


@pytest.mark.parametrize("dotdot_path", [
    "scripts/../scripts/two_site_autopilot_tmux.sh",
    "scripts/../scripts/two_site_autopilot_monitor.sh",
    "../scripts/two_site_autopilot_tmux.sh",
    "../scripts/two_site_autopilot_monitor.sh",
    "scripts/../../two_site_autopilot_tmux.sh",
    "./scripts/two_site_autopilot_tmux.sh",
    "./scripts/two_site_autopilot_monitor.sh",
    "scripts/./two_site_autopilot_tmux.sh",
    "scripts/./two_site_autopilot_monitor.sh",
    "scripts//two_site_autopilot_tmux.sh",
    "scripts//two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.sh/",
    "scripts/two_site_autopilot_monitor.sh/",
    "scripts\\two_site_autopilot_tmux.sh",
    "scripts\\two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.sh\0",
    "",
])
def test_dotdot_and_noncanonical_variants_rejected(tmp_path: Path, dotdot_path: str) -> None:
    """Noncanonical, traversal, double-slash, and trailing-slash paths must be rejected."""
    with pytest.raises(ProposalError, match="noncanonical file path"):
        relative_file(tmp_path, dotdot_path)


def test_absolute_paths_rejected(tmp_path: Path) -> None:
    """Absolute paths for trusted shell scripts must be rejected."""
    with pytest.raises(ProposalError):
        relative_file(tmp_path, "/scripts/two_site_autopilot_tmux.sh")
    with pytest.raises(ProposalError):
        relative_file(tmp_path, "/scripts/two_site_autopilot_monitor.sh")
    with pytest.raises(ProposalError):
        relative_file(tmp_path, str(tmp_path / "scripts/two_site_autopilot_tmux.sh"))
    with pytest.raises(ProposalError):
        relative_file(tmp_path, str(tmp_path / "scripts/two_site_autopilot_monitor.sh"))


def test_protected_directory_variants_rejected(tmp_path: Path) -> None:
    """Trusted script basenames inside protected directories must be rejected."""
    for protected in PROTECTED:
        with pytest.raises(ProposalError, match="file outside source/document scope"):
            relative_file(tmp_path, f"{protected}/scripts/two_site_autopilot_tmux.sh")
        with pytest.raises(ProposalError, match="file outside source/document scope"):
            relative_file(tmp_path, f"{protected}/scripts/two_site_autopilot_monitor.sh")
        with pytest.raises(ProposalError, match="file outside source/document scope"):
            relative_file(tmp_path, f"scripts/{protected}/two_site_autopilot_tmux.sh")

    with pytest.raises(ProposalError, match="file outside source/document scope"):
        relative_file(tmp_path, ".env/scripts/two_site_autopilot_tmux.sh")
    with pytest.raises(ProposalError, match="file outside source/document scope"):
        relative_file(tmp_path, ".env.production/scripts/two_site_autopilot_monitor.sh")


@pytest.mark.parametrize("case_alias", [
    "scripts/TWO_SITE_AUTOPILOT_TMUX.SH",
    "scripts/Two_Site_Autopilot_Tmux.sh",
    "Scripts/two_site_autopilot_tmux.sh",
    "scripts/Two_Site_Autopilot_Monitor.sh",
    "SCRIPTS/two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.SH",
    "scripts/two_site_autopilot_monitor.SH",
])
def test_case_alias_variants_rejected(tmp_path: Path, case_alias: str) -> None:
    """Case variations of trusted scripts must be rejected."""
    with pytest.raises(ProposalError, match="file outside source/document scope"):
        relative_file(tmp_path, case_alias)


def test_symlink_aliases_rejected(tmp_path: Path) -> None:
    """Symlinked file or directory aliases for trusted scripts must be rejected."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    outside = tmp_path.parent / "outside_script.sh"
    outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    tmux_target = scripts_dir / "two_site_autopilot_tmux.sh"
    tmux_target.symlink_to(outside)
    with pytest.raises(ProposalError, match="symlink or path escape"):
        relative_file(tmp_path, "scripts/two_site_autopilot_tmux.sh")

    real_in_tree = scripts_dir / "real_file.sh"
    real_in_tree.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monitor_target = scripts_dir / "two_site_autopilot_monitor.sh"
    monitor_target.symlink_to(real_in_tree)
    with pytest.raises(ProposalError, match="symlink or path escape"):
        relative_file(tmp_path, "scripts/two_site_autopilot_monitor.sh")


def test_directory_symlink_rejected(tmp_path: Path) -> None:
    """Directory symlinks masquerading as scripts directory must be rejected."""
    real_scripts = tmp_path / "actual_scripts"
    real_scripts.mkdir(parents=True, exist_ok=True)
    real_file = real_scripts / "two_site_autopilot_tmux.sh"
    real_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    symlink_scripts = tmp_path / "scripts"
    symlink_scripts.symlink_to(real_scripts, target_is_directory=True)
    with pytest.raises(ProposalError, match="symlink or path escape"):
        relative_file(tmp_path, "scripts/two_site_autopilot_tmux.sh")


def test_ordinary_py_behavior_unchanged(tmp_path: Path) -> None:
    """Ordinary python and document files remain accepted while invalid ones are rejected."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    runner_py = scripts_dir / "two_site_proposal_runner.py"
    runner_py.write_text("# runner\n", encoding="utf-8")

    resolved_py = relative_file(tmp_path, "scripts/two_site_proposal_runner.py")
    assert resolved_py == runner_py

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    app_py = pkg_dir / "app.py"
    app_py.write_text("# app\n", encoding="utf-8")

    assert relative_file(tmp_path, "pkg/app.py") == app_py

    for allowed_name in [
        "docs/guide.md",
        "config/contract.json",
        "config/task.yaml",
        "config/task.yml",
        "ui/app.ts",
        "ui/Component.vue",
        "ui/style.css",
        "config/unit-test.env.example",
    ]:
        resolved = relative_file(tmp_path, allowed_name)
        assert resolved == tmp_path / allowed_name

    for disallowed in ["app.bin", "data.csv", "archive.tar.gz", "image.png"]:
        with pytest.raises(ProposalError, match="file outside source/document scope"):
            relative_file(tmp_path, disallowed)

    with pytest.raises(ProposalError, match="file outside source/document scope"):
        relative_file(tmp_path, ".git/module.py")
    with pytest.raises(ProposalError, match="noncanonical file path"):
        relative_file(tmp_path, "pkg/../pkg/app.py")
    with pytest.raises(ProposalError):
        relative_file(tmp_path, str(app_py))


def test_hardlink_defense_preserved(tmp_path: Path) -> None:
    """Hardlinks to trusted shell scripts must be rejected by bounded_file_bytes."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / "two_site_autopilot_tmux.sh"
    target.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")

    hardlink = scripts_dir / "hardlinked_tmux.sh"
    os.link(target, hardlink)
    try:
        with pytest.raises(ProposalError, match="hardlinked and cannot be trusted"):
            bounded_file_bytes(tmp_path, target, 160_000, "trusted script")
    finally:
        hardlink.unlink()

    content = bounded_file_bytes(tmp_path, target, 160_000, "trusted script")
    assert content == b"#!/bin/sh\necho safe\n"
