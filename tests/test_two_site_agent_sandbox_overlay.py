"""Tests for tracked scripts.two_site_agent_sandbox.run_tests overlays boundary."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.two_site_agent_sandbox import (
    SandboxError,
    _run_bounded_process,
    run_tests,
)

EXISTING_PATH = "tests/test_existing.py"
NEW_PATH = "tests/test_new.py"
ENV_PATH = "config/unit-test.env.example"
GATE_PATH = "scripts/two_site_pre_review_pytest_gate.py"

ORIGINAL_EXISTING_CONTENT = "def test_original():\n    assert True\n"
ENV_CONTENT = "APP_ENV=test\n"
GATE_SOURCE = Path(__file__).resolve().parent.parent / GATE_PATH
if not GATE_SOURCE.is_file():
    GATE_SOURCE = Path(GATE_PATH)
REAL_GATE_BYTES = GATE_SOURCE.read_bytes()


def create_test_repo(path: Path) -> Path:
    """Build a tiny temporary Git repository with config/unit-test.env.example and one tracked test."""
    repo = path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "-q"],
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    (repo / "config").mkdir(parents=True)
    (repo / ENV_PATH).write_text(ENV_CONTENT, encoding="utf-8")
    (repo / "tests").mkdir(parents=True)
    (repo / EXISTING_PATH).write_text(ORIGINAL_EXISTING_CONTENT, encoding="utf-8")
    gate_dest = repo / GATE_PATH
    gate_dest.parent.mkdir(parents=True, exist_ok=True)
    gate_dest.write_bytes(REAL_GATE_BYTES)
    copied_bytes = gate_dest.read_bytes()
    assert copied_bytes == REAL_GATE_BYTES
    assert len(copied_bytes) == 824
    assert hashlib.sha256(copied_bytes).hexdigest() == "de6177a5e7923d01586c88fa98e502efac4f3fea792ae5a48cb46326c14301ce"
    subprocess.run(
        ["git", "-C", str(repo), "add", "--all"],
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Sandbox Test",
            "-c",
            "user.email=sandbox@test.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return create_test_repo(tmp_path)


class ProcessRecorder:
    """Record bounded process calls and inspect disposable snapshot before cleanup."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.snapshot_inspections: list[dict[str, object]] = []

    def __call__(self, argv: list[str], *args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        if argv and argv[0] == "/usr/bin/prlimit":
            work_idx = argv.index("/work")
            snapshot_dir = Path(argv[work_idx - 1])
            files: dict[str, bytes] = {}
            for item in snapshot_dir.rglob("*"):
                if item.is_file():
                    files[str(item.relative_to(snapshot_dir))] = item.read_bytes()
            self.snapshot_inspections.append({
                "snapshot_dir": snapshot_dir,
                "files": files,
            })
            return subprocess.CompletedProcess(list(argv), 0, b"2 passed\n", b"")
        return _run_bounded_process(argv, *args, **kwargs)


def test_overlay_replacement_and_new_file_isolated_in_snapshot(repo: Path) -> None:
    """Prove overlay replacement and new-file exist only in disposable snapshot and worktree is untouched."""
    overlay_existing = "def test_replaced():\n    assert 1 == 1\n"
    overlay_new = "def test_new():\n    assert 2 == 2\n"
    overlays = {
        EXISTING_PATH: overlay_existing,
        NEW_PATH: overlay_new,
    }
    selected_tests = [EXISTING_PATH, NEW_PATH]

    recorder = ProcessRecorder()
    with patch("scripts.two_site_agent_sandbox._run_bounded_process", side_effect=recorder):
        result = run_tests(repo, selected_tests, overlays=overlays)

    assert len(recorder.snapshot_inspections) == 1
    inspection = recorder.snapshot_inspections[0]
    snapshot_dir = inspection["snapshot_dir"]
    assert isinstance(snapshot_dir, Path)
    snapshot_files = inspection["files"]
    assert isinstance(snapshot_files, dict)

    assert snapshot_files.get(EXISTING_PATH) == overlay_existing.encode("utf-8")
    assert snapshot_files.get(NEW_PATH) == overlay_new.encode("utf-8")
    assert snapshot_files.get(ENV_PATH) == ENV_CONTENT.encode("utf-8")
    assert snapshot_files.get(GATE_PATH) == REAL_GATE_BYTES

    for selector in selected_tests:
        assert selector in snapshot_files

    assert not snapshot_dir.exists()

    expected_files_count = 4
    expected_bytes_total = (
        len(ENV_CONTENT.encode("utf-8"))
        + len(overlay_existing.encode("utf-8"))
        + len(overlay_new.encode("utf-8"))
        + len(REAL_GATE_BYTES)
    )
    assert result["snapshot_files"] == expected_files_count
    assert result["snapshot_bytes"] == expected_bytes_total
    assert result["returncode"] == 0
    assert result["isolation"] == "bwrap:ro-source,empty-home,clearenv,unshare-all"

    assert (repo / EXISTING_PATH).read_text(encoding="utf-8") == ORIGINAL_EXISTING_CONTENT
    assert not (repo / NEW_PATH).exists()
    assert (repo / ENV_PATH).read_text(encoding="utf-8") == ENV_CONTENT
    assert (repo / GATE_PATH).read_bytes() == REAL_GATE_BYTES
    git_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    assert git_status.stdout.strip() == ""

    final_argv = recorder.calls[-1]
    assert final_argv[0] == "/usr/bin/prlimit"

    work_idx = final_argv.index("/work")
    assert final_argv[work_idx - 2:work_idx] == ["--ro-bind", str(snapshot_dir)]

    root_idx = final_argv.index("/root")
    assert final_argv[root_idx - 1] == "--dir"

    assert "--clearenv" in final_argv
    assert "--unshare-all" in final_argv
    assert "--unshare-user" in final_argv
    assert "--disable-userns" in final_argv
    assert "--die-with-parent" in final_argv
    assert "--new-session" in final_argv
    assert "--share-net" not in final_argv
    gate_flag_idx = final_argv.index("scripts.two_site_pre_review_pytest_gate")
    assert final_argv[gate_flag_idx - 1:gate_flag_idx + 1] == ["-p", "scripts.two_site_pre_review_pytest_gate"]
    assert final_argv[-2:] == selected_tests


@pytest.mark.parametrize(
    ("overlays", "extra_paths", "snapshot_limit", "expected_match"),
    [
        (["tests/test_existing.py"], None, 300_000_000, "overlays must be a mapping"),
        ("not_a_mapping", None, 300_000_000, "overlays must be a mapping"),
        (12345, None, 300_000_000, "overlays must be a mapping"),
        (
            {f"tests/test_{i}.py": "def test(): pass" for i in range(33)},
            None,
            300_000_000,
            "overlays mapping exceeds 32 entries",
        ),
        ({"../test_escape.py": "pass"}, None, 300_000_000, "escapes the assigned worktree"),
        ({"tests//test_existing.py": "pass"}, None, 300_000_000, "source path escapes the assigned worktree"),
        ({".git/config": "pass"}, None, 300_000_000, "overlay path in excluded root"),
        ({".venv/test.py": "pass"}, None, 300_000_000, "overlay path in excluded root"),
        ({"secrets/token.py": "pass"}, None, 300_000_000, "overlay path exposes credential or private location"),
        ({".env": "TOKEN=1"}, None, 300_000_000, "overlay path exposes environment file"),
        ({"data/items.json": "{}"}, None, 300_000_000, "overlay path in restricted data/archive location"),
        ({"tests/test.exe": "pass"}, None, 300_000_000, "overlay path has unsupported suffix"),
        ({"tests/test_overlap.py": "pass"}, ["tests/test_overlap.py"], 300_000_000, "overlay path overlaps extra_paths"),
        ({EXISTING_PATH: 12345}, None, 300_000_000, "overlay content must be a string"),
        ({EXISTING_PATH: b"bytes"}, None, 300_000_000, "overlay content must be a string"),
        ({EXISTING_PATH: None}, None, 300_000_000, "overlay content must be a string"),
        ({EXISTING_PATH: ""}, None, 300_000_000, "overlay content must be non-empty"),
        ({EXISTING_PATH: "def test():\x00pass"}, None, 300_000_000, "overlay content must not contain NUL"),
        ({EXISTING_PATH: "def test(): pass \ud800"}, None, 300_000_000, "overlay content must be valid UTF-8"),
        ({NEW_PATH: "x" * 200}, None, 100, "source snapshot exceeds reviewed capacity"),
    ],
)
def test_invalid_overlays_fail_before_execution(
    repo: Path,
    overlays: object,
    extra_paths: list[str] | None,
    snapshot_limit: int,
    expected_match: str,
) -> None:
    """Prove malformed, excessive, unsafe, overlapping, invalid, and oversized overlays fail before execution."""
    recorder = ProcessRecorder()
    with patch("scripts.two_site_agent_sandbox._run_bounded_process", side_effect=recorder):
        with pytest.raises(SandboxError, match=expected_match):
            run_tests(
                repo,
                [EXISTING_PATH],
                extra_paths=extra_paths,
                snapshot_limit=snapshot_limit,
                overlays=overlays,
            )

        for call_argv in recorder.calls:
            assert call_argv[0] != "/usr/bin/prlimit", f"Final process executed unexpectedly: {call_argv}"
        assert len(recorder.snapshot_inspections) == 0


@pytest.mark.parametrize("overlays", [None, {}])
def test_omitted_and_empty_overlays_preserve_default_behavior(repo: Path, overlays: dict[str, str] | None) -> None:
    """Prove omitted and empty overlays preserve default behavior and sandbox isolation flags."""
    recorder = ProcessRecorder()
    with patch("scripts.two_site_agent_sandbox._run_bounded_process", side_effect=recorder):
        result = run_tests(repo, [EXISTING_PATH], overlays=overlays)

    assert result["returncode"] == 0
    assert result["isolation"] == "bwrap:ro-source,empty-home,clearenv,unshare-all"
    assert result["snapshot_files"] == 3
    assert recorder.snapshot_inspections[0]["files"].get(GATE_PATH) == REAL_GATE_BYTES
    expected_bytes = (
        len(ENV_CONTENT.encode("utf-8"))
        + len(ORIGINAL_EXISTING_CONTENT.encode("utf-8"))
        + len(REAL_GATE_BYTES)
    )
    assert result["snapshot_bytes"] == expected_bytes

    assert len(recorder.calls) == 2
    final_argv = recorder.calls[-1]
    assert final_argv[0] == "/usr/bin/prlimit"

    work_idx = final_argv.index("/work")
    assert final_argv[work_idx - 2:work_idx] == ["--ro-bind", str(recorder.snapshot_inspections[0]["snapshot_dir"])]

    root_idx = final_argv.index("/root")
    assert final_argv[root_idx - 1] == "--dir"

    assert "--clearenv" in final_argv
    assert "--unshare-all" in final_argv
    assert "--unshare-user" in final_argv
    assert "--disable-userns" in final_argv
    assert "--die-with-parent" in final_argv
    assert "--new-session" in final_argv
    assert "--share-net" not in final_argv
    gate_flag_idx = final_argv.index("scripts.two_site_pre_review_pytest_gate")
    assert final_argv[gate_flag_idx - 1:gate_flag_idx + 1] == ["-p", "scripts.two_site_pre_review_pytest_gate"]
    assert final_argv[-1] == EXISTING_PATH

    assert (repo / EXISTING_PATH).read_text(encoding="utf-8") == ORIGINAL_EXISTING_CONTENT
    assert not (repo / NEW_PATH).exists()
    assert (repo / GATE_PATH).read_bytes() == REAL_GATE_BYTES
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    assert status.stdout.strip() == ""

    with pytest.raises(SandboxError, match="test selector not in authorized source snapshot"):
        run_tests(repo, [NEW_PATH], overlays=overlays)
