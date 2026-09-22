"""Integration tests for two_site_cycle_tools single-pass source bundle extraction."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import two_site_cycle_tools as ct
from scripts import two_site_proposal_runner as runner
from tests.test_two_site_cycle_tools import repo, task


def _build_170k_source() -> bytes:
    header = (
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n\n"
        "PERSIAN_GREETING = 'سلام\u200cدنیا و \u200dترکیب'\n"
        "BIDI_SAMPLE = 'abc\u200e\u200fdef'\n"
        "PARAGRAPH_SEP = 'line1\u2028line2\u2029line3'\n\n"
        "def custom_decorator(fn):\n"
        "    return fn\n\n"
        "@custom_decorator\n"
        "def narrow_fn(x: int) -> int:\n"
        "    return x + 42\n\n"
        "def ignored_worker():\n"
        "    return 'unrelated'\n\n"
    )
    raw_header = header.encode("utf-8")
    pad_needed = 170000 - len(raw_header)
    assert pad_needed > 3
    padding = b"# " + b"x" * (pad_needed - 3) + b"\n"
    source_bytes = raw_header + padding
    assert len(source_bytes) == 170000
    return source_bytes


def test_source_bundle_build_and_recording_revalidation(repo, monkeypatch):
    root, git = repo
    source_bytes = _build_170k_source()
    (root / "large_source.py").write_bytes(source_bytes)
    git("add", "--", "large_source.py")
    git("commit", "-m", "add tracked 170k source file")

    local_dir = root / ".local"
    local_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir = local_dir / "two-site-refactor/tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    spec_data = {
        "task": task(),
        "gap_reference": "gap.md#gap-a",
        "scenario_key": "scenario_a",
        "snippets": [
            {
                "path": "large_source.py",
                "symbols": [
                    "PERSIAN_GREETING",
                    "BIDI_SAMPLE",
                    "PARAGRAPH_SEP",
                    "custom_decorator",
                    "narrow_fn",
                ],
            },
            {"path": "code.py", "symbols": ["f"]},
        ],
    }
    spec_path = local_dir / "bundle_spec.json"
    spec_path.write_text(json.dumps(spec_data))

    head_before = git("rev-parse", "HEAD")
    status_before = git("status", "--porcelain=v1", "--untracked-files=all")
    assert status_before == ""

    def forbidden_socket(*args, **kwargs):
        raise AssertionError("socket usage is forbidden in local tests")

    def forbidden_run(*args, **kwargs):
        raise AssertionError("runner.run usage is forbidden in local tests")

    monkeypatch.setattr("socket.socket", forbidden_socket)
    monkeypatch.setattr(runner, "run", forbidden_run)

    recorded_calls: list[list[dict]] = []
    real_extract_bundle = ct.extract_bundle

    def recording_extract_bundle(r, requests):
        recorded_calls.append(list(requests))
        return real_extract_bundle(r, requests)

    monkeypatch.setattr(ct, "extract_bundle", recording_extract_bundle)

    output_path = tasks_dir / "task_ready.json"
    ready_result = ct.build(root, spec_path, output_path)

    assert ready_result.get("state") == "TASK_READY"
    assert ready_result.get("excerpt_count") == 2
    assert ready_result.get("task_path") == str(output_path)
    assert "context_budget" in ready_result
    cb = ready_result["context_budget"]
    assert cb == {
        "source_count": 2,
        "source_bytes": 170000 + len((root / "code.py").read_bytes()),
        "max_source_bytes": 2000000,
        "max_total_bytes": 8000000,
    }

    assert output_path.exists()
    assert output_path.stat().st_mode & 0o777 == 0o600
    task_raw = output_path.read_bytes()
    assert len(task_raw) <= 160000

    head_after = git("rev-parse", "HEAD")
    status_after = git("status", "--porcelain=v1", "--untracked-files=all")
    assert head_after == head_before
    assert status_after == status_before

    loaded_task, sources = runner.load_task(root, output_path)
    assert loaded_task["task_id"] == "GAP-A-001"
    assert "large_source.py" not in sources
    assert "code.py" not in sources

    package = json.loads(loaded_task["goal"].split(ct.MARKER, 1)[1])
    excerpts = package["excerpts"]
    assert len(excerpts) == 2
    large_rec = excerpts[0]
    assert large_rec["path"] == "large_source.py"
    assert large_rec["sha256"] == runner.sha(source_bytes)
    combined_text = "".join(r["text"] for r in large_rec["ranges"])
    assert "PERSIAN_GREETING = 'سلام\u200cدنیا و \u200dترکیب'" in combined_text
    assert "BIDI_SAMPLE = 'abc\u200e\u200fdef'" in combined_text
    assert "PARAGRAPH_SEP = 'line1\u2028line2\u2029line3'" in combined_text
    assert "@custom_decorator" in combined_text
    assert "def narrow_fn" in combined_text
    assert "import os" in combined_text
    assert "ignored_worker" not in combined_text

    assert len(recorded_calls) == 3
    for batch in recorded_calls:
        assert len(batch) == 2
        assert [item["path"] for item in batch] == ["large_source.py", "code.py"]


def test_build_invalid_schema_preserves_bounded_blocked(repo):
    root, _ = repo
    local_dir = root / ".local"
    local_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir = local_dir / "two-site-refactor/tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    bad_task = {**task(), "requirements": []}
    bad_spec_data = {
        "task": bad_task,
        "gap_reference": "gap.md#gap-a",
        "scenario_key": "scenario_a",
        "snippets": [{"path": "code.py", "symbols": ["f"]}],
    }
    bad_spec_path = local_dir / "bad_spec.json"
    bad_spec_path.write_text(json.dumps(bad_spec_data))
    bad_output_path = tasks_dir / "bad_task.json"

    blocked_result = ct.build(root, bad_spec_path, bad_output_path)
    assert blocked_result.get("state") == "TASK_BLOCKED"
    assert "context_budget" not in blocked_result
    assert "task_path" not in blocked_result
    assert not bad_output_path.exists()


def test_bundle_revalidation_drift_rejection(repo):
    root, git = repo
    local_dir = root / ".local"
    local_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir = local_dir / "two-site-refactor/tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    spec_data = {
        "task": task(),
        "gap_reference": "gap.md#gap-a",
        "scenario_key": "scenario_a",
        "snippets": [{"path": "code.py", "symbols": ["f"]}],
    }
    spec_path = local_dir / "spec.json"
    spec_path.write_text(json.dumps(spec_data))
    output_path = tasks_dir / "task_drift.json"
    ready = ct.build(root, spec_path, output_path)
    assert ready["state"] == "TASK_READY"

    raw_text = output_path.read_text()
    task_obj = json.loads(raw_text)
    goal, raw_pkg = task_obj["goal"].split(ct.MARKER, 1)

    # 1. Text drift rejection
    pkg = json.loads(raw_pkg)
    pkg["excerpts"][0]["ranges"][0]["text"] = "drifted_text = True\n"
    task_obj["goal"] = goal + ct.MARKER + json.dumps(pkg)
    output_path.write_text(json.dumps(task_obj))
    with pytest.raises(runner.ProposalError, match="excerpt provenance mismatch"):
        runner.load_task(root, output_path)

    # 2. Hash drift rejection
    pkg = json.loads(raw_pkg)
    pkg["excerpts"][0]["sha256"] = "0" * 64
    task_obj["goal"] = goal + ct.MARKER + json.dumps(pkg)
    output_path.write_text(json.dumps(task_obj))
    with pytest.raises(runner.ProposalError, match="excerpt provenance mismatch"):
        runner.load_task(root, output_path)

    # 3. Range drift rejection
    pkg = json.loads(raw_pkg)
    pkg["excerpts"][0]["ranges"][0]["start"] = 999
    task_obj["goal"] = goal + ct.MARKER + json.dumps(pkg)
    output_path.write_text(json.dumps(task_obj))
    with pytest.raises(runner.ProposalError, match="excerpt provenance mismatch"):
        runner.load_task(root, output_path)

    # 4. Source content drift rejection
    output_path.write_text(raw_text)
    with (root / "code.py").open("a") as f:
        f.write("# drift\n")
    with pytest.raises(runner.ProposalError, match="excerpt provenance mismatch"):
        runner.load_task(root, output_path)

    git("checkout", "--", "code.py")

    # 5. Git HEAD drift rejection
    output_path.write_text(raw_text)
    git("commit", "--allow-empty", "-m", "drift head")
    with pytest.raises(runner.ProposalError, match="stale or invalid source excerpt package"):
        runner.load_task(root, output_path)


def test_standalone_import_identity():
    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"

    code_std = (
        "import sys\n"
        f"sys.path.insert(0, {str(scripts_dir)!r})\n"
        "import two_site_cycle_tools as ct_std\n"
        "import two_site_source_excerpts as excerpts_std\n"
        "import two_site_proposal_runner as runner_std\n"
        "assert ct_std.runner.ProposalError is runner_std.ProposalError\n"
        "assert excerpts_std.ProposalError is runner_std.ProposalError\n"
        "assert ct_std.extract_bundle is excerpts_std.extract_bundle\n"
    )
    res_std = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code_std],
        capture_output=True,
        text=True,
    )
    assert res_std.returncode == 0, f"STDOUT: {res_std.stdout}\nSTDERR: {res_std.stderr}"

    code_pkg = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "import scripts.two_site_cycle_tools as ct_pkg\n"
        "import scripts.two_site_source_excerpts as excerpts_pkg\n"
        "import scripts.two_site_proposal_runner as runner_pkg\n"
        "assert ct_pkg.runner.ProposalError is runner_pkg.ProposalError\n"
        "assert excerpts_pkg.ProposalError is runner_pkg.ProposalError\n"
        "assert ct_pkg.extract_bundle is excerpts_pkg.extract_bundle\n"
    )
    res_pkg = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code_pkg],
        capture_output=True,
        text=True,
    )
    assert res_pkg.returncode == 0, f"STDOUT: {res_pkg.stdout}\nSTDERR: {res_pkg.stderr}"
