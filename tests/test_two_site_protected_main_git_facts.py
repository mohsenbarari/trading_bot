"""Hermetic unit tests for scripts/two_site_protected_main_git_facts.py under AE03."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
from typing import Any, Callable
import unittest.mock

import pytest

# Collection guard: stat both real files and bounded read before target imports
_TEST_FILE = Path(__file__).resolve()
_REPO_ROOT = _TEST_FILE.parent.parent
_SOURCE_FILE = _REPO_ROOT / "scripts" / "two_site_protected_main_git_facts.py"

_src_st = _SOURCE_FILE.stat()
_test_st = _TEST_FILE.stat()

with open(_SOURCE_FILE, "rb") as _f:
    _src_bytes = _f.read(8001)
with open(_TEST_FILE, "rb") as _f:
    _test_bytes = _f.read(24001)

assert len(_src_bytes) <= 8000, f"source exceeded 8000 bytes: {len(_src_bytes)}"
assert len(_test_bytes) <= 24000, f"test exceeded 24000 bytes: {len(_test_bytes)}"
assert _src_st.st_size == len(_src_bytes), "source stat size inconsistency"
assert _test_st.st_size == len(_test_bytes), "test stat size inconsistency"
assert len(_src_bytes) + len(_test_bytes) <= 32000, "combined exceeded 32000 bytes"

from scripts.two_site_protected_main_git_facts import (
    ProtectedMainGitFactsError,
    verify_protected_main_git_locked,
)
import scripts.two_site_protected_main_git_facts

try:
    from scripts.two_site_maintenance_transition_git import GIT_BIN
except ImportError:
    from two_site_maintenance_transition_git import GIT_BIN  # type: ignore[no-redef]

try:
    from scripts.two_site_agent_sandbox import SandboxError
except ImportError:
    from two_site_agent_sandbox import SandboxError  # type: ignore[no-redef]


def _git(repo: Path, *args: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Committer",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Committer",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    res = subprocess.run(
        [GIT_BIN, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return res.stdout.strip()


def _init_fixture_repo(tmp_path: Path) -> tuple[Path, dict[str, str], dict[str, str]]:
    repo = (tmp_path / "repo").resolve()
    repo.mkdir(mode=0o755)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Committer")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "base.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "prior main")
    prior_main_sha = _git(repo, "rev-parse", "HEAD")

    (repo / "base.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "approved main")
    approved_main_sha = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "refactor/two-site-architecture", prior_main_sha)
    (repo / "work.txt").write_text("w1\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "prior head")
    prior_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "merge", "--no-ff", approved_main_sha, "-m", "merge approved main")
    expected_head = _git(repo, "rev-parse", "HEAD")

    expected_origin_main_sha = approved_main_sha
    _git(repo, "update-ref", "refs/remotes/origin/main", expected_origin_main_sha)

    expected_candidate_sha = expected_head
    _git(repo, "update-ref", "refs/heads/candidate/wa-ir-standby-v1", expected_candidate_sha)

    _git(repo, "checkout", "refactor/two-site-architecture")

    kwargs = {
        "prior_main_sha": prior_main_sha,
        "approved_main_sha": approved_main_sha,
        "prior_head": prior_head,
        "expected_head": expected_head,
        "expected_origin_main_sha": expected_origin_main_sha,
        "expected_candidate_sha": expected_candidate_sha,
    }
    expected_output = {
        "root": str(repo),
        "branch": "refs/heads/refactor/two-site-architecture",
        "head": expected_head,
        "main": approved_main_sha,
        "origin_main": expected_origin_main_sha,
        "candidate": expected_candidate_sha,
        "status": "",
    }
    return repo, kwargs, expected_output


def _snapshot_repo(repo: Path) -> dict[str, Any]:
    files: dict[str, tuple[bytes, str]] = {}
    for p in sorted(repo.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file():
            st = p.stat()
            files[str(p.relative_to(repo))] = (p.read_bytes(), oct(st.st_mode))
    refs_out = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    refs = dict(line.split() for line in refs_out.splitlines() if line)
    try:
        head_ref = _git(repo, "symbolic-ref", "HEAD")
    except Exception:
        head_ref = "detached"
    return {"files": files, "refs": refs, "head_ref": head_ref}


def _run_fault_custody(
    repo: Path,
    base_kwargs: dict[str, str],
    expected_output: dict[str, str],
    inject_fault: Callable[[Path, dict[str, str]], tuple[Any, ...]],
) -> None:
    frozen_kwargs = copy.deepcopy(base_kwargs)
    frozen_expected = copy.deepcopy(expected_output)
    res_before = verify_protected_main_git_locked(repo, **copy.deepcopy(base_kwargs))
    assert res_before == frozen_expected

    repo_state_before = _snapshot_repo(repo)

    caller_kwargs = copy.deepcopy(base_kwargs)
    fault_res = inject_fault(repo, caller_kwargs)
    bad_kwargs = fault_res[0]
    restore_fn = fault_res[1]
    expected_bad_state = fault_res[2] if len(fault_res) > 2 else None
    bad_kwargs_snapshot = copy.deepcopy(bad_kwargs)
    bad_repo_state = copy.deepcopy(expected_bad_state) if expected_bad_state is not None else _snapshot_repo(repo)

    try:
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$") as exc_info:
            verify_protected_main_git_locked(repo, **bad_kwargs)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True

        assert bad_kwargs == bad_kwargs_snapshot
        assert expected_output == frozen_expected
        assert _snapshot_repo(repo) == bad_repo_state
    finally:
        restore_fn()

    assert _snapshot_repo(repo) == repo_state_before
    res_after = verify_protected_main_git_locked(repo, **copy.deepcopy(base_kwargs))
    assert res_after == frozen_expected
    assert base_kwargs == frozen_kwargs


def _inject_approved_main_not_integrated(repo: Path, kwargs: dict[str, str]) -> tuple[dict[str, str], Callable[[], None]]:
    old_main = kwargs["approved_main_sha"]
    _git(repo, "checkout", "main")
    (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    _git(repo, "add", "extra.txt")
    _git(repo, "commit", "-m", "unintegrated")
    new_main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "refactor/two-site-architecture")
    kwargs["approved_main_sha"] = new_main

    def restore() -> None:
        _git(repo, "checkout", "main")
        _git(repo, "reset", "--hard", old_main)
        _git(repo, "checkout", "refactor/two-site-architecture")
        (repo / "extra.txt").unlink(missing_ok=True)

    return kwargs, restore


def _inject_prior_main_non_ancestor(repo: Path, kwargs: dict[str, str]) -> tuple[dict[str, str], Callable[[], None]]:
    tree = _git(repo, "write-tree")
    unrelated = _git(repo, "commit-tree", tree, "-m", "unrelated")
    kwargs["prior_main_sha"] = unrelated
    return kwargs, lambda: None


def _inject_prior_head_non_ancestor(repo: Path, kwargs: dict[str, str]) -> tuple[dict[str, str], Callable[[], None]]:
    tree = _git(repo, "write-tree")
    unrelated = _git(repo, "commit-tree", tree, "-m", "unrelated")
    kwargs["prior_head"] = unrelated
    return kwargs, lambda: None


def _inject_dirty_untracked(repo: Path, kwargs: dict[str, str]) -> tuple[dict[str, str], Callable[[], None]]:
    dirty = repo / "untracked.txt"
    dirty.write_text("dirty\n", encoding="utf-8")

    def restore() -> None:
        if dirty.exists():
            dirty.unlink()

    return kwargs, restore


def _inject_detached_branch(repo: Path, kwargs: dict[str, str]) -> tuple[dict[str, str], Callable[[], None]]:
    _git(repo, "checkout", "--detach", "HEAD")

    def restore() -> None:
        _git(repo, "checkout", "refactor/two-site-architecture")

    return kwargs, restore


def _inject_post_query_ref_drift(repo: Path, kwargs: dict[str, str]) -> tuple[dict[str, str], Callable[[], None], dict[str, Any]]:
    orig_run = scripts.two_site_protected_main_git_facts._run_git_query
    drifted_ref = "refs/heads/candidate/wa-ir-standby-v1"
    old_target = kwargs["expected_candidate_sha"]
    drift_target = kwargs["prior_main_sha"]
    count = 0

    def hooked_run(root: Path, argv: Any, **kw: Any) -> bytes:
        nonlocal count
        count += 1
        if count == 16:
            _git(repo, "update-ref", drifted_ref, drift_target)
        return orig_run(root, argv, **kw)

    scripts.two_site_protected_main_git_facts._run_git_query = hooked_run

    def restore() -> None:
        scripts.two_site_protected_main_git_facts._run_git_query = orig_run
        _git(repo, "update-ref", drifted_ref, old_target)

    current_state = _snapshot_repo(repo)
    bad_refs = dict(current_state["refs"])
    bad_refs[drifted_ref] = drift_target
    bad_state = {
        "files": current_state["files"],
        "refs": bad_refs,
        "head_ref": current_state["head_ref"],
    }
    return kwargs, restore, bad_state


def test_good_fixture_and_custody_matrix(tmp_path: Path) -> None:
    repo, kwargs, expected_output = _init_fixture_repo(tmp_path)
    _run_fault_custody(repo, kwargs, expected_output, _inject_approved_main_not_integrated)
    _run_fault_custody(repo, kwargs, expected_output, _inject_prior_main_non_ancestor)
    _run_fault_custody(repo, kwargs, expected_output, _inject_prior_head_non_ancestor)
    _run_fault_custody(repo, kwargs, expected_output, _inject_dirty_untracked)
    _run_fault_custody(repo, kwargs, expected_output, _inject_detached_branch)
    _run_fault_custody(repo, kwargs, expected_output, _inject_post_query_ref_drift)


def test_exact_ordered_23_query_argv_and_defaults(tmp_path: Path) -> None:
    repo, kwargs, expected_output = _init_fixture_repo(tmp_path)
    calls: list[tuple[list[str], tuple[Any, ...], dict[str, Any]]] = []
    orig_run = scripts.two_site_protected_main_git_facts._run_git_query

    def spied_run(root: Path, argv: Any, *args: Any, **kw: Any) -> bytes:
        calls.append((list(argv), args, kw))
        return orig_run(root, argv, *args, **kw)

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=spied_run
    ):
        res = verify_protected_main_git_locked(repo, **kwargs)
    assert res == expected_output
    assert len(calls) == 23

    expected_argvs = [
        ["rev-parse", "--show-toplevel"],
        ["symbolic-ref", "-q", "HEAD"],
        ["rev-parse", "--verify", "HEAD"],
        ["rev-parse", "--verify", "refs/heads/main"],
        ["rev-parse", "--verify", "refs/remotes/origin/main"],
        ["rev-parse", "--verify", "refs/heads/candidate/wa-ir-standby-v1"],
        ["status", "--porcelain=v1", "--untracked-files=all"],
        ["cat-file", "-t", kwargs["prior_main_sha"]],
        ["cat-file", "-t", kwargs["approved_main_sha"]],
        ["cat-file", "-t", kwargs["prior_head"]],
        ["cat-file", "-t", kwargs["expected_head"]],
        ["cat-file", "-t", kwargs["expected_origin_main_sha"]],
        ["cat-file", "-t", kwargs["expected_candidate_sha"]],
        ["merge-base", "--is-ancestor", kwargs["prior_main_sha"], kwargs["approved_main_sha"]],
        ["merge-base", "--is-ancestor", kwargs["prior_head"], kwargs["expected_head"]],
        ["merge-base", "--is-ancestor", kwargs["approved_main_sha"], kwargs["expected_head"]],
        ["rev-parse", "--show-toplevel"],
        ["symbolic-ref", "-q", "HEAD"],
        ["rev-parse", "--verify", "HEAD"],
        ["rev-parse", "--verify", "refs/heads/main"],
        ["rev-parse", "--verify", "refs/remotes/origin/main"],
        ["rev-parse", "--verify", "refs/heads/candidate/wa-ir-standby-v1"],
        ["status", "--porcelain=v1", "--untracked-files=all"],
    ]
    for idx, (argv, _, kw) in enumerate(calls):
        assert argv == expected_argvs[idx]
        assert "timeout" not in kw
        assert "output_limit" not in kw


def test_valid_format_wrong_bindings_reach_proof(tmp_path: Path) -> None:
    repo, kwargs, _ = _init_fixture_repo(tmp_path)

    bad_kwargs = copy.deepcopy(kwargs)
    bad_kwargs["expected_head"] = "f" * 40
    with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
        verify_protected_main_git_locked(repo, **bad_kwargs)

    bad_kwargs2 = copy.deepcopy(kwargs)
    bad_kwargs2["expected_origin_main_sha"] = "e" * 40
    with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
        verify_protected_main_git_locked(repo, **bad_kwargs2)

    bad_kwargs3 = copy.deepcopy(kwargs)
    bad_kwargs3["expected_candidate_sha"] = "d" * 40
    with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
        verify_protected_main_git_locked(repo, **bad_kwargs3)


def test_all_six_invalid_binding_dimensions_zero_io(tmp_path: Path) -> None:
    repo, kwargs, _ = _init_fixture_repo(tmp_path)
    keys = list(kwargs.keys())
    assert len(keys) == 6

    class MyStr(str):
        pass

    invalid_values = [
        MyStr("a" * 40),
        ["a" * 40],
        {"sha": "a" * 40},
        b"a" * 40,
        True,
        12345,
        None,
        "a" * 39,
        "a" * 41,
        "",
        "A" + "a" * 39,
        "0123456789abcdef0123456789abcdef0123456g",
    ]

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_validate_root"
    ) as mock_root, unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query"
    ) as mock_query:
        for k in keys:
            for bad_val in invalid_values:
                test_kwargs = copy.deepcopy(kwargs)
                test_kwargs[k] = copy.deepcopy(bad_val)
                test_kwargs_snapshot = copy.deepcopy(test_kwargs)

                with pytest.raises(
                    ProtectedMainGitFactsError, match=r"^invalid protected main git binding$"
                ) as exc_info:
                    verify_protected_main_git_locked(repo, **test_kwargs)
                assert exc_info.value.__cause__ is None
                assert exc_info.value.__suppress_context__ is True
                assert test_kwargs == test_kwargs_snapshot

                with pytest.raises(
                    ProtectedMainGitFactsError, match=r"^invalid protected main git binding$"
                ) as exc_info2:
                    verify_protected_main_git_locked(
                        Path("/nonexistent/invalid/root"), **test_kwargs
                    )
                assert exc_info2.value.__cause__ is None
                assert exc_info2.value.__suppress_context__ is True

        mock_root.assert_not_called()
        mock_query.assert_not_called()


def test_exact_byte_malformed_rows(tmp_path: Path) -> None:
    repo, kwargs, _ = _init_fixture_repo(tmp_path)
    orig_run = scripts.two_site_protected_main_git_facts._run_git_query

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", return_value="commit\n"
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
            verify_protected_main_git_locked(repo, **kwargs)

    def run_missing_lf(root: Path, argv: Any, **kw: Any) -> bytes:
        res = orig_run(root, argv, **kw)
        if argv == ["symbolic-ref", "-q", "HEAD"]:
            return b"refs/heads/refactor/two-site-architecture"
        return res

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=run_missing_lf
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
            verify_protected_main_git_locked(repo, **kwargs)

    def run_crlf(root: Path, argv: Any, **kw: Any) -> bytes:
        res = orig_run(root, argv, **kw)
        if argv == ["symbolic-ref", "-q", "HEAD"]:
            return b"refs/heads/refactor/two-site-architecture\r\n"
        return res

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=run_crlf
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
            verify_protected_main_git_locked(repo, **kwargs)

    def run_extra_lines(root: Path, argv: Any, **kw: Any) -> bytes:
        res = orig_run(root, argv, **kw)
        if argv[0] == "cat-file":
            return b"commit\nextra\n"
        return res

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=run_extra_lines
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
            verify_protected_main_git_locked(repo, **kwargs)

    def run_nonempty_ancestor(root: Path, argv: Any, **kw: Any) -> bytes:
        res = orig_run(root, argv, **kw)
        if argv[:2] == ["merge-base", "--is-ancestor"]:
            return b"ancestor-found\n"
        return res

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=run_nonempty_ancestor
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
            verify_protected_main_git_locked(repo, **kwargs)

    def run_tree_type(root: Path, argv: Any, **kw: Any) -> bytes:
        res = orig_run(root, argv, **kw)
        if argv[0] == "cat-file":
            return b"tree\n"
        return res

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=run_tree_type
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$"):
            verify_protected_main_git_locked(repo, **kwargs)


def test_oserror_sandboxerror_wrapping_and_base_exceptions(tmp_path: Path) -> None:
    repo, kwargs, _ = _init_fixture_repo(tmp_path)

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_validate_root", side_effect=OSError("disk error")
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$") as exc_info:
            verify_protected_main_git_locked(repo, **kwargs)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts,
        "_run_git_query",
        side_effect=SandboxError("sandbox timeout"),
    ):
        with pytest.raises(ProtectedMainGitFactsError, match=r"^invalid protected main git facts$") as exc_info:
            verify_protected_main_git_locked(repo, **kwargs)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__suppress_context__ is True

    ki = KeyboardInterrupt("interrupt")
    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=ki
    ):
        with pytest.raises(KeyboardInterrupt) as exc_ki:
            verify_protected_main_git_locked(repo, **kwargs)
        assert exc_ki.value is ki

    se = SystemExit(42)
    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_run_git_query", side_effect=se
    ):
        with pytest.raises(SystemExit) as exc_se:
            verify_protected_main_git_locked(repo, **kwargs)
        assert exc_se.value is se


def test_observe_root_helper_once(tmp_path: Path) -> None:
    repo, kwargs, expected_output = _init_fixture_repo(tmp_path)
    orig_validate = scripts.two_site_protected_main_git_facts._validate_root
    root_calls: list[Path] = []

    def spied_validate(r: Path) -> Path:
        root_calls.append(r)
        return orig_validate(r)

    with unittest.mock.patch.object(
        scripts.two_site_protected_main_git_facts, "_validate_root", side_effect=spied_validate
    ):
        res = verify_protected_main_git_locked(repo, **kwargs)
    assert res == expected_output
    assert len(root_calls) == 1
    assert root_calls[0] == repo


def test_return_mutation_detachment(tmp_path: Path) -> None:
    repo, kwargs, expected_output = _init_fixture_repo(tmp_path)
    frozen_expected = copy.deepcopy(expected_output)
    frozen_kwargs = copy.deepcopy(kwargs)

    res1 = verify_protected_main_git_locked(repo, **kwargs)
    assert res1 == frozen_expected
    assert kwargs == frozen_kwargs

    res1["status"] = "tampered"
    res1["head"] = "0" * 40
    res1["extra_key"] = "extra"
    assert res1 != frozen_expected
    assert expected_output == frozen_expected
    assert kwargs == frozen_kwargs

    res2 = verify_protected_main_git_locked(repo, **kwargs)
    assert res2 == frozen_expected
    assert res2["status"] == ""
    assert "extra_key" not in res2
    assert kwargs == frozen_kwargs
