import io
import json
os = __import__("os")
from pathlib import Path
from unittest.mock import patch
import pytest

for _key in list(os.environ):
    if _key.startswith("TWO_SITE_ASSIGNMENT_"):
        del os.environ[_key]

from scripts.two_site_proposal_runner import (
    MAX_CONTEXT,
    ProposalError,
    _TaskBoundaryError,
    bounded_file_bytes,
    load_task,
)


@pytest.fixture(autouse=True)
def clear_assignment_env():
    for k in list(os.environ):
        if k.startswith("TWO_SITE_ASSIGNMENT_"):
            del os.environ[k]
    yield
    for k in list(os.environ):
        if k.startswith("TWO_SITE_ASSIGNMENT_"):
            del os.environ[k]


def _cb(code: str, path: str | None = None) -> _TaskBoundaryError:
    return _TaskBoundaryError(code, path)


def test_callback_mode_unreadable_open(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "data.txt"
    target.write_text("content")

    with patch("os.open", side_effect=PermissionError("denied")):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            bounded_file_bytes(root, Path("data.txt"), 100, "test", failure_callback=_cb)
        assert exc_info.value.code == "TASK_FILE_UNREADABLE"
        assert exc_info.value.path == "data.txt"

    with patch("os.open", side_effect=PermissionError("denied")):
        with pytest.raises(ProposalError, match="cannot safely open test"):
            bounded_file_bytes(root, Path("data.txt"), 100, "test", failure_callback=None)


def test_callback_mode_open_missing_preserved(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "missing.txt"
    target.write_text("temp")

    with patch("os.open", side_effect=FileNotFoundError("gone")):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            bounded_file_bytes(root, Path("missing.txt"), 100, "test", failure_callback=_cb)
        assert exc_info.value.code == "TASK_FILE_NOT_FOUND"
        assert exc_info.value.path == "missing.txt"


def test_callback_mode_unreadable_read_and_direct_default(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "data.txt"
    target.write_text("content")

    class FailingReader(io.BytesIO):
        def read(self, *args, **kwargs):
            raise OSError(5, "Input/output error")

    def mock_fdopen(fd, *args, **kwargs):
        return FailingReader()

    with patch("os.fdopen", side_effect=mock_fdopen):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            bounded_file_bytes(root, Path("data.txt"), 100, "test", failure_callback=_cb)
        assert exc_info.value.code == "TASK_FILE_UNREADABLE"
        assert exc_info.value.path == "data.txt"

    with patch("os.fdopen", side_effect=mock_fdopen):
        with pytest.raises(OSError) as exc_info:
            bounded_file_bytes(root, Path("data.txt"), 100, "test", failure_callback=None)
        assert not isinstance(exc_info.value, ProposalError)


def test_dynamic_over_limit_callback_and_direct_default(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "grow.txt"
    target.write_text("12345")

    def mock_fdopen(fd, *args, **kwargs):
        return io.BytesIO(b"123456789012345")

    with patch("os.fdopen", side_effect=mock_fdopen):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            bounded_file_bytes(root, Path("grow.txt"), 10, "test", failure_callback=_cb)
        assert exc_info.value.code == "TASK_BUDGET_EXCEEDED"
        assert exc_info.value.path == "grow.txt"

    with patch("os.fdopen", side_effect=mock_fdopen):
        with pytest.raises(ProposalError, match="exceeds the safe context budget"):
            bounded_file_bytes(root, Path("grow.txt"), 10, "test", failure_callback=None)


def test_post_read_fingerprint_mismatch_and_direct_default(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "racy.txt"
    target.write_text("steady")

    original_stat = os.stat

    def mock_stat(path, *args, **kwargs):
        st = original_stat(path, *args, **kwargs)
        return os.stat_result((
            st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
            st.st_uid, st.st_gid, st.st_size + 1, st.st_atime,
            st.st_mtime + 1, st.st_ctime + 1,
        ))

    with patch("os.stat", side_effect=mock_stat):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            bounded_file_bytes(root, Path("racy.txt"), 100, "test", failure_callback=_cb)
        assert exc_info.value.code == "TASK_FILE_CHANGED_DURING_READ"
        assert exc_info.value.path == "racy.txt"

    with patch("os.stat", side_effect=mock_stat):
        with pytest.raises(ProposalError, match="changed while it was being read"):
            bounded_file_bytes(root, Path("racy.txt"), 100, "test", failure_callback=None)


def test_post_read_stat_oserror_callback_and_direct_default(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "racy.txt"
    target.write_text("steady")

    with patch("os.stat", side_effect=OSError("removed during read")):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            bounded_file_bytes(root, Path("racy.txt"), 100, "test", failure_callback=_cb)
        assert exc_info.value.code == "TASK_FILE_CHANGED_DURING_READ"
        assert exc_info.value.path == "racy.txt"

    with patch("os.stat", side_effect=OSError("removed during read")):
        with pytest.raises(ProposalError, match="changed while it was being read"):
            bounded_file_bytes(root, Path("racy.txt"), 100, "test", failure_callback=None)


def test_boundary_error_code_and_facts() -> None:
    err1 = _TaskBoundaryError("TASK_FILE_UNREADABLE", "rel/path.py")
    assert err1.code == "TASK_FILE_UNREADABLE"
    assert err1.path == "rel/path.py"
    assert err1.to_fact() == {"code": "TASK_FILE_UNREADABLE", "path": "rel/path.py"}
    assert "is unreadable" in str(err1)

    err2 = _TaskBoundaryError("TASK_FILE_CHANGED_DURING_READ", "rel/path.py")
    assert err2.code == "TASK_FILE_CHANGED_DURING_READ"
    assert err2.path == "rel/path.py"
    assert err2.to_fact() == {"code": "TASK_FILE_CHANGED_DURING_READ", "path": "rel/path.py"}
    assert "changed while it was being read" in str(err2)


def test_load_task_unreadable_context_file(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    task_file = root / "task.json"
    task_file.write_text(
        '{"task_id": "T1", "goal": "G", "context_files": ["ctx.py"], "allowed_files": ["ctx.py"], "requirements": ["R1"], "test_selectors": ["T1"]}'
    )
    ctx_file = root / "ctx.py"
    ctx_file.write_text("print(1)")

    original_open = os.open

    def mock_open(path, flags, *args, **kwargs):
        if str(path).endswith("ctx.py"):
            raise PermissionError("unreadable")
        return original_open(path, flags, *args, **kwargs)

    with patch("os.open", side_effect=mock_open):
        with pytest.raises(_TaskBoundaryError) as exc_info:
            load_task(root, Path("task.json"))
        assert exc_info.value.code == "TASK_FILE_UNREADABLE"
        assert exc_info.value.path == "ctx.py"


def test_load_task_aggregate_budget_boundary(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    task = {
        "task_id": "T1",
        "goal": "G",
        "context_files": ["ctx.py"],
        "allowed_files": ["ctx.py"],
        "requirements": ["R1"],
        "test_selectors": ["T1"],
    }
    task_file = root / "task.json"
    task_file.write_text(json.dumps(task))
    ctx_file = root / "ctx.py"
    ctx_file.write_text("")

    loaded_task, loaded_sources = load_task(root, Path("task.json"))
    base_len = len(json.dumps({"task": loaded_task, "sources": loaded_sources}).encode("utf-8"))
    assert base_len < MAX_CONTEXT

    pad = MAX_CONTEXT - base_len
    ctx_file.write_text("a" * pad)
    accepted_task, accepted_sources = load_task(root, Path("task.json"))
    accepted_len = len(json.dumps({"task": accepted_task, "sources": accepted_sources}).encode("utf-8"))
    assert accepted_len == MAX_CONTEXT
    assert accepted_task["task_id"] == "T1"

    ctx_file.write_text("a" * (pad + 1))
    with pytest.raises(_TaskBoundaryError) as exc_info:
        load_task(root, Path("task.json"))
    assert exc_info.value.code == "TASK_BUDGET_EXCEEDED"
    assert exc_info.value.path is None
    assert exc_info.value.to_fact() == {"code": "TASK_BUDGET_EXCEEDED", "path": None}
