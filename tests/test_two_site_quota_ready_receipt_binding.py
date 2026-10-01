"""Tests for scripts.two_site_quota_ready_receipt_binding."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
from typing import Mapping
import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_proposal_pipeline import (
    MAX_CONTEXT,
    ProposalError,
    load_verifier_pre_review_receipt,
)
from scripts.two_site_quota_ready_receipt_artifacts import (
    validate_ready_receipt_artifacts,
)
from scripts.two_site_quota_ready_receipt_binding import (
    verify_ready_pre_review_receipt,
)


class EqualitySpoof:
    """Non-string object with equality-spoofing behavior."""

    def __init__(self, target: object) -> None:
        self.target = target

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


def _make_valid_ready(
    run_prefix: str = "run-prefix-1",
    correction_count: int = 0,
    proposal_sha: str = "a" * 64,
    test_sha: str = "b" * 64,
) -> dict[str, object]:
    return {
        "run_prefix": run_prefix,
        "correction_count": correction_count,
        "pre_review_test_path": (
            f".local/two-site-refactor/pipeline/{run_prefix}/"
            f"pre-review-tests-{correction_count}.json"
        ),
        "pre_review_test_sha256": test_sha,
        "pre_review_test_state": "PASSED",
        "proposal_sha256": proposal_sha,
    }


def _make_ten_key_passed_receipt(
    proposal_sha: str = "a" * 64,
    selectors_list: list[str] | None = None,
) -> dict[str, object]:
    if selectors_list is None:
        selectors_list = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    return {
        "state": "PASSED",
        "proposal_sha256": proposal_sha,
        "selectors": list(selectors_list),
        "unsupported_specs": [],
        "returncode": 0,
        "stdout_tail": "",
        "stderr_tail": "",
        "snapshot_files": 1,
        "snapshot_bytes": 10,
        "isolation": "bwrap",
    }


def test_exact_public_api_signature() -> None:
    from scripts import two_site_quota_ready_receipt_binding as mod

    assert mod.__all__ == ["verify_ready_pre_review_receipt"]
    sig = inspect.signature(verify_ready_pre_review_receipt)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["root", "ready", "expected_selectors"]
    assert [p.kind for p in params] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ]
    for p in params:
        assert p.default is inspect.Parameter.empty


def test_real_canonical_positive_unmocked_loader(tmp_path: Path) -> None:
    expected_selectors = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    proposal_sha = "1" * 64
    receipt_data = _make_ten_key_passed_receipt(
        proposal_sha=proposal_sha, selectors_list=expected_selectors
    )
    assert "selectors" in receipt_data
    assert "test_selectors" not in receipt_data

    run_prefix = "run-prefix-real"
    rel_path = f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-0.json"
    receipt_file = tmp_path / Path(rel_path)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = json.dumps(receipt_data).encode("utf-8")
    receipt_file.write_bytes(raw_bytes)
    receipt_sha = hashlib.sha256(raw_bytes).hexdigest()

    ready = _make_valid_ready(
        run_prefix=run_prefix,
        correction_count=0,
        proposal_sha=proposal_sha,
        test_sha=receipt_sha,
    )
    result = verify_ready_pre_review_receipt(tmp_path, ready, expected_selectors)
    assert result == {
        "status": "RECEIPT_BINDING_OBSERVED_NOT_ACCEPTED",
        "receipt_sha256": receipt_sha,
        "proposal_sha256": proposal_sha,
        "selector_count": 1,
    }


def test_boundary_symlink_asserts_loader_error_and_redacted_helper_error(
    tmp_path: Path,
) -> None:
    expected_selectors = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    proposal_sha = "2" * 64
    receipt_data = _make_ten_key_passed_receipt(
        proposal_sha=proposal_sha, selectors_list=expected_selectors
    )
    raw_bytes = json.dumps(receipt_data).encode("utf-8")
    receipt_sha = hashlib.sha256(raw_bytes).hexdigest()

    target_file = tmp_path / "valid_target_receipt.json"
    target_file.write_bytes(raw_bytes)

    run_prefix = "run-symlink"
    rel_path = f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-0.json"
    receipt_file = tmp_path / Path(rel_path)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.symlink_to(target_file)

    ready = _make_valid_ready(
        run_prefix=run_prefix,
        correction_count=0,
        proposal_sha=proposal_sha,
        test_sha=receipt_sha,
    )
    validated = validate_ready_receipt_artifacts(ready)

    with pytest.raises(ProposalError) as loader_exc:
        load_verifier_pre_review_receipt(tmp_path, validated)
    assert re.search(r"symlink|escape", str(loader_exc.value), re.IGNORECASE)

    with pytest.raises(ValueError) as helper_exc:
        verify_ready_pre_review_receipt(tmp_path, ready, expected_selectors)
    assert helper_exc.value.args[0] == "pre-review receipt verification failed"
    assert helper_exc.value.__cause__ is None


def test_boundary_hardlink_asserts_loader_error_and_redacted_helper_error(
    tmp_path: Path,
) -> None:
    expected_selectors = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    proposal_sha = "3" * 64
    receipt_data = _make_ten_key_passed_receipt(
        proposal_sha=proposal_sha, selectors_list=expected_selectors
    )
    raw_bytes = json.dumps(receipt_data).encode("utf-8")
    receipt_sha = hashlib.sha256(raw_bytes).hexdigest()

    sibling_file = tmp_path / "valid_sibling_receipt.json"
    sibling_file.write_bytes(raw_bytes)

    run_prefix = "run-hardlink"
    rel_path = f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-0.json"
    receipt_file = tmp_path / Path(rel_path)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    os.link(sibling_file, receipt_file)
    assert receipt_file.stat().st_nlink >= 2

    ready = _make_valid_ready(
        run_prefix=run_prefix,
        correction_count=0,
        proposal_sha=proposal_sha,
        test_sha=receipt_sha,
    )
    validated = validate_ready_receipt_artifacts(ready)

    with pytest.raises(ProposalError) as loader_exc:
        load_verifier_pre_review_receipt(tmp_path, validated)
    assert "hardlink" in str(loader_exc.value).lower()

    with pytest.raises(ValueError) as helper_exc:
        verify_ready_pre_review_receipt(tmp_path, ready, expected_selectors)
    assert helper_exc.value.args[0] == "pre-review receipt verification failed"
    assert helper_exc.value.__cause__ is None


def test_boundary_oversize_asserts_loader_error_and_redacted_helper_error(
    tmp_path: Path,
) -> None:
    expected_selectors = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    proposal_sha = "4" * 64
    receipt_data = _make_ten_key_passed_receipt(
        proposal_sha=proposal_sha, selectors_list=expected_selectors
    )
    valid_bytes = json.dumps(receipt_data).encode("utf-8")
    padded_bytes = valid_bytes + (b" " * (MAX_CONTEXT + 256))

    run_prefix = "run-oversize"
    rel_path = f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-0.json"
    receipt_file = tmp_path / Path(rel_path)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_bytes(padded_bytes)
    receipt_sha = hashlib.sha256(padded_bytes).hexdigest()

    ready = _make_valid_ready(
        run_prefix=run_prefix,
        correction_count=0,
        proposal_sha=proposal_sha,
        test_sha=receipt_sha,
    )
    validated = validate_ready_receipt_artifacts(ready)

    with pytest.raises(ProposalError) as loader_exc:
        load_verifier_pre_review_receipt(tmp_path, validated)
    assert re.search(
        r"exceeds safe context budget|context budget",
        str(loader_exc.value),
        re.IGNORECASE,
    )

    with pytest.raises(ValueError) as helper_exc:
        verify_ready_pre_review_receipt(tmp_path, ready, expected_selectors)
    assert helper_exc.value.args[0] == "pre-review receipt verification failed"
    assert helper_exc.value.__cause__ is None


def test_selectors_none_return_invokes_no_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loader_invoked = False

    def forbidden_loader(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal loader_invoked
        loader_invoked = True
        raise AssertionError("Canonical loader must not be invoked")

    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.load_verifier_pre_review_receipt",
        forbidden_loader,
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.selectors",
        lambda s: None,
    )
    ready = _make_valid_ready()
    selectors_list = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready, selectors_list)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert exc_info.value.__cause__ is None
    assert not loader_invoked


def test_selectors_error_and_mismatch_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loader_invoked = False

    def forbidden_loader(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal loader_invoked
        loader_invoked = True
        raise AssertionError("Canonical loader must not be invoked")

    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.load_verifier_pre_review_receipt",
        forbidden_loader,
    )

    ready = _make_valid_ready()
    selectors_list = ["tests/test_two_site_quota_ready_receipt_binding.py"]

    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.selectors",
        lambda s: tuple(s),
    )
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready, selectors_list)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert not loader_invoked

    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.selectors",
        lambda s: ["tests/test_other.py"],
    )
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready, selectors_list)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert not loader_invoked

    def raise_err(s: object) -> list[str]:
        raise RuntimeError("syntax error in selector")

    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.selectors",
        raise_err,
    )
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready, selectors_list)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert not loader_invoked


def test_input_types_fail_closed_before_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loader_invoked = False

    def forbidden_loader(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal loader_invoked
        loader_invoked = True
        raise AssertionError("Canonical loader must not be invoked")

    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.load_verifier_pre_review_receipt",
        forbidden_loader,
    )

    ready = _make_valid_ready()
    valid_sel = ["tests/test_two_site_quota_ready_receipt_binding.py"]

    for bad_root in (str(tmp_path), 123, None, [tmp_path]):
        with pytest.raises(ValueError) as exc_info:
            verify_ready_pre_review_receipt(bad_root, ready, valid_sel)  # type: ignore
        assert exc_info.value.args[0] == "pre-review receipt verification failed"
        assert not loader_invoked

    for bad_sel in (
        None,
        [],
        [""],
        [123],
        valid_sel * 201,
        tuple(valid_sel),
        "tests/test_a.py",
    ):
        with pytest.raises(ValueError) as exc_info:
            verify_ready_pre_review_receipt(tmp_path, ready, bad_sel)  # type: ignore
        assert exc_info.value.args[0] == "pre-review receipt verification failed"
        assert not loader_invoked

    for bad_ready in (None, "not-mapping", 123, [ready]):
        with pytest.raises(ValueError) as exc_info:
            verify_ready_pre_review_receipt(tmp_path, bad_ready, valid_sel)  # type: ignore
        assert exc_info.value.args[0] == "pre-review receipt verification failed"
        assert not loader_invoked


def test_equality_spoofed_ready_path_and_state_before_io(tmp_path: Path) -> None:
    valid_sel = ["tests/test_two_site_quota_ready_receipt_binding.py"]

    ready_spoof_path = _make_valid_ready()
    ready_spoof_path["pre_review_test_path"] = EqualitySpoof(
        ready_spoof_path["pre_review_test_path"]
    )
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready_spoof_path, valid_sel)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert exc_info.value.__cause__ is None

    ready_spoof_state = _make_valid_ready()
    ready_spoof_state["pre_review_test_state"] = EqualitySpoof("PASSED")
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready_spoof_state, valid_sel)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert exc_info.value.__cause__ is None


def test_malformed_json_sentinel_redacted(tmp_path: Path) -> None:
    valid_sel = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    run_prefix = "run-malformed"
    rel_path = f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-0.json"
    receipt_file = tmp_path / Path(rel_path)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    malformed_content = b"SENTINEL_MALFORMED_BYTES_12345"
    receipt_file.write_bytes(malformed_content)
    test_sha = hashlib.sha256(malformed_content).hexdigest()

    ready = _make_valid_ready(
        run_prefix=run_prefix,
        correction_count=0,
        test_sha=test_sha,
    )
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready, valid_sel)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert "SENTINEL_MALFORMED_BYTES_12345" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_loader_result_validation_mismatches_and_unsupported_specs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    valid_sel = ["tests/test_two_site_quota_ready_receipt_binding.py"]
    ready = _make_valid_ready(proposal_sha="5" * 64)

    base_receipt = _make_ten_key_passed_receipt(
        proposal_sha="5" * 64, selectors_list=valid_sel
    )

    mutations = [
        {"selectors": ["tests/test_mismatch.py"]},
        {"proposal_sha256": "6" * 64},
        {"unsupported_specs": ["tests/test_unsupported.py"]},
        {"unsupported_specs": None},
        {"state": "FAILED"},
    ]

    for patch_dict in mutations:
        cand = dict(base_receipt)
        cand.update(patch_dict)
        monkeypatch.setattr(
            "scripts.two_site_quota_ready_receipt_binding.load_verifier_pre_review_receipt",
            lambda r, a, c=cand: dict(c),
        )
        with pytest.raises(ValueError) as exc_info:
            verify_ready_pre_review_receipt(tmp_path, ready, valid_sel)
        assert exc_info.value.args[0] == "pre-review receipt verification failed"
        assert exc_info.value.__cause__ is None

    cand_missing = dict(base_receipt)
    del cand_missing["unsupported_specs"]
    monkeypatch.setattr(
        "scripts.two_site_quota_ready_receipt_binding.load_verifier_pre_review_receipt",
        lambda r, a: dict(cand_missing),
    )
    with pytest.raises(ValueError) as exc_info:
        verify_ready_pre_review_receipt(tmp_path, ready, valid_sel)
    assert exc_info.value.args[0] == "pre-review receipt verification failed"
    assert exc_info.value.__cause__ is None
