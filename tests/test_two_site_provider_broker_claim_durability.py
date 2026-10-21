from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import two_site_provider_broker as broker
from tests.test_two_site_provider_broker import broker_repo, make_valid_request


def _create_isolated_queue_state(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    state_root = tmp_path / ".local/two-site-refactor/provider-broker"
    requests = state_root / "requests"
    claimed = state_root / "claimed"
    responses = state_root / "responses"
    for directory in (state_root, requests, claimed, responses):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory.chmod(0o700)
    return requests, claimed, responses, state_root / "terminal-quota"


def _publish_test_request(requests: Path, request_id: str, content: dict | None = None) -> Path:
    target = requests / f"{request_id}.json"
    payload = content if content is not None else {"request_id": request_id, "dummy": True}
    broker._atomic_new_json(target, payload, broker.MAX_REQUEST_BYTES)
    return target


def test_claim_next_success_verifies_fsync_order_and_inode_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests, claimed, responses, terminal_quota = _create_isolated_queue_state(tmp_path)
    req_id = "a" * 32
    source = _publish_test_request(requests, req_id)
    source_stat = os.lstat(source)

    fsynced_dirs: list[Path] = []
    orig_fsync = broker.fsync_private_record_directory

    def spy_fsync(path: Path) -> None:
        fsynced_dirs.append(path)
        orig_fsync(path)

    monkeypatch.setattr(broker, "fsync_private_record_directory", spy_fsync)

    claimed_path = broker._claim_next(requests, claimed, responses, terminal_quota)

    assert claimed_path == claimed / f"{req_id}.json"
    assert fsynced_dirs == [claimed, requests]
    assert not os.path.lexists(source)

    dest_stat = os.lstat(claimed_path)
    assert (
        dest_stat.st_dev, dest_stat.st_ino, dest_stat.st_size
    ) == (
        source_stat.st_dev, source_stat.st_ino, source_stat.st_size
    )
    assert stat.S_ISREG(dest_stat.st_mode)
    assert not stat.S_ISLNK(dest_stat.st_mode)
    assert stat.S_IMODE(dest_stat.st_mode) == 0o600
    assert dest_stat.st_nlink == 1
    assert dest_stat.st_uid == os.geteuid()


@pytest.mark.parametrize("fault_target", ["claimed", "requests"])
def test_serve_fault_each_fsync_prevents_executor(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    fault_target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]

    _state_root, _status, requests, claimed, responses = broker.broker_paths(root)
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    target_dir = claimed if fault_target == "claimed" else requests
    orig_fsync = broker.fsync_private_record_directory

    def failing_fsync(path: Path) -> None:
        if path == target_dir:
            raise broker.BrokerError(f"injected {fault_target} fsync failure")
        orig_fsync(path)

    monkeypatch.setattr(broker, "fsync_private_record_directory", failing_fsync)
    executor = mock.Mock()

    with pytest.raises(broker.BrokerError, match=f"injected {fault_target} fsync failure"):
        broker.serve(
            root=root,
            once=True,
            executor=executor,
            scope_loader=lambda _r: scope,
            install_signal_handlers=False,
        )

    executor.assert_not_called()
    assert not (responses / f"{req_id}.json").exists()
    assert (claimed / f"{req_id}.json").exists()


def test_claim_next_verification_failure_dev_ino_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests, claimed, responses, terminal_quota = _create_isolated_queue_state(tmp_path)
    req_id = "b" * 32
    _publish_test_request(requests, req_id)

    orig_lstat = os.lstat

    def tampered_lstat(path, *args, **kwargs):
        st = orig_lstat(path, *args, **kwargs)
        if Path(path) == claimed / f"{req_id}.json":
            return os.stat_result((
                st.st_mode,
                st.st_ino + 1000,
                st.st_dev,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                st.st_size,
                int(st.st_atime),
                int(st.st_mtime),
                int(st.st_ctime),
            ))
        return st

    monkeypatch.setattr(os, "lstat", tampered_lstat)

    with pytest.raises(broker.BrokerError, match="claimed request file verification failed"):
        broker._claim_next(requests, claimed, responses, terminal_quota)


def test_duplicate_id_in_claimed_responses_terminal_receipt_blocks_admission(
    tmp_path: Path
) -> None:
    requests, claimed, responses, terminal_quota = _create_isolated_queue_state(tmp_path)
    req_id = "c" * 32
    source = _publish_test_request(requests, req_id)

    claimed_dup = claimed / f"{req_id}.json"
    claimed_dup.write_text("{}", encoding="utf-8")
    claimed_dup.chmod(0o600)
    with pytest.raises(broker.BrokerError, match="broker claimed request already exists"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()
    claimed_dup.unlink()

    resp_dup = responses / f"{req_id}.json"
    resp_dup.write_text("{}", encoding="utf-8")
    resp_dup.chmod(0o600)
    with pytest.raises(broker.BrokerError, match="broker response already exists for request"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()
    resp_dup.unlink()

    terminal_quota.mkdir(mode=0o700)
    terminal_quota.chmod(0o700)
    tq_dup = terminal_quota / f"{req_id}.json"
    tq_dup.write_text("{}", encoding="utf-8")
    tq_dup.chmod(0o600)
    with pytest.raises(broker.BrokerError, match="terminal quota record already exists for request"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()


def test_unsafe_terminal_directory_blocks_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests, claimed, responses, terminal_quota = _create_isolated_queue_state(tmp_path)
    req_id = "d" * 32
    source = _publish_test_request(requests, req_id)

    target_dir = tmp_path / "real_terminal_dir"
    target_dir.mkdir(mode=0o700)
    target_dir.chmod(0o700)
    terminal_quota.symlink_to(target_dir)
    with pytest.raises(broker.BrokerError, match="terminal quota directory cannot be a symlink"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()
    terminal_quota.unlink()

    terminal_quota.write_text("not-a-directory", encoding="utf-8")
    terminal_quota.chmod(0o700)
    with pytest.raises(broker.BrokerError, match="terminal quota directory must be a directory"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()
    terminal_quota.unlink()

    terminal_quota.mkdir(mode=0o755)
    terminal_quota.chmod(0o755)
    with pytest.raises(broker.BrokerError, match="terminal quota directory mode must be 0700"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()
    terminal_quota.chmod(0o700)
    terminal_quota.rmdir()

    terminal_quota.mkdir(mode=0o700)
    terminal_quota.chmod(0o700)
    orig_lstat = os.lstat

    def owner_mismatch_lstat(path, *args, **kwargs):
        st = orig_lstat(path, *args, **kwargs)
        if Path(path) == terminal_quota:
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                st.st_uid + 9999,
                st.st_gid,
                st.st_size,
                int(st.st_atime),
                int(st.st_mtime),
                int(st.st_ctime),
            ))
        return st

    monkeypatch.setattr(os, "lstat", owner_mismatch_lstat)
    with pytest.raises(broker.BrokerError, match="terminal quota directory owner mismatch"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    assert source.exists()


def test_source_queue_file_safety_rejects(tmp_path: Path) -> None:
    requests, claimed, responses, terminal_quota = _create_isolated_queue_state(tmp_path)

    req_symlink = "e" * 32
    external_file = tmp_path / "external.json"
    external_file.write_text("{}", encoding="utf-8")
    external_file.chmod(0o600)
    (requests / f"{req_symlink}.json").symlink_to(external_file)
    with pytest.raises(broker.BrokerError, match="is not a trusted queue file"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    (requests / f"{req_symlink}.json").unlink()

    req_bad_mode = "f" * 32
    bad_mode_file = requests / f"{req_bad_mode}.json"
    bad_mode_file.write_text("{}", encoding="utf-8")
    bad_mode_file.chmod(0o644)
    with pytest.raises(broker.BrokerError, match="is not a trusted queue file"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    bad_mode_file.unlink()

    req_hardlink = "1" * 32
    hardlink_source = requests / f"{req_hardlink}.json"
    hardlink_source.write_text("{}", encoding="utf-8")
    hardlink_source.chmod(0o600)
    extra_link1 = tmp_path / "extra1.tmp"
    extra_link2 = tmp_path / "extra2.tmp"
    os.link(hardlink_source, extra_link1)
    os.link(hardlink_source, extra_link2)
    with pytest.raises(broker.BrokerError, match="has an invalid hardlink count"):
        broker._claim_next(requests, claimed, responses, terminal_quota)
    extra_link2.unlink()

    assert broker._claim_next(requests, claimed, responses, terminal_quota) is None
    extra_link1.unlink()
    hardlink_source.unlink()


def test_terminal_quota_directory_never_created_by_claim_next(tmp_path: Path) -> None:
    requests, claimed, responses, terminal_quota = _create_isolated_queue_state(tmp_path)
    req_id = "2" * 32
    _publish_test_request(requests, req_id)

    assert not os.path.lexists(terminal_quota)
    claimed_path = broker._claim_next(requests, claimed, responses, terminal_quota)
    assert claimed_path == claimed / f"{req_id}.json"
    assert not os.path.lexists(terminal_quota)
