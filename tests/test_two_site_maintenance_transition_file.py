"""Tests for private maintenance-transition record file operations under AE03."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from unittest import mock

import pytest

try:
    from scripts.two_site_maintenance_transition_file import (
        MaintenanceTransitionAmbiguousError,
        MaintenanceTransitionDuplicateError,
        MaintenanceTransitionFileError,
        publish_maintenance_transition_bytes_noreplace,
        read_maintenance_transition_bytes,
    )
    from scripts.two_site_maintenance_transition_record import (
        PROTECTED_REFS,
        build_maintenance_transition_record,
        serialize_maintenance_transition_record,
    )
except ImportError:
    from two_site_maintenance_transition_file import (  # type: ignore[no-redef]
        MaintenanceTransitionAmbiguousError,
        MaintenanceTransitionDuplicateError,
        MaintenanceTransitionFileError,
        publish_maintenance_transition_bytes_noreplace,
        read_maintenance_transition_bytes,
    )
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        PROTECTED_REFS,
        build_maintenance_transition_record,
        serialize_maintenance_transition_record,
    )

VALID_LEAF = f"rescope-{'a' * 64}.json"


def _make_valid_record_bytes() -> bytes:
    prior_head = "1" * 40
    new_head = "2" * 40
    new_repo = {
        "head": new_head,
        "status": "",
        "protected_refs": {ref: "3" * 40 for ref in sorted(PROTECTED_REFS)},
        "control_blobs": {"scripts/two_site_codex_autopilot.py": "4" * 40},
        "assignment_scope": {
            "assignment_id": "aid-1",
            "stage_id": "stage-1",
            "sha256": "5" * 64,
            "expires_at": "2026-10-01T00:00:00Z",
            "allowed_paths": ["scripts/two_site_proposal_runner.py"],
            "forbidden_paths": [],
        },
    }
    commits = [
        {
            "head": new_head,
            "parent": prior_head,
            "paths": ["scripts/two_site_proposal_runner.py"],
            "proposal_sha256": "6" * 64,
            "test_evidence_sha256": "7" * 64,
            "worker_run_id": "worker-1",
            "reviewer_run_id": "reviewer-1",
        }
    ]
    rec = build_maintenance_transition_record(
        prior_checkpoint_sha256="0" * 64,
        prior_head=prior_head,
        new_repository=new_repo,
        maintenance_commits=commits,
    )
    return serialize_maintenance_transition_record(rec)


@pytest.fixture
def transitions_dir(tmp_path: Path):
    d = tmp_path / "maintenance-transitions"
    d.mkdir(mode=0o700)
    os.chmod(str(d), 0o700)
    fd = os.open(str(d), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        yield fd, d
    finally:
        os.close(fd)


def test_normal_publish_and_read(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()

    # None on reading missing leaf
    assert read_maintenance_transition_bytes(fd, VALID_LEAF) is None

    publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    read_back = read_maintenance_transition_bytes(fd, VALID_LEAF)
    assert read_back == raw

    leaf_path = d / VALID_LEAF
    assert leaf_path.exists()
    st = leaf_path.stat()
    assert (st.st_mode & 0o777) == 0o600
    assert st.st_nlink == 1
    assert st.st_size == len(raw)


def test_duplicate_publication_error(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    with pytest.raises(MaintenanceTransitionDuplicateError):
        publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)


def test_prelink_write_failure_with_replacement_inode(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    orig_write = os.write
    replacement_content = b"unrelated-replacement-content"
    hook_called = False

    def hook_write(file_desc, data):
        nonlocal hook_called
        if not hook_called:
            hook_called = True
            temp_entries = [p for p in d.iterdir() if p.name.startswith(".tmp-")]
            assert len(temp_entries) == 1
            temp_path = temp_entries[0]
            temp_path.unlink()
            temp_path.write_bytes(replacement_content)
            temp_path.chmod(0o600)
            raise OSError(errno.EIO, "simulated write failure")
        return orig_write(file_desc, data)

    with mock.patch("os.write", side_effect=hook_write):
        with pytest.raises(MaintenanceTransitionAmbiguousError):
            publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    assert hook_called
    temp_entries = [p for p in d.iterdir() if p.name.startswith(".tmp-")]
    assert len(temp_entries) == 1
    assert temp_entries[0].read_bytes() == replacement_content
    assert not (d / VALID_LEAF).exists()


def test_postlink_temp_disappears_before_cleanup(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    orig_link = os.link
    hook_called = False

    def hook_link(src, dst, **kwargs):
        nonlocal hook_called
        hook_called = True
        orig_link(src, dst, **kwargs)
        os.unlink(src, dir_fd=kwargs.get("src_dir_fd"))

    with mock.patch("os.link", side_effect=hook_link):
        with pytest.raises(MaintenanceTransitionAmbiguousError):
            publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    assert hook_called
    leaf_path = d / VALID_LEAF
    assert leaf_path.exists()
    assert leaf_path.read_bytes() == raw
    temp_entries = [p for p in d.iterdir() if p.name.startswith(".tmp-")]
    assert len(temp_entries) == 0


def test_prelink_write_failure_normal_cleanup(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    orig_write = os.write
    hook_called = False

    def hook_write(file_desc, data):
        nonlocal hook_called
        hook_called = True
        raise OSError(errno.EIO, "disk full")

    with mock.patch("os.write", side_effect=hook_write):
        with pytest.raises(MaintenanceTransitionFileError):
            publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    assert hook_called
    temp_entries = [p for p in d.iterdir() if p.name.startswith(".tmp-")]
    assert len(temp_entries) == 0
    assert not (d / VALID_LEAF).exists()


def test_link_eexist_different_inode_cleans_temp(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    leaf_path = d / VALID_LEAF
    leaf_path.write_bytes(b"existing-content")
    leaf_path.chmod(0o600)

    orig_stat = os.stat
    first_call = True

    def hook_stat(path, *args, **kwargs):
        nonlocal first_call
        if path == VALID_LEAF and kwargs.get("dir_fd") == fd and first_call:
            first_call = False
            raise FileNotFoundError()
        return orig_stat(path, *args, **kwargs)

    with mock.patch("os.stat", side_effect=hook_stat):
        with pytest.raises(MaintenanceTransitionDuplicateError):
            publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    assert leaf_path.read_bytes() == b"existing-content"
    temp_entries = [p for p in d.iterdir() if p.name.startswith(".tmp-")]
    assert len(temp_entries) == 0


def test_link_eexist_same_inode_raises_ambiguous(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    orig_link = os.link
    hook_called = False

    def hook_link(src, dst, **kwargs):
        nonlocal hook_called
        hook_called = True
        orig_link(src, dst, **kwargs)
        raise FileExistsError(errno.EEXIST, "already exists")

    with mock.patch("os.link", side_effect=hook_link):
        with pytest.raises(MaintenanceTransitionAmbiguousError):
            publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    assert hook_called
    assert (d / VALID_LEAF).exists()


def test_link_generic_error_cleans_temp(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()

    with mock.patch("os.link", side_effect=OSError(errno.EACCES, "permission denied")):
        with pytest.raises(MaintenanceTransitionFileError):
            publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    assert not (d / VALID_LEAF).exists()
    temp_entries = [p for p in d.iterdir() if p.name.startswith(".tmp-")]
    assert len(temp_entries) == 0


def test_invalid_parameters_and_metadata(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()

    with pytest.raises(MaintenanceTransitionFileError):
        publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, "not bytes")  # type: ignore
    with pytest.raises(MaintenanceTransitionFileError):
        publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, b"")
    with pytest.raises(MaintenanceTransitionFileError):
        publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, b"invalid json")

    with pytest.raises(MaintenanceTransitionFileError):
        publish_maintenance_transition_bytes_noreplace(fd, "invalid-leaf.json", raw)
    with pytest.raises(MaintenanceTransitionFileError):
        read_maintenance_transition_bytes(fd, "invalid-leaf.json")

    with pytest.raises(MaintenanceTransitionFileError):
        publish_maintenance_transition_bytes_noreplace(-1, VALID_LEAF, raw)
    with pytest.raises(MaintenanceTransitionFileError):
        read_maintenance_transition_bytes(-1, VALID_LEAF)


def test_symlink_rejection(transitions_dir):
    fd, d = transitions_dir
    target = d / "target-file.json"
    target.write_bytes(b"content")
    target.chmod(0o600)
    leaf_path = d / VALID_LEAF
    leaf_path.symlink_to(target)

    with pytest.raises(MaintenanceTransitionFileError):
        read_maintenance_transition_bytes(fd, VALID_LEAF)


def test_read_stat_drift_detection(transitions_dir):
    fd, d = transitions_dir
    raw = _make_valid_record_bytes()
    publish_maintenance_transition_bytes_noreplace(fd, VALID_LEAF, raw)

    orig_open = os.open

    def hook_open(path, flags, *args, **kwargs):
        res = orig_open(path, flags, *args, **kwargs)
        if path == VALID_LEAF:
            leaf_path = d / VALID_LEAF
            st = leaf_path.stat()
            os.utime(str(leaf_path), (st.st_atime, st.st_mtime + 10))
        return res

    with mock.patch("os.open", side_effect=hook_open):
        with pytest.raises(MaintenanceTransitionFileError):
            read_maintenance_transition_bytes(fd, VALID_LEAF)
