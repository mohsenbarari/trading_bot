from __future__ import annotations

import copy
import hashlib
import pytest

try:
    from scripts.two_site_proposal_runner import ProposalError, normalize_result
except ImportError:
    from two_site_proposal_runner import ProposalError, normalize_result


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_fixture():
    small_content = "def small_fn():\n    return 42\n"
    large_content = "# header\n" + ("line = 1\n" * 1200) + "# marker\n"
    assert len(large_content.encode("utf-8")) >= 8192

    task = {
        "task_id": "test-strict-transport-task",
        "goal": "test strict transport handling",
        "context_files": ["pkg/small.py", "pkg/large.py"],
        "allowed_files": ["pkg/new.py", "pkg/small.py", "pkg/large.py"],
        "requirements": ["req1"],
        "test_selectors": ["tests/test_two_site_strict_transport_legacy.py"],
    }
    sources = {
        "pkg/new.py": {"sha256": None, "content": None},
        "pkg/small.py": {"sha256": _sha(small_content), "content": small_content},
        "pkg/large.py": {"sha256": _sha(large_content), "content": large_content},
    }
    return task, sources, small_content, large_content


@pytest.mark.parametrize(
    ("path_key", "orig_sha", "new_content"),
    [
        ("pkg/new.py", None, "created = True\n"),
        ("pkg/large.py", "large_sha", "updated = True\n"),
    ],
)
def test_strict_new_and_large_legacy_rejection(path_key, orig_sha, new_content):
    task, sources, _, _ = _make_fixture()
    actual_sha = sources[path_key]["sha256"] if orig_sha == "large_sha" else orig_sha
    raw_result = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": path_key,
                "original_sha256": actual_sha,
                "content": new_content,
            }
        ],
    }

    with pytest.raises(ProposalError, match="proposal transport policy violation"):
        normalize_result(raw_result, task, sources, "worker", strict_transport=True)

    non_strict = normalize_result(
        raw_result, task, sources, "worker", strict_transport=False
    )
    assert non_strict["changes"][0]["path"] == path_key
    assert non_strict["changes"][0]["content"] == new_content


def test_strict_small_valid_legacy_accepted():
    task, sources, _, _ = _make_fixture()
    raw_result = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "pkg/small.py",
                "original_sha256": sources["pkg/small.py"]["sha256"],
                "content": "def small_fn():\n    return 84\n",
            }
        ],
    }
    normalized = normalize_result(
        raw_result, task, sources, "worker", strict_transport=True
    )
    assert len(normalized["changes"]) == 1
    assert normalized["changes"][0]["path"] == "pkg/small.py"
    assert normalized["changes"][0]["content"] == "def small_fn():\n    return 84\n"


def test_strict_four_key_accepted():
    task, sources, _, _ = _make_fixture()
    raw_result = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "pkg/new.py",
                "original_sha256": None,
                "content": "val = 10\n",
                "replacements": [],
            },
            {
                "path": "pkg/large.py",
                "original_sha256": sources["pkg/large.py"]["sha256"],
                "content": None,
                "replacements": [{"old": "# marker\n", "new": "# replaced\n"}],
            },
        ],
    }
    normalized = normalize_result(
        raw_result, task, sources, "worker", strict_transport=True
    )
    assert len(normalized["changes"]) == 2
    by_path = {c["path"]: c for c in normalized["changes"]}
    assert by_path["pkg/new.py"]["content"] == "val = 10\n"
    assert "# replaced\n" in by_path["pkg/large.py"]["content"]
    assert "# marker\n" not in by_path["pkg/large.py"]["content"]
    assert set(by_path["pkg/new.py"].keys()) == {"path", "original_sha256", "content"}
    assert set(by_path["pkg/large.py"].keys()) == {"path", "original_sha256", "content"}


def test_inputs_remain_unmutated():
    task, sources, _, _ = _make_fixture()
    task_copy = copy.deepcopy(task)
    sources_copy = copy.deepcopy(sources)
    raw_result = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "pkg/small.py",
                "original_sha256": sources["pkg/small.py"]["sha256"],
                "content": "changed = 1\n",
            }
        ],
    }
    raw_result_copy = copy.deepcopy(raw_result)

    normalize_result(raw_result, task, sources, "worker", strict_transport=True)

    assert task == task_copy
    assert sources == sources_copy
    assert raw_result == raw_result_copy


def test_bad_sha_and_scope_rejection():
    task, sources, _, _ = _make_fixture()
    bad_sha_result = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "pkg/small.py",
                "original_sha256": _sha("mismatch"),
                "content": "x = 1\n",
            }
        ],
    }
    with pytest.raises(ProposalError, match="proposal is based on different source bytes"):
        normalize_result(bad_sha_result, task, sources, "worker", strict_transport=True)

    out_of_scope_result = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "pkg/outside.py",
                "original_sha256": None,
                "content": "x = 1\n",
            }
        ],
    }
    with pytest.raises(
        ProposalError, match="changed path is duplicated or outside assignment"
    ):
        normalize_result(out_of_scope_result, task, sources, "worker", strict_transport=True)
