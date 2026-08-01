"""Adversarial local tests for the Full-Matrix receipt journal.

The journal test doubles never execute a real phase or reach a host, socket,
Object Storage, Docker, PostgreSQL, or a command.
"""

from __future__ import annotations

from datetime import datetime, timezone
import ast
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest import mock
from uuid import UUID

from core import physical_full_matrix_execution_driver as driver
from core import physical_full_matrix_receipt_journal as journal
from core.physical_full_matrix_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PhysicalFullMatrixCampaignReadiness,
)


NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
RUN_ID = UUID("d6df149a-a5c7-4768-b6ea-b53a6ad6436c")
PLAN_SHA256 = "a" * 64
REQUEST_ONE = "b" * 64
REQUEST_TWO = "c" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "physical_full_matrix_receipt_journal.py"
)


def _receipt(
    *,
    run_id: UUID,
    plan_sha256: str,
    sequence: int,
    phase_request_sha256: str,
    previous_receipt_sha256: str,
) -> bytes:
    phase = driver.PHYSICAL_FULL_MATRIX_PHASES[sequence - 1]
    body = {
        "schema": driver.PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_SCHEMA,
        "status": "completed-redacted-phase-receipt",
        "run_id": str(run_id),
        "plan_sha256": plan_sha256,
        "sequence": sequence,
        "phase": phase.name,
        "phase_request_sha256": phase_request_sha256,
        "oracle": phase.oracle,
        "oracle_evidence_sha256": "d" * 64,
        "previous_receipt_sha256": previous_receipt_sha256,
        "recorded_at": "2026-07-31T14:00:00Z",
        "campaign_id": "physical-full-matrix-20260731",
        "release_sha": "e" * 40,
        "readiness_binding_sha256": "0" * 63 + "3",
        "release_manifest_sha256": "f" * 64,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "route_binding_sha256": "1" * 64,
        "writer_epoch": 7,
        "writer_lease_id": "writer-lease-20260731",
        "witness_transition_id": "witness-transition-20260731",
        "witnessed_term_proof_sha256": "2" * 64,
        "direct_fi_to_ir_control": "forbidden",
        "direct_ir_to_fi_control": "forbidden",
        "legacy_runner_compatibility": "forbidden",
        "successor_binding": None,
        "full_matrix_executed": False,
    }
    return driver._receipt_from_body(body).canonical_receipt


