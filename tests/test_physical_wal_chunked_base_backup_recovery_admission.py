from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
import unittest

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_chunked_base_backup_receiver_receipt_ledger import (
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
)
from core.physical_wal_chunked_base_backup_receiver_staging_runtime import (
    RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig,
    execute_root_owned_physical_wal_chunked_base_backup_receiver_staging,
)
from core.physical_wal_chunked_base_backup_recovery_admission import (
    PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
    PhysicalWalChunkedBaseBackupRecoveryAdmissionScope,
    RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig,
    admit_root_owned_physical_wal_chunked_base_backup_recovery,
    require_verified_physical_wal_chunked_base_backup_recovery_admission,
    validate_root_owned_physical_wal_chunked_base_backup_recovery_admission_config,
)
from core import physical_wal_chunked_base_backup_recovery_admission as admission_module
from tests.test_physical_wal_chunked_base_backup_receiver_staging_runtime import (
    NOW,
    _Decryptor,
    _EvidenceFixture,
    _ExactReceiver,
)


class PhysicalWalChunkedBaseBackupRecoveryAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="chunked-v2-recovery-admission-")
        self.root = Path(self.temporary.name)
        self.stage_root = self.root / "stage-root"
        self.ledger_root = self.root / "ledger-root"
        self.stage_root.mkdir(mode=0o700)
        self.ledger_root.mkdir(mode=0o700)
        os.chmod(self.stage_root, 0o700)
        os.chmod(self.ledger_root, 0o700)
        self.evidence = _EvidenceFixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def receiver_config(self) -> RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig:
        return RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig(
            staging_root=self.stage_root,
            receipt_ledger_config=PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig(
                ledger_root=self.ledger_root,
                enabled=True,
            ),
            receiver_site="webapp_ir",
            enabled=True,
        )

    def admission_config(self, **changes: object) -> RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig:
        config = RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig(
            staging_root=self.stage_root,
            receiver_site="webapp_ir",
            enabled=True,
        )
        return replace(config, **changes)

    def scope(self, **changes: object) -> PhysicalWalChunkedBaseBackupRecoveryAdmissionScope:
        handoff = self.evidence.handoff
        scope = PhysicalWalChunkedBaseBackupRecoveryAdmissionScope(
            transfer_binding=self.evidence.binding,
            baseline_generation_id=handoff.baseline_generation_id,
            database_system_identifier=handoff.database_system_identifier,
            timeline_id=handoff.timeline_id,
            wal_segment_size_bytes=handoff.wal_segment_size_bytes,
            baseline_wal_lsn=handoff.baseline_wal_lsn,
            wal_chain_start_lsn=handoff.wal_chain_start_lsn,
            base_backup_end_lsn=handoff.base_backup_end_lsn,
            completion_attestation_sha256=handoff.completion_attestation_sha256,
            legacy_route_binding_sha256=handoff.legacy_route_binding_sha256,
            witness_transition_id=handoff.witness_transition_id,
        )
        return replace(scope, **changes)

    def stage(self):
        return execute_root_owned_physical_wal_chunked_base_backup_receiver_staging(
            self.receiver_config(),
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            exact_version_receiver=_ExactReceiver(self.evidence.objects),
            age_decryptor=_Decryptor(),
            clock=lambda: NOW,
        )

    def admit(self, result, **changes: object):
        return admit_root_owned_physical_wal_chunked_base_backup_recovery(
            self.admission_config(**changes),
            scope=self.scope(),
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            staging_result=result,
            now=NOW,
        )

    def require(self, admission, *, now=NOW):
        return require_verified_physical_wal_chunked_base_backup_recovery_admission(
            admission,
            config=self.admission_config(),
            scope=self.scope(),
            manifest=self.evidence.manifest,
            handoff_receipt=self.evidence.handoff,
            now=now,
        )

    def test_admits_only_exact_verified_v2_stage_and_rechecks_it(self) -> None:
        result = self.stage()
        admission = self.admit(result)

        self.assertEqual("webapp_ir", admission.receiver_site)
        self.assertEqual(result.stage_directory.name, admission.stage_directory_name)
        self.assertEqual(result.stage_receipt_sha256, admission.stage_receipt_sha256)
        self.assertEqual(self.evidence.handoff.receipt_id, admission.receipt_id)
        self.assertEqual(self.evidence.manifest.manifest_id, admission.manifest_id)
        self.assertEqual(self.evidence.handoff.lineage_sha256, admission.lineage_sha256)
        self.assertEqual(self.evidence.handoff.wal_segment_size_bytes, admission.wal_segment_size_bytes)
        self.assertIs(admission, self.require(admission))

    def test_admission_capability_is_process_local_and_not_serializable(self) -> None:
        admission = self.admit(self.stage())

        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(admission)

    def test_disabled_or_relaxed_policy_is_fail_closed_without_reading_stage(self) -> None:
        result = self.stage()
        for config in (
            replace(self.admission_config(), enabled=False),
            self.admission_config(remote_object_storage="allowed"),
            self.admission_config(v1_fallback="allowed"),
            self.admission_config(restore_or_promotion="allowed"),
        ):
            with self.subTest(config=config):
                with self.assertRaisesRegex(
                    PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
                    "CONFIG_INVALID",
                ):
                    validate_root_owned_physical_wal_chunked_base_backup_recovery_admission_config(
                        config,
                        require_enabled=True,
                    )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "CONFIG_INVALID",
        ):
            admit_root_owned_physical_wal_chunked_base_backup_recovery(
                replace(self.admission_config(), enabled=False),
                scope=self.scope(),
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                staging_result=result,
                now=NOW,
            )

    def test_forged_result_or_capability_never_admits(self) -> None:
        result = self.stage()
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_RESULT_INVALID",
        ):
            self.admit(object())
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_RESULT_MISMATCH",
        ):
            self.admit(replace(result, stage_receipt_sha256="f" * 64))

        admission = self.admit(result)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "CAPABILITY_REQUIRED",
        ):
            self.require(replace(admission, chunk_count=admission.chunk_count + 1))
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "CAPABILITY_REQUIRED",
        ):
            self.require(object())

    def test_manifest_or_handoff_capability_tampering_never_crosses_boundary(self) -> None:
        result = self.stage()
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "V2_CAPABILITY_INVALID",
        ):
            admit_root_owned_physical_wal_chunked_base_backup_recovery(
                self.admission_config(),
                scope=self.scope(),
                manifest=replace(self.evidence.manifest, total_plaintext_bytes=1),
                handoff_receipt=self.evidence.handoff,
                staging_result=result,
                now=NOW,
            )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "V2_CAPABILITY_INVALID",
        ):
            admit_root_owned_physical_wal_chunked_base_backup_recovery(
                self.admission_config(),
                scope=self.scope(),
                manifest=self.evidence.manifest,
                handoff_receipt=replace(self.evidence.handoff, snapshot_bytes=1),
                staging_result=result,
                now=NOW,
            )

    def test_scope_rejects_cross_campaign_release_route_recipient_and_wal(self) -> None:
        result = self.stage()
        alternate_bindings = (
            replace(self.evidence.binding, campaign_id="other-campaign-20260731"),
            replace(self.evidence.binding, release_sha="f" * 40),
            replace(self.evidence.binding, route_commitment_sha256="c" * 64),
            replace(
                self.evidence.binding,
                destination_age_recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqr",
            ),
        )
        for binding in alternate_bindings:
            with self.subTest(binding=binding):
                with self.assertRaisesRegex(
                    PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
                    "CROSS_PIN_MISMATCH",
                ):
                    admit_root_owned_physical_wal_chunked_base_backup_recovery(
                        self.admission_config(),
                        scope=self.scope(transfer_binding=binding),
                        manifest=self.evidence.manifest,
                        handoff_receipt=self.evidence.handoff,
                        staging_result=result,
                        now=NOW,
                    )
        for scope in (
            self.scope(timeline_id=2),
            self.scope(baseline_wal_lsn="0/1900000"),
            self.scope(base_backup_end_lsn="0/2900000"),
        ):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(
                    PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
                    "CROSS_PIN_MISMATCH",
                ):
                    admit_root_owned_physical_wal_chunked_base_backup_recovery(
                        self.admission_config(),
                        scope=scope,
                        manifest=self.evidence.manifest,
                        handoff_receipt=self.evidence.handoff,
                        staging_result=result,
                        now=NOW,
                    )
        # An unsupported WAL segment size is malformed policy, rather than a
        # valid-but-foreign known WAL geometry.  Both outcomes are fail closed.
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "SCOPE_INVALID",
        ):
            admit_root_owned_physical_wal_chunked_base_backup_recovery(
                self.admission_config(),
                scope=self.scope(wal_segment_size_bytes=8 * 1024 * 1024),
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                staging_result=result,
                now=NOW,
            )

    def test_scope_is_strictly_primitive_and_canonical_before_it_is_hashed(self) -> None:
        result = self.stage()

        class TextSubclass(str):
            pass

        malformed_scopes = (
            self.scope(
                transfer_binding=replace(
                    self.evidence.binding,
                    campaign_id=TextSubclass(self.evidence.binding.campaign_id),
                )
            ),
            self.scope(timeline_id=True),
            self.scope(baseline_generation_id="short"),
            self.scope(completion_attestation_sha256="0" * 64),
            self.scope(witness_transition_id="contains whitespace"),
        )
        for scope in malformed_scopes:
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(
                    PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
                    "SCOPE_INVALID",
                ):
                    admit_root_owned_physical_wal_chunked_base_backup_recovery(
                        self.admission_config(),
                        scope=scope,
                        manifest=self.evidence.manifest,
                        handoff_receipt=self.evidence.handoff,
                        staging_result=result,
                        now=NOW,
                    )

    def test_stale_handoff_is_not_recovery_evidence(self) -> None:
        result = self.stage()
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "V2_CAPABILITY_INVALID",
        ):
            admit_root_owned_physical_wal_chunked_base_backup_recovery(
                self.admission_config(),
                scope=self.scope(),
                manifest=self.evidence.manifest,
                handoff_receipt=self.evidence.handoff,
                staging_result=result,
                now=NOW + timedelta(minutes=3),
            )
        admission = self.admit(result)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "V2_CAPABILITY_INVALID",
        ):
            self.require(admission, now=NOW + timedelta(minutes=3))

    def test_canonical_stage_receipt_pins_every_shared_value(self) -> None:
        result = self.stage()
        payload = json.loads(result.stage_receipt_path.read_text(encoding="ascii"))
        payload["lineage_sha256"] = "f" * 64
        changed = canonical_json_bytes(payload)
        result.stage_receipt_path.write_bytes(changed)
        os.chmod(result.stage_receipt_path, 0o600)
        tampered = replace(result, stage_receipt_sha256=hashlib.sha256(changed).hexdigest())
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_RECEIPT_PIN_MISMATCH",
        ):
            self.admit(tampered)

    def test_symlink_stage_receipt_or_untrusted_result_path_is_never_followed(self) -> None:
        result = self.stage()
        target = self.root / "outside-receipt.json"
        target.write_bytes(result.stage_receipt_path.read_bytes())
        result.stage_receipt_path.unlink()
        result.stage_receipt_path.symlink_to(target)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_RECEIPT_UNSAFE",
        ):
            self.admit(result)

        unrelated = replace(
            result,
            stage_directory=self.root / "outside-stage",
            stage_receipt_path=self.root / "outside-stage" / "stage-receipt.json",
        )
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_PATH_INVALID",
        ):
            self.admit(unrelated)

    def test_stage_root_symlink_and_hardlinked_receipt_are_never_accepted(self) -> None:
        result = self.stage()
        os.link(result.stage_receipt_path, result.stage_directory / "second-receipt-link")
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_RECEIPT_UNSAFE",
        ):
            self.admit(result)

        # Root validation happens before receipt traversal, so the same
        # deliberately bad candidate also verifies the root-link boundary.
        real_root = self.root / "real-stage-root"
        self.stage_root.rename(real_root)
        self.stage_root.symlink_to(real_root, target_is_directory=True)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_ROOT_UNSAFE",
        ):
            self.admit(result)

    def test_existing_admission_rechecks_local_receipt_instead_of_trusting_cached_result(self) -> None:
        result = self.stage()
        admission = self.admit(result)
        result.stage_receipt_path.write_bytes(b"{}")
        os.chmod(result.stage_receipt_path, 0o600)
        with self.assertRaisesRegex(
            PhysicalWalChunkedBaseBackupRecoveryAdmissionError,
            "STAGE_RECEIPT_INVALID|STAGE_RECEIPT_NONCANONICAL",
        ):
            self.require(admission)

    def test_module_has_no_v1_transfer_fallback_or_remote_execution_surface(self) -> None:
        source = inspect.getsource(admission_module)
        for forbidden in (
            "physical_wal_base_backup_spool",
            "physical_wal_receiver_staging",
            "base_backup_object",
            "boto3",
            "requests",
            "socket",
            "subprocess",
            "os.unlink",
            "os.remove",
            "os.mkdir",
            "os.write",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
