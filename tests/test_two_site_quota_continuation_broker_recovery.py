"""Hermetic characterization tests for broker continuation recovery."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
import traceback
from typing import Any
from unittest import mock

import pytest

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_provider_broker import (
    BrokerError,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    _atomic_new_json,
    _claim_next,
    _recover_interrupted_claims,
    broker_paths,
    serve,
)
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_storage import (
    TERMINAL_DIR_NAME,
    TerminalContinuationStorageError,
    publish_terminal_continuation_record,
    read_terminal_continuation_record,
)
from scripts.two_site_quota_resume_submission_intent import (
    publish_quota_resume_submission_intent,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


@pytest.fixture
def spy_dispatch():
    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy:
        yield spy
    assert spy.call_count == 0


def _terminal_file(root: Path, req_id: str) -> Path:
    return (
        root
        / ".local"
        / "two-site-refactor"
        / "provider-broker"
        / TERMINAL_DIR_NAME
        / f"{req_id}.json"
    )


def _setup_recovery_test_case(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> dict[str, Any]:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root = ctx["root"]
    resume_request = ctx["resume_request"]
    now_epoch = ctx["now_epoch"]

    with exclusive_supervisor_lock(ctx["sc"]["lock_path"]):
        publish_quota_resume_submission_intent(
            root, resume_request, now_epoch=now_epoch
        )

    _, _, _, claimed_dir, responses_dir = broker_paths(root)
    req_id = resume_request["request_id"]
    claim_path = claimed_dir / f"{req_id}.json"
    _atomic_new_json(claim_path, resume_request, MAX_REQUEST_BYTES)

    response = build_continuation_broker_response(
        resume_request, {"state": "READY_FOR_CODEX"}, now_epoch=now_epoch
    )
    runs_dir = ctx["runs_dir"]
    runs_snapshot = {p: p.read_bytes() for p in runs_dir.glob("*.json")}

    return {
        "root": root,
        "resume_request": resume_request,
        "claimed_dir": claimed_dir,
        "responses_dir": responses_dir,
        "req_id": req_id,
        "claim_path": claim_path,
        "response": response,
        "response_path": responses_dir / f"{req_id}.json",
        "runs_snapshot": runs_snapshot,
    }


def _assert_runs_unchanged(data: dict[str, Any]) -> None:
    for p, content in data["runs_snapshot"].items():
        assert p.read_bytes() == content


def test_continuation_recovery_matching_receipt_and_absent_response(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    req_id = data["req_id"]
    resume_request = data["resume_request"]
    response = data["response"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]

    published = publish_terminal_continuation_record(root, resume_request, response)
    term_file = _terminal_file(root, req_id)
    assert term_file.is_file()
    receipt_bytes_before = term_file.read_bytes()
    assert not response_path.exists()
    assert claim_path.is_file()

    _recover_interrupted_claims(root, data["claimed_dir"], data["responses_dir"])

    assert not claim_path.exists()
    assert response_path.is_file()
    st = response_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert json.loads(response_path.read_text(encoding="utf-8")) == response

    canonical_written = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert response_path.read_bytes() == canonical_written
    assert term_file.read_bytes() == receipt_bytes_before

    readback = read_terminal_continuation_record(
        root, req_id, expected_request=resume_request
    )
    assert readback == published
    _assert_runs_unchanged(data)


def test_continuation_recovery_matching_receipt_and_existing_response(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    response = data["response"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]

    publish_terminal_continuation_record(root, resume_request, response)
    _atomic_new_json(response_path, response, MAX_RESPONSE_BYTES)
    resp_bytes_before = response_path.read_bytes()
    resp_stat_before = response_path.stat()
    assert claim_path.is_file()

    _recover_interrupted_claims(root, data["claimed_dir"], data["responses_dir"])

    assert not claim_path.exists()
    assert response_path.read_bytes() == resp_bytes_before
    resp_stat_after = response_path.stat()
    assert (resp_stat_after.st_ino, resp_stat_after.st_size) == (
        resp_stat_before.st_ino,
        resp_stat_before.st_size,
    )
    _assert_runs_unchanged(data)


def test_continuation_recovery_missing_receipt_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]

    assert not _terminal_file(root, data["req_id"]).exists()
    claim_bytes_before = claim_path.read_bytes()
    assert not response_path.exists()

    with pytest.raises(
        BrokerError, match="continuation terminal receipt is missing or invalid"
    ):
        _recover_interrupted_claims(root, data["claimed_dir"], data["responses_dir"])

    assert claim_path.is_file()
    assert claim_path.read_bytes() == claim_bytes_before
    assert not response_path.exists()
    _assert_runs_unchanged(data)


def test_continuation_recovery_divergent_existing_response_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    response = data["response"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]

    publish_terminal_continuation_record(root, resume_request, response)
    term_file = _terminal_file(root, data["req_id"])
    receipt_bytes_before = term_file.read_bytes()

    divergent_response = dict(response, stderr="divergent error output recorded")
    _atomic_new_json(response_path, divergent_response, MAX_RESPONSE_BYTES)
    resp_bytes_before = response_path.read_bytes()
    claim_bytes_before = claim_path.read_bytes()

    with pytest.raises(
        BrokerError, match="existing response does not match receipt response"
    ):
        _recover_interrupted_claims(root, data["claimed_dir"], data["responses_dir"])

    assert claim_path.is_file()
    assert claim_path.read_bytes() == claim_bytes_before
    assert response_path.read_bytes() == resp_bytes_before
    assert term_file.read_bytes() == receipt_bytes_before
    _assert_runs_unchanged(data)


@pytest.mark.parametrize("corrupt_mode", ["corrupt_json", "symlink"])
def test_continuation_recovery_corrupt_or_symlink_receipt_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    corrupt_mode: str,
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    response = data["response"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]

    publish_terminal_continuation_record(root, resume_request, response)
    term_file = _terminal_file(root, data["req_id"])
    assert term_file.is_file()

    if corrupt_mode == "corrupt_json":
        term_file.write_bytes(b'{"corrupt": invalid json')
        os.chmod(term_file, 0o600)
        expected_bytes: bytes | None = term_file.read_bytes()
    else:
        term_file.unlink()
        target = root / "symlink_target.json"
        target.write_text("{}", encoding="utf-8")
        term_file.symlink_to(target)
        expected_bytes = None

    claim_bytes_before = claim_path.read_bytes()
    assert not response_path.exists()

    with pytest.raises(
        BrokerError, match="continuation terminal receipt is missing or invalid"
    ):
        _recover_interrupted_claims(root, data["claimed_dir"], data["responses_dir"])

    assert claim_path.is_file()
    assert claim_path.read_bytes() == claim_bytes_before
    assert not response_path.exists()
    if corrupt_mode == "corrupt_json":
        assert term_file.read_bytes() == expected_bytes
    else:
        assert term_file.is_symlink()
    _assert_runs_unchanged(data)


def test_claim_next_duplicate_queued_request_with_terminal_receipt_blocks_claim(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    req_id = data["req_id"]
    resume_request = data["resume_request"]
    response = data["response"]
    claim_path = data["claim_path"]
    claimed_dir = data["claimed_dir"]

    _, _, requests_dir, _, responses_dir = broker_paths(root)
    publish_terminal_continuation_record(root, resume_request, response)

    queued_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, queued_path)

    queued_bytes_before = queued_path.read_bytes()
    queued_stat_before = queued_path.stat()

    term_file = _terminal_file(root, req_id)
    assert term_file.is_file()
    receipt_bytes_before = term_file.read_bytes()
    receipt_stat_before = term_file.stat()

    response_path = responses_dir / f"{req_id}.json"
    assert not response_path.exists()
    assert not claim_path.exists()

    with pytest.raises(
        BrokerError, match="terminal continuation record already exists"
    ):
        _claim_next(requests_dir, claimed_dir, responses_dir)

    assert queued_path.is_file()
    assert queued_path.read_bytes() == queued_bytes_before
    queued_stat_after = queued_path.stat()
    assert stat.S_IMODE(queued_stat_after.st_mode) == 0o600
    assert (queued_stat_after.st_dev, queued_stat_after.st_ino) == (
        queued_stat_before.st_dev,
        queued_stat_before.st_ino,
    )

    assert not claim_path.exists()
    assert not response_path.exists()
    assert term_file.is_file()
    assert term_file.read_bytes() == receipt_bytes_before
    receipt_stat_after = term_file.stat()
    assert (receipt_stat_after.st_dev, receipt_stat_after.st_ino) == (
        receipt_stat_before.st_dev,
        receipt_stat_before.st_ino,
    )
    _assert_runs_unchanged(data)
    assert spy_dispatch.call_count == 0


def test_claim_next_symlinked_terminal_continuation_directory_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    spy_dispatch: mock.MagicMock,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    claimed_dir = data["claimed_dir"]

    _, _, requests_dir, _, responses_dir = broker_paths(root)
    queued_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, queued_path)

    queued_bytes_before = queued_path.read_bytes()
    queued_stat_before = queued_path.stat()

    canary_dir = root / "outside_canary_dir"
    canary_dir.mkdir(mode=0o700, exist_ok=True)
    canary_file = canary_dir / f"{req_id}.json"
    canary_bytes = b"canary-terminal-receipt"
    canary_file.write_bytes(canary_bytes)
    canary_stat_before = canary_file.stat()

    term_continuation_dir = requests_dir.parent / "terminal-continuation"
    assert not os.path.lexists(term_continuation_dir)
    term_continuation_dir.symlink_to(canary_dir)

    response_path = responses_dir / f"{req_id}.json"
    assert not response_path.exists()
    assert not claim_path.exists()

    with pytest.raises(BrokerError):
        _claim_next(requests_dir, claimed_dir, responses_dir)

    assert queued_path.is_file()
    assert queued_path.read_bytes() == queued_bytes_before
    queued_stat_after = queued_path.stat()
    assert stat.S_IMODE(queued_stat_after.st_mode) == 0o600
    assert (queued_stat_after.st_dev, queued_stat_after.st_ino) == (
        queued_stat_before.st_dev,
        queued_stat_before.st_ino,
    )

    assert not claim_path.exists()
    assert not response_path.exists()

    assert canary_file.is_file()
    assert canary_file.read_bytes() == canary_bytes
    canary_stat_after = canary_file.stat()
    assert (canary_stat_after.st_dev, canary_stat_after.st_ino) == (
        canary_stat_before.st_dev,
        canary_stat_before.st_ino,
    )
    _assert_runs_unchanged(data)
    assert spy_dispatch.call_count == 0


def test_serve_continuation_success_receipt_persisted_before_response_and_unlink(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response = data["response"]
    response_path = data["response_path"]
    claimed_dir = data["claimed_dir"]

    _, _, requests_dir, _, _ = broker_paths(root)
    req_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, req_path)
    assert req_path.is_file()
    assert not claim_path.exists()

    events: list[str] = []

    orig_publish = publish_terminal_continuation_record

    def spy_publish(root_arg: Path, req_arg: dict, resp_arg: dict) -> dict:
        events.append("publish_start")
        res = orig_publish(root_arg, req_arg, resp_arg)
        events.append("publish_done")
        return res

    orig_atomic = _atomic_new_json

    def spy_atomic(path_arg: Path, obj_arg: Any, max_bytes_arg: int) -> None:
        if path_arg == response_path:
            events.append("response_write")
        orig_atomic(path_arg, obj_arg, max_bytes_arg)

    orig_unlink = Path.unlink

    def spy_unlink(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == claim_path or (
            self.name == f"{req_id}.json" and self.parent == claimed_dir
        ):
            events.append("claim_unlink")
        return orig_unlink(self, *args, **kwargs)

    mock_provider = mock.MagicMock(return_value=response)

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        mock_provider,
    ), mock.patch(
        "scripts.two_site_quota_continuation_terminal_storage.publish_terminal_continuation_record",
        side_effect=spy_publish,
    ), mock.patch(
        "scripts.two_site_provider_broker._atomic_new_json",
        side_effect=spy_atomic,
    ), mock.patch.object(
        Path,
        "unlink",
        spy_unlink,
    ):
        ret = serve(
            root,
            once=True,
            quota_continuation_enabled=True,
            install_signal_handlers=False,
        )

    assert ret == 0
    assert mock_provider.call_count == 1
    mock_provider.assert_called_once_with(root, resume_request)

    term_file = _terminal_file(root, req_id)
    assert term_file.is_file()
    validated_record = read_terminal_continuation_record(
        root, req_id, expected_request=resume_request
    )
    assert validated_record["request_id"] == req_id
    assert validated_record["response"] == response

    assert "publish_done" in events
    assert "response_write" in events
    assert "claim_unlink" in events
    pub_idx = events.index("publish_done")
    resp_idx = events.index("response_write")
    unlink_idx = events.index("claim_unlink")
    assert pub_idx < resp_idx
    assert pub_idx < unlink_idx
    assert resp_idx < unlink_idx

    assert response_path.is_file()
    assert json.loads(response_path.read_text(encoding="utf-8")) == response
    canonical_resp = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert response_path.read_bytes() == canonical_resp

    assert not claim_path.exists()
    assert not req_path.exists()
    _assert_runs_unchanged(data)


def test_serve_continuation_terminal_publisher_failure_fails_closed_without_replay(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response = data["response"]
    response_path = data["response_path"]
    claimed_dir = data["claimed_dir"]
    responses_dir = data["responses_dir"]

    _, _, requests_dir, _, _ = broker_paths(root)
    req_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, req_path)
    claim_bytes_before = req_path.read_bytes()

    mock_provider = mock.MagicMock(return_value=response)
    injected_marker = "injected_terminal_publisher_failure_secret_marker_7f9a"

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        mock_provider,
    ):
        with mock.patch(
            "scripts.two_site_quota_continuation_terminal_storage.publish_terminal_continuation_record",
            side_effect=TerminalContinuationStorageError(injected_marker),
        ):
            with pytest.raises(BrokerError) as exc_info:
                serve(
                    root,
                    once=True,
                    quota_continuation_enabled=True,
                    install_signal_handlers=False,
                )

        broker_err = exc_info.value
        assert str(broker_err) == "continuation dispatch failed or returned invalid response"
        assert broker_err.__cause__ is None
        assert injected_marker not in str(broker_err)
        tb_formatted = "".join(
            traceback.format_exception(
                type(broker_err), broker_err, broker_err.__traceback__
            )
        )
        assert injected_marker not in tb_formatted

        assert mock_provider.call_count == 1

        term_dir = (
            root
            / ".local"
            / "two-site-refactor"
            / "provider-broker"
            / TERMINAL_DIR_NAME
        )
        term_file = _terminal_file(root, req_id)
        assert not term_file.exists()
        if term_dir.exists():
            markers = [
                p
                for p in term_dir.iterdir()
                if p.name.startswith(f".{req_id}") or p.name.startswith(req_id)
            ]
            assert markers == []

        assert not response_path.exists()
        assert claim_path.is_file()
        assert claim_path.read_bytes() == claim_bytes_before
        assert not req_path.exists()

        with pytest.raises(
            BrokerError, match="continuation terminal receipt is missing or invalid"
        ):
            _recover_interrupted_claims(root, claimed_dir, responses_dir)

        assert mock_provider.call_count == 1
        assert claim_path.is_file()
        assert claim_path.read_bytes() == claim_bytes_before
        assert not response_path.exists()
        _assert_runs_unchanged(data)


def test_serve_continuation_response_write_failure_recovers_from_receipt_without_replay(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response = data["response"]
    response_path = data["response_path"]
    claimed_dir = data["claimed_dir"]
    responses_dir = data["responses_dir"]

    _, _, requests_dir, _, _ = broker_paths(root)
    req_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, req_path)
    claim_bytes_before = req_path.read_bytes()

    mock_provider = mock.MagicMock(return_value=response)
    orig_atomic = _atomic_new_json

    def failing_atomic_new_json(
        path_arg: Path, obj_arg: Any, max_bytes_arg: int
    ) -> None:
        if path_arg == response_path:
            raise OSError("injected response queue write failure")
        orig_atomic(path_arg, obj_arg, max_bytes_arg)

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        mock_provider,
    ):
        with mock.patch(
            "scripts.two_site_provider_broker._atomic_new_json",
            side_effect=failing_atomic_new_json,
        ):
            with pytest.raises(OSError, match="injected response queue write failure"):
                serve(
                    root,
                    once=True,
                    quota_continuation_enabled=True,
                    install_signal_handlers=False,
                )

        assert mock_provider.call_count == 1

        term_file = _terminal_file(root, req_id)
        assert term_file.is_file()
        readback = read_terminal_continuation_record(
            root, req_id, expected_request=resume_request
        )
        assert readback["request_id"] == req_id
        assert readback["response"] == response

        assert claim_path.is_file()
        assert claim_path.read_bytes() == claim_bytes_before
        assert not req_path.exists()
        assert not response_path.exists()

        _recover_interrupted_claims(root, claimed_dir, responses_dir)

        assert mock_provider.call_count == 1
        assert response_path.is_file()
        st = response_path.stat()
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert json.loads(response_path.read_text(encoding="utf-8")) == response
        canonical_resp = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        assert response_path.read_bytes() == canonical_resp

        assert not claim_path.exists()
        assert term_file.is_file()
        _assert_runs_unchanged(data)


@pytest.mark.parametrize("stop_kind", ["regular_file", "broken_symlink"])
def test_serve_continuation_pre_dispatch_stop_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    stop_kind: str,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]
    claimed_dir = data["claimed_dir"]
    responses_dir = data["responses_dir"]

    _, _, requests_dir, _, _ = broker_paths(root)
    req_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, req_path)
    claim_bytes_before = req_path.read_bytes()
    assert req_path.is_file()
    assert not claim_path.exists()

    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    secret_marker = f"pre_dispatch_stop_secret_{stop_kind}_7e3a9c"
    if stop_kind == "regular_file":
        stop_path.write_text(f"STOP\n{secret_marker}\n", encoding="utf-8")
        assert stop_path.is_file()
    else:
        target_path = root / f"nonexistent_target_{secret_marker}"
        stop_path.symlink_to(target_path)
        assert stop_path.is_symlink()
        assert not stop_path.exists()
        assert os.path.lexists(stop_path)

    mock_provider = mock.MagicMock()

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        mock_provider,
    ):
        with pytest.raises(BrokerError) as exc_info:
            serve(
                root,
                once=True,
                quota_continuation_enabled=True,
                install_signal_handlers=False,
            )

        broker_err = exc_info.value
        assert str(broker_err) == "stop requested before continuation dispatch"
        assert broker_err.__cause__ is None
        assert secret_marker not in str(broker_err)
        tb_formatted = "".join(
            traceback.format_exception(
                type(broker_err), broker_err, broker_err.__traceback__
            )
        )
        assert secret_marker not in tb_formatted

        assert mock_provider.call_count == 0

        term_file = _terminal_file(root, req_id)
        assert not term_file.exists()
        assert not response_path.exists()
        assert not req_path.exists()
        assert claim_path.is_file()
        assert claim_path.read_bytes() == claim_bytes_before
        assert stat.S_IMODE(claim_path.stat().st_mode) == 0o600

        with pytest.raises(
            BrokerError, match="continuation terminal receipt is missing or invalid"
        ):
            _recover_interrupted_claims(root, claimed_dir, responses_dir)

        assert mock_provider.call_count == 0
        assert claim_path.is_file()
        assert claim_path.read_bytes() == claim_bytes_before
        assert not response_path.exists()
        assert not term_file.exists()
        _assert_runs_unchanged(data)


def test_serve_continuation_post_dispatch_stop_preserves_terminal_custody_and_response(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response = data["response"]
    response_path = data["response_path"]
    claimed_dir = data["claimed_dir"]

    _, _, requests_dir, _, _ = broker_paths(root)
    req_path = requests_dir / f"{req_id}.json"
    os.replace(claim_path, req_path)
    assert req_path.is_file()
    assert not claim_path.exists()

    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    events: list[str] = []

    def fake_provider(root_arg: Path, req_arg: dict) -> dict:
        events.append("provider_call")
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.write_text("STOP\n", encoding="utf-8")
        return response

    mock_provider = mock.MagicMock(side_effect=fake_provider)

    orig_publish = publish_terminal_continuation_record

    def spy_publish(root_arg: Path, req_arg: dict, resp_arg: dict) -> dict:
        res = orig_publish(root_arg, req_arg, resp_arg)
        assert _terminal_file(root_arg, req_id).is_file()
        events.append("publish_done")
        return res

    orig_atomic = _atomic_new_json

    def spy_atomic(path_arg: Path, obj_arg: Any, max_bytes_arg: int) -> None:
        if path_arg == response_path:
            events.append("response_write")
        orig_atomic(path_arg, obj_arg, max_bytes_arg)

    orig_unlink = Path.unlink

    def spy_unlink(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == claim_path or (
            self.name == f"{req_id}.json" and self.parent == claimed_dir
        ):
            events.append("claim_unlink")
        return orig_unlink(self, *args, **kwargs)

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        mock_provider,
    ), mock.patch(
        "scripts.two_site_quota_continuation_terminal_storage.publish_terminal_continuation_record",
        side_effect=spy_publish,
    ), mock.patch(
        "scripts.two_site_provider_broker._atomic_new_json",
        side_effect=spy_atomic,
    ), mock.patch.object(
        Path,
        "unlink",
        spy_unlink,
    ):
        ret = serve(
            root,
            once=True,
            quota_continuation_enabled=True,
            install_signal_handlers=False,
        )

    assert ret == 0
    assert mock_provider.call_count == 1
    mock_provider.assert_called_once_with(root, resume_request)
    assert stop_path.is_file()

    term_file = _terminal_file(root, req_id)
    assert term_file.is_file()
    validated_record = read_terminal_continuation_record(
        root, req_id, expected_request=resume_request
    )
    assert validated_record["request_id"] == req_id
    assert validated_record["response"] == response

    assert "provider_call" in events
    assert "publish_done" in events
    assert "response_write" in events
    assert "claim_unlink" in events
    provider_idx = events.index("provider_call")
    pub_idx = events.index("publish_done")
    resp_idx = events.index("response_write")
    unlink_idx = events.index("claim_unlink")
    assert provider_idx < pub_idx < resp_idx < unlink_idx

    assert response_path.is_file()
    st = response_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert json.loads(response_path.read_text(encoding="utf-8")) == response
    canonical_resp = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert response_path.read_bytes() == canonical_resp

    assert not claim_path.exists()
    assert not req_path.exists()
    _assert_runs_unchanged(data)
