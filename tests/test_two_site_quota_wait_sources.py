#!/usr/bin/env python3
"""Hermetic unit tests for initial quota wait sources collector."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AssignmentScope,
    rescope_epoch_basename,
    validate_accepted_repository,
)
from scripts.two_site_provider_broker import (
    broker_paths,
    publish_cycle_reservation,
    publish_terminal_quota_record,
    read_terminal_quota_bytes,
)
from scripts.two_site_quota_wait_sources import (
    WaitSourceError,
    collect_initial_wait_source_expected,
)
from tests.test_two_site_provider_broker_cycle_reservation_read import (
    _setup_cycle_root,
    _write_startup_json,
)
from tests.test_two_site_provider_broker_terminal_record import (
    _setup_quota_roundtrip_fixture,
)


def _snapshot_tree(root: Path) -> dict[Path, tuple[int, int]]:
    return {
        p.relative_to(root): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def _build_valid_repo_facts(request: dict) -> dict:
    return {
        "head": request["source_head"],
        "status": "",
        "protected_refs": {ref: "1" * 40 for ref in sorted(PROTECTED_REFS)},
        "control_blobs": {
            p.as_posix(): "2" * 40 for p in sorted(CONTROL_PATHS, key=lambda p: p.as_posix())
        },
        "assignment_scope": AssignmentScope(
            assignment_id=request["assignment_id"],
            stage_id="P1-00b",
            sha256=request["assignment_sha256"],
            expires_at="2026-09-30T00:00:00Z",
            allowed_paths=["scripts/two_site_quota_wait_sources.py"],
            forbidden_paths=[],
        ),
    }


def _setup_test_env(
    tmp_dir: str,
    *,
    publish_reservation: bool = True,
    publish_receipt: bool = True,
) -> tuple[Path, dict, dict, Path, Path, str, dict, dict | None]:
    root = Path(tmp_dir).resolve()
    broker_paths(root)
    request, response, task_path, status_path = _setup_quota_roundtrip_fixture(root)

    stdout_obj = json.loads(response["stdout"])
    stdout_obj["semantic_task_fingerprint"] = "d" * 64
    response["stdout"] = json.dumps(
        stdout_obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    root, runs_dir = _setup_cycle_root(tmp_dir, request["controller_run_id"])
    _write_startup_json(
        runs_dir,
        cycle_id=request["controller_run_id"],
        source_head=request["source_head"],
        assignment_sha256=request["assignment_sha256"],
    )

    if publish_reservation:
        publish_cycle_reservation(root, request)

    published_receipt = None
    if publish_receipt:
        published_receipt = publish_terminal_quota_record(root, request, response)

    repo_facts = _build_valid_repo_facts(request)
    epoch = rescope_epoch_basename(validate_accepted_repository(repo_facts))

    return root, request, response, task_path, status_path, epoch, repo_facts, published_receipt


class TestTwoSiteQuotaWaitSources(unittest.TestCase):
    def test_positive_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (
                root,
                request,
                _response,
                _task_path,
                _status_path,
                epoch,
                repo_facts,
                published_receipt,
            ) = _setup_test_env(tmp_dir)
            assert published_receipt is not None

            snap_before = _snapshot_tree(root)

            with mock.patch(
                "scripts.two_site_quota_wait_sources.validate_repository",
                side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
            ):
                expected = collect_initial_wait_source_expected(
                    root, request["controller_run_id"], epoch, 1
                )

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)
            self.assertFalse((root / ".local" / "two-site-refactor" / "quota-wait").exists())

            self.assertIsInstance(expected, dict)
            self.assertEqual(len(expected), 12)
            self.assertEqual(expected["request_sha256"], published_receipt["request_sha256"])

            receipt_bytes = read_terminal_quota_bytes(root, request["request_id"])
            expected_receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
            self.assertEqual(expected["terminal_receipt_sha256"], expected_receipt_sha)

            self.assertEqual(expected["semantic_task_fingerprint"], "d" * 64)
            self.assertEqual(expected["controller_run_id"], request["controller_run_id"])
            self.assertEqual(expected["rescope_epoch"], epoch)
            self.assertEqual(expected["rescope_attempt"], 1)
            self.assertEqual(expected["request_id"], request["request_id"])

    def test_missing_cycle_reservation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (
                root,
                request,
                _,
                _,
                _,
                epoch,
                repo_facts,
                _,
            ) = _setup_test_env(tmp_dir, publish_reservation=False)

            snap_before = _snapshot_tree(root)
            with mock.patch(
                "scripts.two_site_quota_wait_sources.validate_repository",
                side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
            ):
                with self.assertRaises(WaitSourceError):
                    collect_initial_wait_source_expected(
                        root, request["controller_run_id"], epoch, 1
                    )

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)
            self.assertFalse((root / ".local" / "two-site-refactor" / "quota-wait").exists())

    def test_missing_terminal_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (
                root,
                request,
                _,
                _,
                _,
                epoch,
                repo_facts,
                _,
            ) = _setup_test_env(tmp_dir, publish_receipt=False)

            snap_before = _snapshot_tree(root)
            with mock.patch(
                "scripts.two_site_quota_wait_sources.validate_repository",
                side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
            ):
                with self.assertRaises(WaitSourceError):
                    collect_initial_wait_source_expected(
                        root, request["controller_run_id"], epoch, 1
                    )

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)
            self.assertFalse((root / ".local" / "two-site-refactor" / "quota-wait").exists())

    def test_status_witness_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (
                root,
                request,
                _,
                _,
                status_path,
                epoch,
                repo_facts,
                _,
            ) = _setup_test_env(tmp_dir)

            status_path.write_bytes(b'{"drifted": true}')

            snap_before = _snapshot_tree(root)
            with mock.patch(
                "scripts.two_site_quota_wait_sources.validate_repository",
                side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
            ):
                with self.assertRaises(WaitSourceError):
                    collect_initial_wait_source_expected(
                        root, request["controller_run_id"], epoch, 1
                    )

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)
            self.assertFalse((root / ".local" / "two-site-refactor" / "quota-wait").exists())

    def test_mismatched_epoch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (
                root,
                request,
                _,
                _,
                _,
                _epoch,
                repo_facts,
                _,
            ) = _setup_test_env(tmp_dir)

            bad_epoch = "rescope-" + "0" * 64

            snap_before = _snapshot_tree(root)
            with mock.patch(
                "scripts.two_site_quota_wait_sources.validate_repository",
                side_effect=lambda *args, **kwargs: copy.deepcopy(repo_facts),
            ):
                with self.assertRaises(WaitSourceError) as ctx:
                    collect_initial_wait_source_expected(
                        root, request["controller_run_id"], bad_epoch, 1
                    )
            self.assertIn("rescope_epoch mismatch", str(ctx.exception))

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)
            self.assertFalse((root / ".local" / "two-site-refactor" / "quota-wait").exists())

    def test_repository_drift_on_second_validate_call_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (
                root,
                request,
                _,
                _,
                _,
                epoch,
                repo_facts,
                _,
            ) = _setup_test_env(tmp_dir)

            drifted_repo_facts = copy.deepcopy(repo_facts)
            drifted_repo_facts["head"] = "f" * 40

            snap_before = _snapshot_tree(root)
            with mock.patch(
                "scripts.two_site_quota_wait_sources.validate_repository",
                side_effect=[
                    copy.deepcopy(repo_facts),
                    drifted_repo_facts,
                    copy.deepcopy(repo_facts),
                ],
            ):
                with self.assertRaises(WaitSourceError) as ctx:
                    collect_initial_wait_source_expected(
                        root, request["controller_run_id"], epoch, 1
                    )
            self.assertIn("repository facts changed during Broker reads", str(ctx.exception))

            snap_after = _snapshot_tree(root)
            self.assertEqual(snap_before, snap_after)
            self.assertFalse((root / ".local" / "two-site-refactor" / "quota-wait").exists())


if __name__ == "__main__":
    unittest.main()
