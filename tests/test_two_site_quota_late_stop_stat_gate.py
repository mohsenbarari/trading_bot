"""Late continuation STOP inspection must fail closed, retaining real custody.

Hermetic synthetic quota evidence only; no live wake or replay authority.
"""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from datetime import datetime, timedelta, timezone
import errno
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts import two_site_provider_broker as broker
from scripts import two_site_quota_broker_route as route
from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_resume_submission_intent import publish_quota_resume_submission_intent
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import _setup_w2_admission_claim_scenario


def _sentinel(root: Path) -> Path:
    return root / ".local/two-site-refactor/autopilot/STOP"


def test_absent_stop_allows_without_lexists_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "_STOP", False)
    with patch.object(route.os.path, "lexists", side_effect=AssertionError("unsafe fallback")) as fallback:
        assert route._check_pre_dispatch_stop(tmp_path) is None
    fallback.assert_not_called()
    assert not _sentinel(tmp_path).exists()


@pytest.mark.parametrize("kind", ["file", "directory", "broken_symlink", "fifo"])
def test_any_present_stop_blocks_without_reading(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(broker, "_STOP", False)
    sentinel = _sentinel(tmp_path)
    sentinel.parent.mkdir(parents=True, mode=0o700)
    if kind == "file":
        sentinel.write_bytes(b"STOP\n")
        sentinel.chmod(0)
    elif kind == "directory":
        sentinel.mkdir()
    elif kind == "broken_symlink":
        sentinel.symlink_to(sentinel.parent / "missing")
    else:
        os.mkfifo(sentinel, 0o600)
    before = sentinel.lstat()
    with pytest.raises(broker.BrokerError, match="^stop requested before continuation dispatch$"):
        route._check_pre_dispatch_stop(tmp_path)
    after = sentinel.lstat()
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_nlink) == (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink
    )


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.ELOOP, errno.ENOTDIR, errno.EBADF])
def test_non_absence_stat_errors_fail_closed_with_cause(tmp_path, monkeypatch, code):
    monkeypatch.setattr(broker, "_STOP", False)
    failure = OSError(code, "synthetic inspection failure")
    with patch.object(Path, "lstat", side_effect=failure) as probe:
        with pytest.raises(broker.BrokerError, match="^cannot inspect continuation STOP sentinel$") as raised:
            route._check_pre_dispatch_stop(tmp_path)
    assert raised.value.__cause__ is failure
    probe.assert_called_once_with()


@pytest.mark.parametrize("flag", [True, None, 0, 1, "false"])
def test_invalid_or_requested_signal_blocks_before_filesystem(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(broker, "_STOP", flag)
    with patch.object(Path, "lstat", side_effect=AssertionError("unexpected inspection")) as probe:
        with pytest.raises(broker.BrokerError, match="stop requested before continuation dispatch"):
            route._check_pre_dispatch_stop(tmp_path)
    probe.assert_not_called()


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_propagate_without_reclassification(tmp_path, monkeypatch, failure_type):
    monkeypatch.setattr(broker, "_STOP", False)
    failure = failure_type()
    with patch.object(Path, "lstat", side_effect=failure):
        with pytest.raises(failure_type) as raised:
            route._check_pre_dispatch_stop(tmp_path)
    assert raised.value is failure


@pytest.mark.parametrize("fault", ["stat_error", "late_stop"])
def test_late_stop_after_real_admission_and_intent_retains_custody(broker_repo, monkeypatch, fault):
    monkeypatch.setattr(broker, "_STOP", False)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo, datetime.now(timezone.utc) - timedelta(minutes=2)
    )
    root, request, run_dir = ctx["root"], ctx["resume_request"], ctx["runs_dir"]
    with exclusive_supervisor_lock(ctx["sc"]["lock_path"]):
        publish_quota_resume_submission_intent(root, request, now_epoch=ctx["now_epoch"])
    intent = run_dir / "quota-resume-submission.json"
    tracked = [ctx["task"], ctx["wait_file"], ctx["claim_file"], intent]
    custody = {path: (path.read_bytes(), path.stat().st_ino) for path in tracked}
    run_names = sorted(os.listdir(run_dir))
    proposals = root / ".local/two-site-refactor/proposals"
    proposal_names = sorted(os.listdir(proposals))
    _, _, requests, claimed, responses = broker.broker_paths(root)
    queues = {path: sorted(os.listdir(path)) for path in (requests, claimed, responses)}
    real_reader = route.read_quota_resume_submission_intent
    real_lstat = Path.lstat
    sentinel = _sentinel(root)
    late_failure = OSError(errno.EIO, "synthetic late STOP inspection failure")

    def inspect(path, *args, **kwargs):
        if path == sentinel:
            raise late_failure
        return real_lstat(path, *args, **kwargs)

    def after_intent(*args, **kwargs):
        record = real_reader(*args, **kwargs)
        if fault == "stat_error":
            monkeypatch.setattr(Path, "lstat", inspect)
        else:
            sentinel.write_bytes(b"STOP\n")
        return record

    dispatch = MagicMock(side_effect=AssertionError("Provider dispatcher must not run"))
    response_builder = MagicMock(side_effect=AssertionError("no success response"))
    with patch.object(route, "read_quota_resume_submission_intent", side_effect=after_intent) as reader, \
         patch.object(route, "dispatch_quota_continuation_once", dispatch), \
         patch.object(route, "build_continuation_broker_response", response_builder):
        expected = "cannot inspect continuation STOP sentinel" if fault == "stat_error" else "stop requested before continuation dispatch"
        with pytest.raises(broker.BrokerError, match=expected) as raised:
            route.execute_intent_bound_continuation(root, request)
    reader.assert_called_once_with(root, request['original_controller_run_id'], request)
    dispatch.assert_not_called()
    response_builder.assert_not_called()
    if fault == "stat_error":
        assert raised.value.__cause__ is late_failure
    for path, (contents, inode) in custody.items():
        assert path.read_bytes() == contents
        assert path.stat().st_ino == inode
    assert sorted(os.listdir(run_dir)) == run_names
    assert sorted(os.listdir(proposals)) == proposal_names
    assert {path: sorted(os.listdir(path)) for path in queues} == queues
    assert not (run_dir / "accepted-lineage.json").exists()
    assert not (run_dir / "final-ack.json").exists()
    # This proves the logical last gate, not OS-atomic STOP-vs-Popen ordering.