class _PhaseAdapter:
    """A local deterministic driver seam; it has no live capability."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.calls: list[driver.PhysicalFullMatrixExecutionRequest] = []

    def execute_phase(
        self, *, request: driver.PhysicalFullMatrixExecutionRequest
    ) -> driver.PhysicalFullMatrixPhaseOracle:
        self.calls.append(request)
        phase = request.phase
        binding = request.binding
        return driver.PhysicalFullMatrixPhaseOracle(
            schema=driver.PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=phase.name,
            oracle=phase.oracle,
            transport_profile=phase.transport_profile,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            release_manifest_sha256=binding.release_manifest_sha256,
            route_binding_sha256=binding.route_binding_sha256,
            writer_epoch=binding.writer_epoch,
            writer_lease_id=binding.writer_lease_id,
            witness_transition_id=binding.witness_transition_id,
            witnessed_term_proof_sha256=binding.witnessed_term_proof_sha256,
            evidence_sha256=(str(phase.sequence) * 64)[:64],
            observed_at=self.now,
        )


@unittest.skipUnless(os.geteuid() == 0, "root-only receipt-journal tests require root")
class PhysicalFullMatrixReceiptJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="full-matrix-receipt-journal-")
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.patch_root = mock.patch.object(
            journal,
            "FIXED_PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT",
            self.root,
        )
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.config = journal.RootOwnedPhysicalFullMatrixReceiptJournalConfig(enabled=True)
        self.instance = journal.RootOwnedPhysicalFullMatrixReceiptJournal(self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def state_path(self) -> Path:
        return self.root / "receipt-journal.json"

    def _claim(
        self,
        *,
        sequence: int = 1,
        request_sha256: str = REQUEST_ONE,
        instance: journal.RootOwnedPhysicalFullMatrixReceiptJournal | None = None,
    ) -> driver.PhysicalFullMatrixPhaseClaim:
        active = self.instance if instance is None else instance
        return active.claim_phase(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=sequence,
            phase_request_sha256=request_sha256,
        )

    def test_default_off_nonroot_and_unsafe_fixed_root_fail_before_state_open(self) -> None:
        disabled = journal.RootOwnedPhysicalFullMatrixReceiptJournal(
            journal.RootOwnedPhysicalFullMatrixReceiptJournalConfig()
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DISABLED$",
        ):
            disabled.read_receipts(run_id=RUN_ID)
        self.assertFalse((self.root / "receipt-journal.lock").exists())

        with mock.patch.object(journal.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                journal.PhysicalFullMatrixReceiptJournalError,
                "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_ROOT_RUNTIME_REQUIRED$",
            ):
                self.instance.read_receipts(run_id=RUN_ID)
        self.assertFalse((self.root / "receipt-journal.lock").exists())

        self.root.chmod(0o755)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE$",
        ):
            self.instance.read_receipts(run_id=RUN_ID)

    def test_claim_append_idempotence_and_redacted_semantic_append_only_chain(self) -> None:
        claim_one = self._claim()
        self.assertIsNotNone(claim_one.claim_id)
        first = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_ONE,
            previous_receipt_sha256="0" * 64,
        )
        self.assertEqual(
            first,
            self.instance.append_claimed(
                claim=claim_one,
                canonical_receipt=first,
            ),
        )
        self.assertEqual((first,), tuple(self.instance.read_receipts(run_id=RUN_ID)))
        self.assertEqual(0o600, stat.S_IMODE(os.lstat(self.state_path).st_mode))

        existing = self._claim()
        self.assertIsNone(existing.claim_id)
        self.assertEqual(first, existing.existing_receipt)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_CONFLICT$",
        ):
            self._claim(request_sha256="9" * 64)

        parsed_first = driver.parse_physical_full_matrix_run_receipt(first)
        claim_two = self._claim(sequence=2, request_sha256=REQUEST_TWO)
        second = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=2,
            phase_request_sha256=REQUEST_TWO,
            previous_receipt_sha256=parsed_first.receipt_sha256,
        )
        self.instance.append_claimed(claim=claim_two, canonical_receipt=second)
        self.assertEqual((first, second), tuple(self.instance.read_receipts(run_id=RUN_ID)))

        state = json.loads(self.state_path.read_text(encoding="ascii"))
        self.assertFalse(state["completion_authorized"])
        self.assertFalse(state["promotion_authorized"])
        self.assertFalse(state["full_matrix_executed"])
        rendered = self.state_path.read_bytes().lower()
        for forbidden in (b"credential", b"secret", b"ssh", b"scp", b"docker"):
            self.assertNotIn(forbidden, rendered)

    def test_pending_claim_is_crash_safe_busy_and_forged_append_never_persists(self) -> None:
        claim = self._claim()
        repeated = self._claim()
        self.assertIsNone(repeated.claim_id)
        self.assertIsNone(repeated.existing_receipt)

        forged = driver.PhysicalFullMatrixPhaseClaim(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_ONE,
            claim_id=claim.claim_id,
        )
        valid = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_ONE,
            previous_receipt_sha256="0" * 64,
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE$",
        ):
            self.instance.append_claimed(claim=forged, canonical_receipt=valid)
        self.assertEqual((), tuple(self.instance.read_receipts(run_id=RUN_ID)))

        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_INVALID$",
        ):
            self.instance.append_claimed(
                claim=claim,
                canonical_receipt=b"x" * (journal._MAX_RECEIPT_BYTES + 1),  # type: ignore[attr-defined]
            )
        self.assertEqual((), tuple(self.instance.read_receipts(run_id=RUN_ID)))

        wrong = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=2,
            phase_request_sha256=REQUEST_TWO,
            previous_receipt_sha256="0" * 64,
        )
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_CLAIM_MISMATCH$",
        ):
            self.instance.append_claimed(claim=claim, canonical_receipt=wrong)
        self.assertEqual((), tuple(self.instance.read_receipts(run_id=RUN_ID)))

        self.instance.append_claimed(claim=claim, canonical_receipt=valid)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE$",
        ):
            self.instance.append_claimed(claim=claim, canonical_receipt=valid)

    def test_out_of_order_and_tampered_canonical_state_are_rejected_on_reread(self) -> None:
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_SEQUENCE_INVALID$",
        ):
            self._claim(sequence=2, request_sha256=REQUEST_TWO)
        self.assertFalse(self.state_path.exists())

        claim = self._claim()
        first = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_ONE,
            previous_receipt_sha256="0" * 64,
        )
        self.instance.append_claimed(claim=claim, canonical_receipt=first)
        state = json.loads(self.state_path.read_text(encoding="ascii"))
        state["runs"][str(RUN_ID)]["receipts"].append(
            _receipt(
                run_id=RUN_ID,
                plan_sha256=PLAN_SHA256,
                sequence=2,
                phase_request_sha256=REQUEST_TWO,
                previous_receipt_sha256="8" * 64,
            ).decode("ascii")
        )
        self.state_path.write_bytes(
            journal._canonical_json(  # type: ignore[attr-defined]
                state,
                code="test-state",
            )
            + b"\n"
        )
        self.state_path.chmod(0o600)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CHAIN_INVALID$",
        ):
            self.instance.read_receipts(run_id=RUN_ID)

    def test_hard_link_symlink_and_replace_failure_fail_closed(self) -> None:
        claim = self._claim()
        first = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_ONE,
            previous_receipt_sha256="0" * 64,
        )
        self.instance.append_claimed(claim=claim, canonical_receipt=first)
        linked = self.root / "receipt-journal-link"
        os.link(self.state_path, linked)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "STATE_UNSAFE",
        ):
            self.instance.read_receipts(run_id=RUN_ID)
        linked.unlink()

        saved = self.root / "receipt-journal-saved.json"
        self.state_path.rename(saved)
        self.state_path.symlink_to(saved.name)
        with self.assertRaisesRegex(
            journal.PhysicalFullMatrixReceiptJournalError,
            "STATE_OPEN_FAILED|STATE_UNSAFE",
        ):
            self.instance.read_receipts(run_id=RUN_ID)
        self.state_path.unlink()
        saved.rename(self.state_path)

        fresh_root = self.root / "replace-failure"
        fresh_root.mkdir(mode=0o700)
        other = journal.RootOwnedPhysicalFullMatrixReceiptJournal(self.config)
        with mock.patch.object(
            journal,
            "FIXED_PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT",
            fresh_root,
        ), mock.patch.object(journal.os, "replace", side_effect=OSError("fixture")):
            with self.assertRaisesRegex(
                journal.PhysicalFullMatrixReceiptJournalError,
                "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_REPLACE_FAILED$",
            ):
                other.claim_phase(
                    run_id=RUN_ID,
                    plan_sha256=PLAN_SHA256,
                    sequence=1,
                    phase_request_sha256=REQUEST_ONE,
                )
        self.assertFalse((fresh_root / "receipt-journal.json").exists())

    def test_directory_fsync_failure_after_replace_leaves_durable_busy_claim(self) -> None:
        # Create the persistent lock before injecting a directory-fsync failure.
        self.instance.read_receipts(run_id=RUN_ID)
        original_fsync = journal._fsync  # type: ignore[attr-defined]

        def fail_directory_fsync(descriptor: int, *, code: str) -> None:
            if code == "PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED":
                raise journal.PhysicalFullMatrixReceiptJournalError(code)
            original_fsync(descriptor, code=code)

        with mock.patch.object(
            journal,
            "_fsync",
            side_effect=fail_directory_fsync,
        ):
            with self.assertRaisesRegex(
                journal.PhysicalFullMatrixReceiptJournalError,
                "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED$",
            ):
                self._claim()
        self.assertTrue(self.state_path.exists())
        restarted = journal.RootOwnedPhysicalFullMatrixReceiptJournal(self.config)
        busy = self._claim(instance=restarted)
        self.assertIsNone(busy.claim_id)
        self.assertIsNone(busy.existing_receipt)

    def test_temp_fsync_failure_leaves_no_claim_or_temp_alias(self) -> None:
        # Create the persistent lock before injecting the temporary-file fsync
        # fault, so the failure point is exclusively the state mutation.
        self.instance.read_receipts(run_id=RUN_ID)
        original_fsync = journal._fsync  # type: ignore[attr-defined]

        def fail_temp_fsync(descriptor: int, *, code: str) -> None:
            if code == "PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_FSYNC_FAILED":
                raise journal.PhysicalFullMatrixReceiptJournalError(code)
            original_fsync(descriptor, code=code)

        with mock.patch.object(journal, "_fsync", side_effect=fail_temp_fsync):
            with self.assertRaisesRegex(
                journal.PhysicalFullMatrixReceiptJournalError,
                "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_FSYNC_FAILED$",
            ):
                self._claim()
        self.assertFalse(self.state_path.exists())
        self.assertEqual([], list(self.root.glob(".receipt-journal.tmp-*")))
        self.assertIsNotNone(self._claim().claim_id)

    def test_append_retry_is_idempotent_after_post_replace_directory_fsync_failure(self) -> None:
        claim = self._claim()
        receipt = _receipt(
            run_id=RUN_ID,
            plan_sha256=PLAN_SHA256,
            sequence=1,
            phase_request_sha256=REQUEST_ONE,
            previous_receipt_sha256="0" * 64,
        )
        original_fsync = journal._fsync  # type: ignore[attr-defined]

        def fail_directory_fsync(descriptor: int, *, code: str) -> None:
            if code == "PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED":
                raise journal.PhysicalFullMatrixReceiptJournalError(code)
            original_fsync(descriptor, code=code)

        with mock.patch.object(
            journal,
            "_fsync",
            side_effect=fail_directory_fsync,
        ):
            with self.assertRaisesRegex(
                journal.PhysicalFullMatrixReceiptJournalError,
                "^PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED$",
            ):
                self.instance.append_claimed(claim=claim, canonical_receipt=receipt)
        self.assertEqual(
            receipt,
            self.instance.append_claimed(claim=claim, canonical_receipt=receipt),
        )
        self.assertEqual((receipt,), tuple(self.instance.read_receipts(run_id=RUN_ID)))

    def test_flock_serializes_two_concurrent_claimers_without_duplicate_claim(self) -> None:
        first = journal.RootOwnedPhysicalFullMatrixReceiptJournal(self.config)
        second = journal.RootOwnedPhysicalFullMatrixReceiptJournal(self.config)
        barrier = threading.Barrier(2)
        outcomes: list[driver.PhysicalFullMatrixPhaseClaim] = []
        failures: list[Exception] = []

        def claim_with(instance: journal.RootOwnedPhysicalFullMatrixReceiptJournal) -> None:
            try:
                barrier.wait(timeout=5)
                outcomes.append(
                    instance.claim_phase(
                        run_id=RUN_ID,
                        plan_sha256=PLAN_SHA256,
                        sequence=1,
                        phase_request_sha256=REQUEST_ONE,
                    )
                )
            except Exception as exc:  # pragma: no cover - assertion below reports it
                failures.append(exc)

        left = threading.Thread(target=claim_with, args=(first,))
        right = threading.Thread(target=claim_with, args=(second,))
        left.start()
        right.start()
        left.join(timeout=10)
        right.join(timeout=10)
        self.assertFalse(left.is_alive())
        self.assertFalse(right.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(2, len(outcomes))
        self.assertEqual(1, sum(item.claim_id is not None for item in outcomes))
        self.assertEqual(
            1,
            sum(item.claim_id is None and item.existing_receipt is None for item in outcomes),
        )

    def test_driver_integration_runs_only_local_fake_phase_and_rereads_durable_receipt(self) -> None:
        binding = driver.PhysicalFullMatrixExecutionBinding(
            campaign_id="physical-full-matrix-20260731",
            release_sha="a" * 40,
            readiness_binding_sha256="b" * 64,
            release_manifest_sha256="c" * 64,
            route_binding_sha256="d" * 64,
            writer_epoch=7,
            writer_lease_id="lease-20260731",
            witness_transition_id="transition-20260731",
            witnessed_term_proof_sha256="e" * 64,
        )
        readiness = PhysicalFullMatrixCampaignReadiness(
            schema=PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
            status=PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
            reason_codes=(),
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            binding_sha256=binding.readiness_binding_sha256,
            observed_slots=driver.PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS,
        )
        config = driver.PhysicalFullMatrixExecutionConfig(
            binding=binding,
            readiness=readiness,
            run_id=RUN_ID,
            enabled=True,
        )
        # This integration test owns only the local receipt-journal seam.
        # Provenance is separately adversarially tested at the driver boundary,
        # so retain its compact synthetic readiness fixture without creating a
        # production mint bypass.
        with mock.patch.object(
            driver,
            "require_verified_physical_full_matrix_campaign_readiness",
            side_effect=lambda value, **_kwargs: value,
        ):
            plan = driver.build_physical_full_matrix_execution_plan(config=config)
            adapters = {
                phase.name: _PhaseAdapter(NOW)
                for phase in driver.PHYSICAL_FULL_MATRIX_PHASES
            }
            result = driver.execute_next_physical_full_matrix_phase(
                config=config,
                plan=plan,
                adapters=driver.PhysicalFullMatrixExecutionAdapters(
                    phase_adapters=adapters,
                    receipt_journal=self.instance,
                ),
                now=NOW,
            )
        self.assertEqual("completed-redacted-phase-receipt", result.status)
        self.assertFalse(result.full_matrix_executed)
        self.assertEqual(1, len(adapters[plan.phases[0].name].calls))
        self.assertTrue(self.state_path.exists())

    def test_module_has_no_live_transport_or_phase_adapter_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertTrue(
            imports.issubset(
                {
                    "__future__",
                    "collections",
                    "contextlib",
                    "dataclasses",
                    "fcntl",
                    "json",
                    "os",
                    "pathlib",
                    "re",
                    "secrets",
                    "stat",
                    "typing",
                    "uuid",
                    "core",
                }
            )
        )
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "docker",
                "paramiko",
                "psycopg",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )
        self.assertNotIn("def execute_phase", source)
        for forbidden in (
            "import boto3",
            "import docker",
            "import paramiko",
            "import psycopg",
            "import requests",
            "import socket",
            "import subprocess",
            "import urllib",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
