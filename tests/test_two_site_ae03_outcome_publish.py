#!/usr/bin/env python3
"""Hermetic core tests for publish_validated_rescope_outcome."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_assignment_scope import AssignmentScope
from collections.abc import Callable

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    load_latest_rescope_receipt,
    load_rescope_receipt,
    publish_rescope_intent,
    publish_validated_rescope_outcome,
    rescope_intent_exists,
)
import scripts.two_site_codex_autopilot as autopilot_mod

HEAD_SHA = "a" * 40


def make_canonical_repo() -> dict[str, object]:
    scope = AssignmentScope(
        assignment_id="ae03-assign-1",
        stage_id="P1-00b",
        sha256="0" * 64,
        expires_at="2099-12-31T23:59:59Z",
        allowed_paths=("scripts/two_site_codex_autopilot.py",),
        forbidden_paths=(),
    )
    return {
        "head": HEAD_SHA,
        "status": "",
        "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
        "assignment_scope": scope,
    }


def make_rescope_result(head: str = HEAD_SHA, summary: str = "scope rescope") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": summary,
        "commit": None,
        "next_action": "rescope task to smaller unit",
        "blockers": ["bounded task needs split"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": head,
        "head_after": head,
        "head_changed": False,
    }


def test_outcome_publish_attempt1_accepts_advisories_and_persisted_receipt_excludes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_canonical_repo()
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: repo)

    publish_rescope_intent(epoch, 1)

    result_with_advisories = dict(make_rescope_result())
    result_with_advisories["advisories"] = []

    outcome = publish_validated_rescope_outcome(root, epoch, 1, result_with_advisories, repo)
    assert outcome["attempt"] == 1
    assert outcome["terminal"] is False
    assert "advisories" not in outcome["result"]
    assert outcome["result"]["state"] == "BLOCKED"

    persisted = load_rescope_receipt(epoch, repo, "rescope_receipt.json")
    assert persisted is not None
    assert "advisories" not in persisted["result"]
    assert persisted["attempt"] == 1
    assert persisted["terminal"] is False


def test_outcome_publish_sequential_attempts_2_and_3_with_terminal3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_canonical_repo()
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: repo)

    # Attempt 1
    publish_rescope_intent(epoch, 1)
    res1 = make_rescope_result(summary="rescope 1")
    out1 = publish_validated_rescope_outcome(root, epoch, 1, res1, repo)
    assert out1["attempt"] == 1
    assert out1["terminal"] is False

    # Attempt 2
    publish_rescope_intent(epoch, 2)
    res2 = make_rescope_result(summary="rescope 2")
    out2 = publish_validated_rescope_outcome(root, epoch, 2, res2, repo)
    assert out2["attempt"] == 2
    assert out2["terminal"] is False

    # Attempt 3
    publish_rescope_intent(epoch, 3)
    res3 = make_rescope_result(summary="rescope 3")
    out3 = publish_validated_rescope_outcome(root, epoch, 3, res3, repo)
    assert out3["attempt"] == 3
    assert out3["terminal"] is True

    latest = load_latest_rescope_receipt(epoch, repo)
    assert latest is not None
    assert latest["attempt"] == 3
    assert latest["terminal"] is True


def test_outcome_publish_missing_marker_blocks_zero_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_canonical_repo()
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: repo)

    res = make_rescope_result()
    with pytest.raises(AutopilotError, match="rescope intent does not exist"):
        publish_validated_rescope_outcome(root, epoch, 1, res, repo)

    assert load_latest_rescope_receipt(epoch, repo) is None
    assert not (epoch / "rescope_receipt.json").exists()


def test_outcome_publish_attempt2_without_prior_receipt_blocks_zero_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_canonical_repo()
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: repo)

    publish_rescope_intent(epoch, 2)
    res = make_rescope_result()

    with pytest.raises(AutopilotError, match="attempt 2 requires prior rescope receipt"):
        publish_validated_rescope_outcome(root, epoch, 2, res, repo)

    assert load_latest_rescope_receipt(epoch, repo) is None
    assert not (epoch / "rescope_receipt_2.json").exists()
    assert not (epoch / "rescope_receipt.json").exists()


def test_outcome_publish_duplicate_attempt1_proves_prior_receipt_error_and_unchanged_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_canonical_repo()
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: repo)

    publish_rescope_intent(epoch, 1)
    assert rescope_intent_exists(epoch, 1) is True

    res1 = make_rescope_result(summary="initial rescope summary")
    outcome1 = publish_validated_rescope_outcome(root, epoch, 1, res1, repo)
    assert outcome1["attempt"] == 1
    assert outcome1["terminal"] is False

    receipt_path = epoch / "rescope_receipt.json"
    assert receipt_path.exists()
    exact_bytes = receipt_path.read_bytes()

    res1_retry = make_rescope_result(summary="changed rescope summary")
    with pytest.raises(AutopilotError, match=r"^attempt 1 requires no prior rescope receipt$"):
        publish_validated_rescope_outcome(root, epoch, 1, res1_retry, repo)

    assert receipt_path.read_bytes() == exact_bytes
    persisted = load_rescope_receipt(epoch, repo, "rescope_receipt.json")
    assert persisted is not None
    assert persisted["result"]["summary"] == "initial rescope summary"
    assert rescope_intent_exists(epoch, 1) is True


@pytest.mark.parametrize(
    "attempt, mutate_fn, expected_match",
    [
        (
            1,
            lambda r: r.update({"extra_key": "unexpected"}),
            r"^result keys do not match RESCOPE_RECEIPT_KEYS$",
        ),
        (
            1,
            lambda r: r.pop("schema_version"),
            r"^result keys do not match RESCOPE_RECEIPT_KEYS$",
        ),
        (
            1,
            lambda r: r.update({"advisories": ["nonempty_advisory"]}),
            r"^advisories must be an empty list$",
        ),
        (
            True,
            lambda r: None,
            r"^attempt must be an integer in 1..3$",
        ),
    ],
    ids=[
        "unexpected_extra_key",
        "removed_required_key",
        "nonempty_advisories",
        "attempt_boolean_true",
    ],
)
def test_outcome_publish_invalid_input_variants_reject_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attempt: object,
    mutate_fn: Callable[[dict[str, object]], None],
    expected_match: str,
) -> None:
    repo = make_canonical_repo()
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: repo)

    publish_rescope_intent(epoch, 1)
    assert rescope_intent_exists(epoch, 1) is True

    receipt_path = epoch / "rescope_receipt.json"
    assert not receipt_path.exists()

    res = make_rescope_result()
    mutate_fn(res)

    with pytest.raises(AutopilotError, match=expected_match):
        publish_validated_rescope_outcome(root, epoch, attempt, res, repo)

    assert not receipt_path.exists()
    assert load_latest_rescope_receipt(epoch, repo) is None
    assert rescope_intent_exists(epoch, 1) is True


def _make_drifted_repo(variant: str) -> dict[str, object]:
    repo = make_canonical_repo()
    if variant == "changed_head":
        repo["head"] = "f" * 40
    elif variant == "protected_ref":
        refs = dict(repo["protected_refs"])
        first_ref = sorted(PROTECTED_REFS)[0]
        refs[first_ref] = "f" * 40
        repo["protected_refs"] = refs
    elif variant == "control_blob":
        blobs = dict(repo["control_blobs"])
        first_blob = sorted(p.as_posix() for p in CONTROL_PATHS)[0]
        blobs[first_blob] = "f" * 40
        repo["control_blobs"] = blobs
    elif variant == "assignment_scope":
        repo["assignment_scope"] = AssignmentScope(
            assignment_id="ae03-assign-drifted",
            stage_id="P1-00b",
            sha256="1" * 64,
            expires_at="2099-12-31T23:59:59Z",
            allowed_paths=("scripts/two_site_codex_autopilot.py",),
            forbidden_paths=(),
        )
    elif variant == "nonempty_status":
        repo["status"] = " M scripts/two_site_codex_autopilot.py"
    else:
        raise ValueError(f"Unknown variant: {variant}")
    return repo


@pytest.mark.parametrize(
    "variant",
    [
        "changed_head",
        "protected_ref",
        "control_blob",
        "assignment_scope",
        "nonempty_status",
    ],
)
def test_outcome_publish_repository_drift_variants_reject_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    expected_repo = make_canonical_repo()
    drifted_repo = _make_drifted_repo(variant)
    root = tmp_path / "root"
    epoch = tmp_path / "epoch"
    epoch.mkdir(mode=0o700)
    monkeypatch.setattr(autopilot_mod, "validate_repository", lambda r, require_clean=True: drifted_repo)

    publish_rescope_intent(epoch, 1)
    assert rescope_intent_exists(epoch, 1) is True

    receipt_path = epoch / "rescope_receipt.json"
    assert not receipt_path.exists()

    res = make_rescope_result()
    with pytest.raises(AutopilotError) as exc_info:
        publish_validated_rescope_outcome(root, epoch, 1, res, expected_repo)

    assert str(exc_info.value).strip() != ""
    assert not receipt_path.exists()
    assert load_latest_rescope_receipt(epoch, expected_repo) is None
    assert rescope_intent_exists(epoch, 1) is True
