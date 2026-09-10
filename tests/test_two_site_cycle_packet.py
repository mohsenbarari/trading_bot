"""Tests for two_site_cycle_packet: packet losslessness, patch format, safety and efficiency."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import two_site_cycle_packet as packet_mod
from scripts import two_site_cycle_tools as ct
from scripts import two_site_proposal_runner as runner

INITIAL_CODE = (
    "import os\n"
    "LIMIT = 2\n"
    "@staticmethod\n"
    "def f(x):\n"
    "    return x + LIMIT\n"
    "\n"
    "def middle_1():\n"
    "    return 10\n"
    "\n"
    "def middle_2():\n"
    "    return 20\n"
    "\n"
    "def middle_3():\n"
    "    return 30\n"
    "\n"
    "def middle_4():\n"
    "    return 40\n"
    "\n"
    "def unrelated():\n"
    "    return 1\n"
)

UPDATED_CODE = (
    "import os\n"
    "LIMIT = 5\n"
    "@staticmethod\n"
    "def f(x):\n"
    "    return x + LIMIT\n"
    "\n"
    "def middle_1():\n"
    "    return 10\n"
    "\n"
    "def middle_2():\n"
    "    return 20\n"
    "\n"
    "def middle_3():\n"
    "    return 30\n"
    "\n"
    "def middle_4():\n"
    "    return 40\n"
    "\n"
    "def unrelated():\n"
    "    return 99\n"
)


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

    git("init")
    git("config", "user.email", "offline@example.invalid")
    git("config", "user.name", "Offline Fixture")
    (tmp_path / ".gitignore").write_text(".local/\n")
    (tmp_path / "gap.md").write_text("# Gap A\nCharacterize method f.\n")
    (tmp_path / "code.py").write_text(INITIAL_CODE)
    (tmp_path / "doc.md").write_text("start\n--- old divider\nend\n")
    git("add", "--", ".gitignore", "gap.md", "code.py", "doc.md")
    git("commit", "-m", "offline fixture")
    return tmp_path, git


def make_proposal_chain(
    repo,
    worker_changes=None,
    reviewer_findings=None,
    code_content=None,
    allowed_files=None,
):
    root, git = repo
    spec = root / "spec.json"
    all_allowed = allowed_files if allowed_files is not None else ["code.py", "test_new.py", "doc.md"]
    task_def = {
        "task_id": "GAP-A-001",
        "goal": "Test method f.",
        "context_files": ["gap.md"],
        "allowed_files": all_allowed,
        "requirements": ["1. Characterize method f"],
        "test_selectors": ["test_new.py"],
    }
    spec.write_text(
        json.dumps({
            "task": task_def,
            "gap_reference": "gap.md#gap-a",
            "snippets": [],
        })
    )
    output = root / ".local/two-site-refactor/tasks/new.json"
    ct.build(root, spec, output)
    spec.unlink()

    task_value, sources = runner.load_task(root, output)
    head = git("rev-parse", "HEAD")
    base = root / ".local/two-site-refactor/proposals"
    worker = base / "w1"
    reviewer = base / "r1"
    worker.mkdir(parents=True, exist_ok=True)
    reviewer.mkdir(parents=True, exist_ok=True)

    if worker_changes is None:
        if code_content is None:
            code_content = UPDATED_CODE
        worker_changes = [
            {
                "path": "test_new.py",
                "original_sha256": None,
                "content": "def test_new():\n    assert True\n",
            },
            {
                "path": "code.py",
                "original_sha256": sources["code.py"]["sha256"],
                "content": code_content,
            },
        ]

    result = {
        "status": "PROPOSAL_READY",
        "summary": "added test and updated limit and unrelated",
        "covered_requirements": [1],
        "findings": [],
        "changes": worker_changes,
    }
    snapshot = {"source_head": head, "task": task_value, "sources": sources}
    runner.write_json(worker / "result.json", result)
    digest = runner.file_sha256(worker / "result.json")
    runner.write_json(worker / "task.json", snapshot)
    runner.write_json(
        worker / "status.json",
        {
            "run_id": "w1",
            "role": "worker",
            "head": head,
            "model": runner.MODEL,
            "state": "PROPOSAL_READY",
            "conversation_id": "wc",
            "result_sha256": digest,
        },
    )

    rev_findings = reviewer_findings if reviewer_findings is not None else []
    rev_result = {
        "status": "VERIFIED",
        "summary": "verified cleanly",
        "covered_requirements": [1],
        "findings": rev_findings,
        "changes": [],
    }
    runner.write_json(reviewer / "result.json", rev_result)
    runner.write_json(
        reviewer / "task.json",
        {**snapshot, "proposal": (worker / "result.json").read_text()},
    )
    runner.write_json(
        reviewer / "status.json",
        {
            "run_id": "r1",
            "role": "verifier",
            "head": head,
            "model": runner.MODEL,
            "state": "VERIFIED",
            "conversation_id": "rc",
            "result_sha256": runner.file_sha256(reviewer / "result.json"),
            "input_worker_run_id": "w1",
            "input_proposal_sha256": digest,
            "input_worker_conversation_id": "wc",
        },
    )
    return output, digest, result, reviewer, head


def test_packet_losslessness_and_no_duplicate_code(repo):
    root, _ = repo
    output, digest, result, _, head = make_proposal_chain(repo)
    packet_text = packet_mod.packet(root, output, "w1", "r1", digest, full=False)

    assert "Task ID: GAP-A-001" in packet_text
    assert f"Proposal SHA256: {digest}" in packet_text
    assert f"Head: {head}" in packet_text
    assert "Worker Run ID: w1" in packet_text
    assert "Reviewer Run ID: r1" in packet_text
    assert "Semantic Review: REQUIRED_BY_CODEX" in packet_text
    assert "Tests: NOT_RUN" in packet_text

    assert "REQUIREMENTS" in packet_text
    assert "1. Characterize method f" in packet_text

    assert "added test and updated limit and unrelated" in packet_text
    assert "verified cleanly" in packet_text

    assert "def test_new():\n    assert True\n" in packet_text
    assert "--- Modified File: code.py" in packet_text
    assert "-LIMIT = 2" in packet_text
    assert "+LIMIT = 5" in packet_text
    assert "-    return 1" in packet_text
    assert "+    return 99" in packet_text

    assert packet_text.count("def test_new():") == 1
    assert packet_text.count("+LIMIT = 5") == 1
    assert packet_text.count("+    return 99") == 1


def test_packet_full_source_option(repo):
    root, _ = repo
    output, digest, _, _, _ = make_proposal_chain(repo)
    packet_text = packet_mod.packet(root, output, "w1", "r1", digest, full=True)

    assert "--- File: code.py (full source" in packet_text
    assert "LIMIT = 5" in packet_text
    assert "return 99" in packet_text
    assert "def middle_1():" in packet_text
    assert "-LIMIT = 2" not in packet_text
    assert "def test_new():\n    assert True\n" in packet_text


def test_patch_renderer_assertions_unconditional(repo):
    root, _ = repo
    output, digest, result, _, _ = make_proposal_chain(repo)
    patch_text = packet_mod.patch(root, output, "w1", "r1", digest)

    assert patch_text.startswith("*** Begin Patch\n")
    assert patch_text.endswith("*** End Patch\n")
    assert "{" not in patch_text
    assert "*** Add File: test_new.py\n" in patch_text
    assert "+def test_new():\n" in patch_text
    assert "+    assert True\n" in patch_text
    assert "*** Update File: code.py\n" in patch_text
    assert patch_text.count("@@\n") == 2
    assert "-LIMIT = 2\n" in patch_text
    assert "+LIMIT = 5\n" in patch_text
    assert "-    return 1\n" in patch_text
    assert "+    return 99\n" in patch_text
    assert "gap.md" not in patch_text


def test_patch_executable_integration_if_available(repo):
    root, _ = repo
    output, digest, _, _, _ = make_proposal_chain(repo)
    patch_text = packet_mod.patch(root, output, "w1", "r1", digest)

    apply_patch_bin = shutil.which("apply_patch")
    if not apply_patch_bin:
        pytest.skip("apply_patch executable not available in environment")

    proc = subprocess.run([apply_patch_bin], input=patch_text, cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, f"apply_patch executable failed: {proc.stderr}"

    assert (root / "test_new.py").read_text() == "def test_new():\n    assert True\n"
    assert (root / "code.py").read_text() == UPDATED_CODE


def test_patch_leading_diff_marker_content_roundtrip(repo):
    root, _ = repo
    doc_new_content = "start\n+++ new divider\nend\n"
    changes = [
        {
            "path": "doc.md",
            "original_sha256": runner.sha("start\n--- old divider\nend\n".encode("utf-8")),
            "content": doc_new_content,
        },
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=changes)
    patch_text = packet_mod.patch(root, output, "w1", "r1", digest)

    assert "*** Update File: doc.md\n" in patch_text
    assert "@@\n" in patch_text
    assert "---- old divider\n" in patch_text
    assert "++++ new divider\n" in patch_text
    assert " start\n" in patch_text
    assert " end\n" in patch_text

    apply_patch_bin = shutil.which("apply_patch")
    if apply_patch_bin:
        proc = subprocess.run([apply_patch_bin], input=patch_text, cwd=root, capture_output=True, text=True)
        assert proc.returncode == 0, f"apply_patch failed: {proc.stderr}"
        assert (root / "doc.md").read_text() == doc_new_content


def test_no_filesystem_changes_when_rendering(repo):
    root, git = repo
    output, digest, _, _, _ = make_proposal_chain(repo)

    before_status = git("status", "--porcelain=v1", "--untracked-files=all")
    before_files = sorted([str(p.relative_to(root)) for p in root.rglob("*")])

    packet_mod.packet(root, output, "w1", "r1", digest, full=False)
    packet_mod.patch(root, output, "w1", "r1", digest)

    after_status = git("status", "--porcelain=v1", "--untracked-files=all")
    after_files = sorted([str(p.relative_to(root)) for p in root.rglob("*")])

    assert before_status == after_status
    assert before_files == after_files


@pytest.mark.parametrize("rejection_case", [
    "wrong_sha", "drifted_head", "modified_source", "dirty_tree", "task_drift", "reviewer_drift"
])
def test_rejections_for_stale_or_invalid_states(repo, rejection_case):
    root, git = repo
    output, digest, _, reviewer, _ = make_proposal_chain(repo)

    req_sha = digest
    if rejection_case == "wrong_sha":
        req_sha = "0" * 64
    elif rejection_case == "drifted_head":
        git("commit", "--allow-empty", "-m", "drift head")
    elif rejection_case == "modified_source":
        with (root / "code.py").open("a") as f:
            f.write("# drift\n")
    elif rejection_case == "dirty_tree":
        (root / "dirty.txt").write_text("untracked dirty file\n")
    elif rejection_case == "task_drift":
        val = json.loads(output.read_text())
        val["requirements"].append("extra requirement")
        output.write_text(json.dumps(val))
    elif rejection_case == "reviewer_drift":
        status = json.loads((reviewer / "status.json").read_text())
        status["conversation_id"] = "wc"
        runner.write_json(reviewer / "status.json", status)

    with pytest.raises((ValueError, runner.ProposalError)):
        packet_mod.packet(root, output, "w1", "r1", req_sha)

    with pytest.raises((ValueError, runner.ProposalError)):
        packet_mod.patch(root, output, "w1", "r1", req_sha)


def test_unsupported_eof_failure(repo):
    root, _ = repo
    no_eof_change = [
        {
            "path": "test_new.py",
            "original_sha256": None,
            "content": "def test_new():\n    assert True",
        }
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=no_eof_change)

    with pytest.raises(runner.ProposalError, match="missing final newline"):
        packet_mod.packet(root, output, "w1", "r1", digest)

    with pytest.raises(runner.ProposalError, match="missing final newline"):
        packet_mod.patch(root, output, "w1", "r1", digest)


@pytest.mark.parametrize("bad_content", ["crlf", "nul"])
def test_unrepresentable_content_failure(repo, bad_content):
    root, _ = repo
    bad_str = "def test():\r\n    assert True\r\n" if bad_content == "crlf" else "def test():\0\n    assert True\n"
    change = [
        {
            "path": "test_new.py",
            "original_sha256": None,
            "content": bad_str,
        }
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=change)

    expected_msg = "unrepresentable content" if bad_content == "crlf" else "unsafe generated content"

    with pytest.raises(runner.ProposalError, match=expected_msg):
        packet_mod.packet(root, output, "w1", "r1", digest)

    with pytest.raises(runner.ProposalError, match=expected_msg):
        packet_mod.patch(root, output, "w1", "r1", digest)


def test_control_character_path_failure(repo):
    root, _ = repo
    bad_path_change = [
        {
            "path": "test\nnew.py",
            "original_sha256": None,
            "content": "def test_new():\n    assert True\n",
        }
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=bad_path_change)

    with pytest.raises(runner.ProposalError):
        packet_mod.patch(root, output, "w1", "r1", digest)


def test_lf_only_splitting_preserves_unicode_separators(repo):
    root, _ = repo
    unicode_content = "def test_unicode():\n    val = 'hello\u2028world\x0b'\n    assert True\n"
    unicode_changes = [
        {
            "path": "test_new.py",
            "original_sha256": None,
            "content": unicode_content,
        }
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=unicode_changes)

    packet_text = packet_mod.packet(root, output, "w1", "r1", digest, full=False)
    assert unicode_content in packet_text

    patch_text = packet_mod.patch(root, output, "w1", "r1", digest)
    assert "+    val = 'hello\u2028world\x0b'\n" in patch_text
    assert patch_text.count("+\n") == 0
    assert "+    val = " in patch_text


def test_unsupported_update_trailing_blank_line_rejected(repo, capsys):
    root, git = repo
    trailing_blank_code = UPDATED_CODE + "\n"
    blank_changes = [
        {
            "path": "code.py",
            "original_sha256": runner.sha(INITIAL_CODE.encode("utf-8")),
            "content": trailing_blank_code,
        },
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=blank_changes)

    packet_text = packet_mod.packet(root, output, "w1", "r1", digest)
    assert "code.py" in packet_text
    assert "--- Modified File: code.py" in packet_text

    before_status = git("status", "--porcelain=v1", "--untracked-files=all")
    with pytest.raises(runner.ProposalError, match="trailing blank"):
        packet_mod.patch(root, output, "w1", "r1", digest)

    after_status = git("status", "--porcelain=v1", "--untracked-files=all")
    assert before_status == after_status

    rc = packet_mod.main([
        "--root", str(root),
        "patch",
        "--task", str(output),
        "--worker", "w1",
        "--reviewer", "r1",
        "--sha", digest,
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error:" in captured.err


def test_new_file_trailing_blank_line_roundtrip(repo):
    root, _ = repo
    trailing_blank_new = "def test_trailing():\n    pass\n\n"
    blank_changes = [
        {
            "path": "test_new.py",
            "original_sha256": None,
            "content": trailing_blank_new,
        },
    ]
    output, digest, _, _, _ = make_proposal_chain(repo, worker_changes=blank_changes)
    patch_text = packet_mod.patch(root, output, "w1", "r1", digest)

    assert "*** Add File: test_new.py\n" in patch_text
    assert "+\n" in patch_text

    apply_patch_bin = shutil.which("apply_patch")
    if apply_patch_bin:
        proc = subprocess.run([apply_patch_bin], input=patch_text, cwd=root, capture_output=True, text=True)
        assert proc.returncode == 0, f"apply_patch failed: {proc.stderr}"
        assert (root / "test_new.py").read_text() == trailing_blank_new


def test_deterministic_output_and_size_comparison(repo):
    root, _ = repo
    output, digest, result, reviewer, _ = make_proposal_chain(repo)
    worker_dir = reviewer.parent / "w1"

    out1 = packet_mod.packet(root, output, "w1", "r1", digest)
    out2 = packet_mod.packet(root, output, "w1", "r1", digest)
    assert out1 == out2

    patch1 = packet_mod.patch(root, output, "w1", "r1", digest)
    patch2 = packet_mod.patch(root, output, "w1", "r1", digest)
    assert patch1 == patch2

    task_snapshot = (worker_dir / "task.json").read_text()
    worker_status = (worker_dir / "status.json").read_text()
    worker_result = (worker_dir / "result.json").read_text()
    reviewer_status = (reviewer / "status.json").read_text()
    reviewer_result = (reviewer / "result.json").read_text()
    repeated_full_json = json.dumps({
        "task_snapshot": task_snapshot,
        "worker_status": worker_status,
        "worker_result": worker_result,
        "reviewer_status": reviewer_status,
        "reviewer_result": reviewer_result,
    })

    packet_bytes_len = len(out1.encode("utf-8"))
    json_bytes_len = len(repeated_full_json.encode("utf-8"))

    assert packet_bytes_len < json_bytes_len
    # Note: Byte savings reflect format compaction and do not equate to measured model token savings.


def test_cli_execution_via_main(repo, capsys):
    root, _ = repo
    output, digest, _, _, _ = make_proposal_chain(repo)

    rc = packet_mod.main([
        "--root", str(root),
        "packet",
        "--task", str(output),
        "--worker", "w1",
        "--reviewer", "r1",
        "--sha", digest,
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "PROPOSAL PACKET" in captured.out
    assert captured.err == ""

    rc_patch = packet_mod.main([
        "--root", str(root),
        "patch",
        "--task", str(output),
        "--worker", "w1",
        "--reviewer", "r1",
        "--sha", digest,
    ])
    assert rc_patch == 0
    captured_patch = capsys.readouterr()
    assert captured_patch.out.startswith("*** Begin Patch\n")
    assert captured_patch.err == ""

    rc_fail = packet_mod.main([
        "--root", str(root),
        "patch",
        "--task", str(output),
        "--worker", "w1",
        "--reviewer", "r1",
        "--sha", "0" * 64,
    ])
    assert rc_fail == 1
    captured_fail = capsys.readouterr()
    assert captured_fail.out == ""
    assert "Error:" in captured_fail.err


def test_error_produces_no_partial_stdout(repo, capsys):
    root, _ = repo
    output, digest, _, _, _ = make_proposal_chain(repo)

    rc = packet_mod.main([
        "--root", str(root),
        "patch",
        "--task", str(output),
        "--worker", "w1",
        "--reviewer", "r1",
        "--sha", "0" * 64,
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error:" in captured.err
