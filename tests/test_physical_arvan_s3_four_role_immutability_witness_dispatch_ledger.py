"""Adversarial tests for the root-owned Witness dispatch ledger.

The test path uses only signed semantic fixtures and a private temporary
directory.  It deliberately has no Object Storage client, peer address, or
delivery transport.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization

from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as probe
from core import physical_arvan_s3_four_role_immutability_preflight as immutable
from core import physical_arvan_s3_four_role_immutability_witness_dispatch_ledger as dispatch
from core import physical_arvan_s3_four_role_immutability_witness_orchestration as orchestration
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as failback
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


CAMPAIGN = "four-role-witness-dispatch-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 22, 0, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_immutability_witness_dispatch_ledger.py"
)


def _public_key(signer: object) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.failback_binding = failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256="4" * 64,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            fi_publisher_identity_sha256="5" * 64,
            ir_receiver_identity_sha256="6" * 64,
            ir_publisher_identity_sha256="7" * 64,
            fi_receiver_identity_sha256="8" * 64,
        )
        self.fixture = make_four_role_live_iam_durable_admission_fixture(
            binding=self.failback_binding,
            observed_at=NOW,
        )
        preflight = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-four-role-witness-dispatch",
            bucket_access_posture="private",
            normal_object_storage_namespace="physical-wal",
            reverse_object_storage_namespace="physical-failback",
            minimum_retention_days=90,
            normal_route_scope_sha256=self.fixture.live_iam_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=self.fixture.live_iam_binding.reverse_route_scope_sha256,
            four_role_route_binding_sha256=self.fixture.live_iam_binding.four_role_binding_sha256,
            fi_publisher_identity_sha256=self.fixture.live_iam_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.fixture.live_iam_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.fixture.live_iam_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.fixture.live_iam_binding.fi_receiver_identity_sha256,
        )
        self.binding = orchestration.build_physical_arvan_s3_four_role_immutability_witness_binding(
            preflight_binding=preflight,
            live_iam_binding=self.fixture.live_iam_binding,
            failback_binding=self.failback_binding,
            witness_public_key=_public_key(self.fixture.witness_signer),
        )
        self.tempdir = tempfile.TemporaryDirectory(prefix="four-role-witness-dispatch-")
        self.addCleanup(self.tempdir.cleanup)
        self.state_root = Path(self.tempdir.name)
        os.chmod(self.state_root, 0o700)
        self.config = dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig(
            state_root=self.state_root,
            binding=self.binding,
            enabled=True,
        )

    def _open(self):
        return dispatch.open_physical_arvan_s3_four_role_immutability_witness_dispatch_ledger(
            self.config
        )

    def _start(self, runtime, *, at: datetime = NOW):
        with mock.patch.object(dispatch, "_host_now", return_value=at):
            return dispatch.start_physical_arvan_s3_four_role_immutability_witness_dispatch(
                runtime=runtime,
                admission=self.fixture.live_iam_durable_admission,
                operation_nonce_sha256="a" * 64,
                normal_probe_nonce_sha256="b" * 64,
                witness_signer=self.fixture.witness_signer,
            )

    def _publisher_readback(self, request):
        bucket = (
            probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback(
                acl_posture="private-canonical-owner-only-v1",
                versioning_status="Enabled",
                retention_mode="s3-object-lock-compliance-v1",
                retention_days=90,
            )
            if request.role == profiles.ARVAN_S3_FI_PUBLISHER_ROLE
            else None
        )
        return probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback(
            schema=probe.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=request.probe_nonce_sha256,
            object_key=request.object_key,
            object_version_id=(
                "version-normal-dispatch-001"
                if request.role == profiles.ARVAN_S3_FI_PUBLISHER_ROLE
                else "version-reverse-dispatch-001"
            ),
            content_sha256=("a" if request.role == profiles.ARVAN_S3_FI_PUBLISHER_ROLE else "b")
            * 64,
            content_bytes=512,
            retention_until=request.retention_not_before,
            create_only_outcome="create-only-succeeded",
            overwrite_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_readback=bucket,
        )

    def _receiver_readback(self, request):
        version = request.immutable_version
        return probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback(
            schema=probe.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=version.probe_nonce_sha256,
            object_key=version.object_key,
            object_version_id=version.object_version_id,
            exact_head_version_id=version.object_version_id,
            exact_get_version_id=version.object_version_id,
            exact_get_content_sha256=version.content_sha256,
            exact_get_content_bytes=version.content_bytes,
            put_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_enumeration_outcome="access-denied",
            version_enumeration_outcome="access-denied",
        )

    def _receipt_for(self, result, *, at: datetime) -> bytes:
        assert result.approval is not None
        approval = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            result.approval,
            binding=self.binding,
            observed_at=at,
        )
        request = approval.approval.request
        readback = (
            self._publisher_readback(request)
            if type(request) is probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
            else self._receiver_readback(request)
        )
        return orchestration.seal_physical_arvan_s3_four_role_immutability_role_receipt(
            approval=approval,
            binding=self.binding,
            observed_at=at,
            local_readback=readback,
            role_signer=self.fixture.role_signers[request.role],
        )

    def _submit(self, runtime, receipt: bytes, *, at: datetime, reverse_nonce: str | None = None):
        with mock.patch.object(dispatch, "_host_now", return_value=at):
            return dispatch.submit_physical_arvan_s3_four_role_immutability_witness_role_receipt(
                runtime=runtime,
                admission=self.fixture.live_iam_durable_admission,
                receipt=receipt,
                witness_signer=self.fixture.witness_signer,
                reverse_probe_nonce_sha256=reverse_nonce,
            )

    def _complete_chain(self, runtime):
        pending = self._start(runtime)
        fi_receipt = self._receipt_for(pending, at=NOW + timedelta(seconds=1))
        pending = self._submit(runtime, fi_receipt, at=NOW + timedelta(seconds=1))
        ir_read_receipt = self._receipt_for(pending, at=NOW + timedelta(seconds=2))
        pending = self._submit(
            runtime,
            ir_read_receipt,
            at=NOW + timedelta(seconds=2),
            reverse_nonce="c" * 64,
        )
        ir_publish_receipt = self._receipt_for(pending, at=NOW + timedelta(seconds=3))
        pending = self._submit(runtime, ir_publish_receipt, at=NOW + timedelta(seconds=3))
        fi_read_receipt = self._receipt_for(pending, at=NOW + timedelta(seconds=4))
        return self._submit(runtime, fi_read_receipt, at=NOW + timedelta(seconds=4))

    def test_full_chain_is_durable_and_only_returns_witness_delivery_instructions(self) -> None:
        result = self._complete_chain(self._open())
        self.assertEqual("four-role-immutable-observed", result.status)
        self.assertIsNone(result.approval)
        self.assertIsNone(result.target_role)
        self.assertEqual("four-role-immutable-observed", result.preflight_observation.status)
        self.assertEqual(5, result.sequence)

    def test_restart_after_durable_start_replays_exact_pending_approval(self) -> None:
        first = self._start(self._open())
        restarted = self._open()
        resumed = self._start(restarted)
        self.assertEqual(first.approval, resumed.approval)
        self.assertEqual(first.ledger_head_sha256, resumed.ledger_head_sha256)
        self.assertEqual("fi-publisher", resumed.target_role)

    def test_restart_after_durable_receipt_replays_only_the_next_approval(self) -> None:
        runtime = self._open()
        first = self._start(runtime)
        receipt = self._receipt_for(first, at=NOW + timedelta(seconds=1))
        advanced = self._submit(runtime, receipt, at=NOW + timedelta(seconds=1))
        restarted = self._open()
        resumed = self._start(restarted, at=NOW + timedelta(seconds=1))
        self.assertEqual("ir-receiver", resumed.target_role)
        self.assertEqual(advanced.approval, resumed.approval)
        self.assertNotEqual(first.approval, resumed.approval)

    def test_exact_receipt_retry_is_idempotent_but_changed_receipt_is_rejected(self) -> None:
        runtime = self._open()
        first = self._start(runtime)
        receipt = self._receipt_for(first, at=NOW + timedelta(seconds=1))
        advanced = self._submit(runtime, receipt, at=NOW + timedelta(seconds=1))
        retry = self._submit(runtime, receipt, at=NOW + timedelta(seconds=1))
        self.assertEqual(advanced.approval, retry.approval)
        with self.assertRaisesRegex(
            dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError,
            "RECEIPT_INVALID",
        ):
            self._submit(runtime, receipt + b"\n", at=NOW + timedelta(seconds=1))

    def test_start_nonce_cannot_be_changed_after_reservation(self) -> None:
        runtime = self._open()
        self._start(runtime)
        with mock.patch.object(dispatch, "_host_now", return_value=NOW):
            with self.assertRaisesRegex(
                dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError,
                "OPERATION_ALREADY_BOUND",
            ):
                dispatch.start_physical_arvan_s3_four_role_immutability_witness_dispatch(
                    runtime=runtime,
                    admission=self.fixture.live_iam_durable_admission,
                    operation_nonce_sha256="d" * 64,
                    normal_probe_nonce_sha256="e" * 64,
                    witness_signer=self.fixture.witness_signer,
                )

    def test_expired_or_clock_rolled_back_host_state_fails_closed(self) -> None:
        runtime = self._open()
        self._start(runtime, at=NOW + timedelta(seconds=1))
        with mock.patch.object(dispatch, "_host_now", return_value=NOW):
            with self.assertRaisesRegex(
                dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError,
                "CLOCK_ROLLBACK",
            ):
                self._start(runtime, at=NOW)
        with mock.patch.object(
            dispatch,
            "_host_now",
            return_value=NOW + timedelta(seconds=121),
        ):
            with self.assertRaisesRegex(
                dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError,
                "ADMISSION_INVALID",
            ):
                self._start(runtime, at=NOW + timedelta(seconds=121))

    def test_symlink_state_is_rejected_without_following_it(self) -> None:
        unsafe_root = self.state_root / "unsafe"
        unsafe_root.mkdir(mode=0o700)
        os.symlink(
            "/dev/null",
            unsafe_root / "physical-arvan-s3-four-role-immutability-witness-dispatch-ledger-v1",
        )
        config = dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig(
            state_root=unsafe_root,
            binding=self.binding,
            enabled=True,
        )
        with self.assertRaises(dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError):
            dispatch.open_physical_arvan_s3_four_role_immutability_witness_dispatch_ledger(config)

    def test_record_directory_symlink_is_a_fail_closed_fork(self) -> None:
        self._open()
        records = (
            self.state_root
            / "physical-arvan-s3-four-role-immutability-witness-dispatch-ledger-v1"
            / "records"
        )
        os.symlink("/dev/null", records / ("00000000000000000001-" + "a" * 64 + ".json"))
        with self.assertRaisesRegex(
            dispatch.PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError,
            "RECORD_DIRECTORY_INVALID",
        ):
            self._open()

    def test_source_has_fd_anchored_state_and_no_peer_transport(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({"socket", "subprocess", "requests", "urllib", "boto3"} & imported)
        self.assertIn("dir_fd=", source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("os.fsync", source)


if __name__ == "__main__":
    unittest.main()
