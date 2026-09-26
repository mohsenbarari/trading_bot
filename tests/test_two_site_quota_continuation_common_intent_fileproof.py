"""Hermetic boundary tests for quota continuation common intent fileproof.

Boundary scope note:
This suite verifies the observational quota, no replay authority boundary.
It proves that a valid quota continuation request built from real Broker
fixture facts passes real, unpatched common request validation (branch, HEAD, clean
Git worktree, task SHA, task schema, assignment scope binding) and reaches real,
unpatched read_pending_rescope_intent against a durable file-backed 0600 marker.

Explicit disclosures:
1. scripts.two_site_quota_wait_intent.validate_repository is patched to return
   explicit accepted repository facts shaped like the joined W2 test because
   broker_repo intentionally lacks the full autopilot control-file tree. We do not
   claim real autopilot repository validation.
2. collect_initial_wait_source_expected is patched as the test boundary (raising a
   unique sentinel on positive admission and asserting non-execution on tamper).
3. This suite does NOT test or assert joined W2 success, Provider execution,
   Supervisor coordination, wait/claim publication, replay authority, or C5d completion.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from unittest import mock

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    REPOSITORY_FACT_KEYS,
    ensure_rescope_epoch_dir,
    publish_rescope_intent,
    rescope_epoch_basename,
    validate_accepted_repository,
)
from scripts.two_site_quota_continuation_admission import (
    ContinuationAdmissionError,
    validate_quota_continuation_request,
)
from scripts.two_site_quota_wait_intent import (
    RESCOPE_INTENT_FILENAMES,
    WaitIntentError,
    read_pending_rescope_intent,
)
from tests.test_two_site_provider_broker import broker_repo, make_valid_request
from tests.test_two_site_quota_continuation_common_broker_boundary import (
    _build_continuation_request,
)


def _setup_intent_marker(
    root: Path,
    scope: Any,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, str]:
    git_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    repo_facts: dict[str, Any] = {
        "head": git_head,
        "status": "",
        "protected_refs": {ref: "a" * 40 for ref in sorted(PROTECTED_REFS)},
        "control_blobs": {
            p.as_posix(): "b" * 40
            for p in sorted(CONTROL_PATHS, key=lambda p: p.as_posix())
        },
        "assignment_scope": scope,
    }
    assert set(repo_facts.keys()) == set(REPOSITORY_FACT_KEYS)

    validated_repo = validate_accepted_repository(repo_facts)
    epoch = rescope_epoch_basename(validated_repo)

    local_dir = root.resolve() / ".local"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_dir.chmod(0o700)

    refactor_dir = local_dir / "two-site-refactor"
    refactor_dir.mkdir(parents=True, exist_ok=True)
    refactor_dir.chmod(0o700)

    state_root = refactor_dir / "autopilot"
    state_root.mkdir(parents=True, exist_ok=True)
    state_root.chmod(0o700)

    epoch_dir = ensure_rescope_epoch_dir(state_root, validated_repo)
    publish_rescope_intent(epoch_dir, 1)

    marker_filename = RESCOPE_INTENT_FILENAMES[1]
    marker_path = epoch_dir / marker_filename
    assert marker_path.is_file()
    assert (marker_path.stat().st_mode & 0o777) == 0o600

    return repo_facts, validated_repo, epoch_dir, marker_path, epoch


def test_quota_continuation_common_intent_fileproof_positive(broker_repo: Any) -> None:
    """A valid continuation request passes real common validation and reads 0600 intent marker.

    Scope: observational quota, no replay authority.
    Proves that unpatched common validation and unpatched read_pending_rescope_intent
    succeed with a real 0600 marker, reaching collect_initial_wait_source_expected with
    the exact expected arguments.
    """
    root, task, scope, environment = broker_repo
    continuation_req = _build_continuation_request(root, task, environment)
    repo_facts, validated_repo, epoch_dir, marker_path, epoch = _setup_intent_marker(
        root, scope
    )

    sentinel = RuntimeError("sentinel initial wait source collector reached")
    with mock.patch(
        "scripts.two_site_quota_wait_intent.validate_repository",
        side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
    ) as mock_validate_repo:
        with mock.patch(
            "scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected",
            side_effect=sentinel,
        ) as mock_collector:
            with pytest.raises(ContinuationAdmissionError) as exc_info:
                validate_quota_continuation_request(
                    root,
                    continuation_req,
                    scope_loader=lambda _: scope,
                    now_epoch=time.time(),
                )

    assert exc_info.value.__cause__ is sentinel
    assert "failed to collect initial wait source expected" in str(exc_info.value)
    mock_collector.assert_called_once_with(
        root.resolve(),
        continuation_req["original_controller_run_id"],
        epoch,
        1,
    )
    assert mock_validate_repo.call_count >= 2

    # Direct corroboration of read_pending_rescope_intent against the published marker
    with mock.patch(
        "scripts.two_site_quota_wait_intent.validate_repository",
        side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
    ):
        direct_epoch, direct_attempt, direct_repo = read_pending_rescope_intent(root)
    assert direct_epoch == epoch
    assert direct_attempt == 1
    assert direct_repo == validated_repo


def test_quota_continuation_common_intent_fileproof_negative_permission_tamper(
    broker_repo: Any,
) -> None:
    """Tampering marker permissions to 0644 fails closed before expected-wait collection.

    Scope: observational quota, no replay authority.
    Proves that a 0644 marker triggers real WaitIntentError wrapped by
    ContinuationAdmissionError, suppresses collector execution, and preserves task,
    Git HEAD, and marker content.
    """
    root, task, scope, environment = broker_repo
    continuation_req = _build_continuation_request(root, task, environment)
    repo_facts, validated_repo, epoch_dir, marker_path, epoch = _setup_intent_marker(
        root, scope
    )

    original_task_bytes = task.read_bytes()
    original_marker_bytes = marker_path.read_bytes()
    original_head = continuation_req["source_head"]

    marker_path.chmod(0o644)
    try:
        mock_collector = mock.Mock()
        with mock.patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
        ):
            with mock.patch(
                "scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected",
                mock_collector,
            ):
                with pytest.raises(ContinuationAdmissionError) as exc_info:
                    validate_quota_continuation_request(
                        root,
                        continuation_req,
                        scope_loader=lambda _: scope,
                        now_epoch=time.time(),
                    )

        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, WaitIntentError)
        assert "0600" in str(exc_info.value.__cause__)
        assert "failed to read pending rescope intent:" in str(exc_info.value)
        mock_collector.assert_not_called()

        assert task.read_bytes() == original_task_bytes
        assert marker_path.read_bytes() == original_marker_bytes

        git_head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert git_head == original_head
    finally:
        marker_path.chmod(0o600)

    assert (marker_path.stat().st_mode & 0o777) == 0o600
