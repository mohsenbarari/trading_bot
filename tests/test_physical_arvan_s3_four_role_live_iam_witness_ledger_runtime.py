"""Adversarial durable-boundary tests for the live-IAM Witness ledger runtime."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import pickle
import tempfile
import threading
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_live_iam_evidence as evidence
from core import physical_arvan_s3_four_role_live_iam_witness_ledger_runtime as runtime_module


CAMPAIGN = "four-role-runtime-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 14, 0, 0, tzinfo=timezone.utc)
NONCE = "1" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_live_iam_witness_ledger_runtime.py"
)


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _outcomes(role: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        [{"operation": item, "outcome": "allowed"} for item in evidence._ROLE_ALLOWED[role]],
        [{"operation": item, "outcome": "denied"} for item in evidence._ROLE_DENIED[role]],
    )


class PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name)
        os.chmod(self.state_root, 0o700)
        self.witness = Ed25519PrivateKey.generate()
        self.signers = {
            "fi-publisher": Ed25519PrivateKey.generate(),
            "ir-receiver": Ed25519PrivateKey.generate(),
            "ir-publisher": Ed25519PrivateKey.generate(),
            "fi-receiver": Ed25519PrivateKey.generate(),
        }
        self.binding = evidence.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            four_role_binding_sha256="4" * 64,
            fi_publisher_identity_sha256="5" * 64,
            ir_receiver_identity_sha256="6" * 64,
            ir_publisher_identity_sha256="7" * 64,
            fi_receiver_identity_sha256="8" * 64,
            fi_publisher_signer_public_key=_public_key(self.signers["fi-publisher"]),
            ir_receiver_signer_public_key=_public_key(self.signers["ir-receiver"]),
            ir_publisher_signer_public_key=_public_key(self.signers["ir-publisher"]),
            fi_receiver_signer_public_key=_public_key(self.signers["fi-receiver"]),
        )
        self.config = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
            state_root=self.state_root,
            evidence_binding=self.binding,
            enabled=True,
            maximum_records=12,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _open(self) -> runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime:
        return runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(
            self.config
        )

    def _issue(
        self,
        runtime: runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
        *,
        nonce: str = NONCE,
    ) -> tuple[evidence.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit, bytes]:
        _state, permit_raw = runtime_module.issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
            runtime=runtime,
            nonce=nonce,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            witness_signer=self.witness,
        )
        permit = evidence.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
            permit_raw,
            binding=self.binding,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW,
        )
        return permit, permit_raw

    def _direction(
        self,
        *,
        publisher_role: str,
        permit: evidence.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
        offset: int,
    ) -> tuple[
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    ]:
        receiver_role = evidence._RECEIVER_BY_DIRECTION[evidence._DIRECTION_BY_PUBLISHER[publisher_role]]
        publisher_allowed, publisher_denied = _outcomes(publisher_role)
        receiver_allowed, receiver_denied = _outcomes(receiver_role)
        first = NOW + timedelta(seconds=offset)
        locator = evidence.make_physical_arvan_s3_live_iam_probe_locator(
            binding=self.binding,
            nonce=permit.nonce,
            publisher_role=publisher_role,
            object_version_id=f"version-{publisher_role}-{offset}",
            content_sha256=("a" if publisher_role == "fi-publisher" else "b") * 64,
            content_bytes=64 + offset,
        )
        publisher_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.binding,
            nonce_permit=permit,
            publisher_role=publisher_role,
            observed_at=first,
            probe_locator=locator,
            allowed_operation_outcomes=publisher_allowed,
            denied_operation_outcomes=publisher_denied,
            role_signer=self.signers[publisher_role],
        )
        publisher = evidence.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
            publisher_raw,
            binding=self.binding,
            nonce_permit=permit,
            observed_at=first,
        )
        forwarded_at = first + timedelta(seconds=1)
        forward_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_witness_forward(
            binding=self.binding,
            nonce_permit=permit,
            publisher_observation=publisher,
            forwarded_at=forwarded_at,
            witness_signer=self.witness,
        )
        forward = evidence.verify_physical_arvan_s3_four_role_live_iam_witness_forward(
            forward_raw,
            binding=self.binding,
            nonce_permit=permit,
            witness_public_key=_public_key(self.witness),
            observed_at=forwarded_at,
        )
        received_at = forwarded_at + timedelta(seconds=1)
        receiver_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
            binding=self.binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received_at,
            allowed_operation_outcomes=receiver_allowed,
            denied_operation_outcomes=receiver_denied,
            role_signer=self.signers[receiver_role],
        )
        receiver = evidence.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
            receiver_raw,
            binding=self.binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received_at,
        )
        return publisher, forward, receiver

    def _commit(
        self,
        runtime: runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
    ) -> tuple[bytes, bytes]:
        permit, permit_raw = self._issue(runtime)
        normal_publisher, normal_forward, normal_receiver = self._direction(
            publisher_role="fi-publisher", permit=permit, offset=1
        )
        reverse_publisher, reverse_forward, reverse_receiver = self._direction(
            publisher_role="ir-publisher", permit=permit, offset=10
        )
        state, aggregate = runtime_module.seal_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
            runtime=runtime,
            nonce_permit=permit,
            normal_publisher_observation=normal_publisher,
            normal_witness_forward=normal_forward,
            normal_receiver_observation=normal_receiver,
            reverse_publisher_observation=reverse_publisher,
            reverse_witness_forward=reverse_forward,
            reverse_receiver_observation=reverse_receiver,
            committed_at=NOW + timedelta(seconds=20),
            witness_signer=self.witness,
        )
        self.assertEqual(2, state.sequence)
        return permit_raw, aggregate

    def _ledger_dir(self) -> Path:
        return self.state_root / runtime_module._LEDGER_DIRECTORY

    def test_normal_flow_persists_before_release_and_reopens_for_verification(self) -> None:
        first = self._open()
        permit_raw, aggregate = self._commit(first)
        after_commit = runtime_module.read_physical_arvan_s3_four_role_live_iam_witness_ledger_state(first)
        self.assertEqual(2, after_commit.sequence)
        self.assertEqual(1, after_commit.logical_record_count)
        reopened = self._open()
        reopened_state, verified = runtime_module.verify_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
            runtime=reopened,
            aggregate=aggregate,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW + timedelta(seconds=21),
        )
        self.assertEqual(2, reopened_state.sequence)
        self.assertEqual(NONCE, verified.nonce)
        with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "AGGREGATE_.*STALE"):
            runtime_module.verify_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
                runtime=reopened,
                aggregate=aggregate,
                witness_public_key=_public_key(self.witness),
                observed_at=NOW + timedelta(minutes=5),
            )
        record_files = sorted((self._ledger_dir() / runtime_module._RECORDS_DIRECTORY).iterdir())
        head_files = sorted((self._ledger_dir() / runtime_module._HEADS_DIRECTORY).iterdir())
        self.assertEqual(2, len(record_files))
        self.assertEqual(2, len(head_files))
        self.assertEqual(0o600, record_files[0].stat().st_mode & 0o777)
        self.assertEqual(0o700, self._ledger_dir().stat().st_mode & 0o777)
        private_raw = self.witness.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        persisted = b"".join(path.read_bytes() for path in [*record_files, *head_files])
        self.assertNotIn(private_raw, persisted)
        self.assertNotIn(permit_raw, persisted)
        self.assertNotIn(aggregate, persisted)
        with self.assertRaises(TypeError):
            pickle.dumps(first)
        with self.assertRaises(TypeError):
            pickle.dumps(after_commit)

    def test_expire_is_durable_then_reopen_allows_a_fresh_nonce(self) -> None:
        current = self._open()
        self._issue(current)
        receipt = runtime_module.expire_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce(
            runtime=current,
            nonce=NONCE,
            retired_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual(2, receipt.sequence)
        reopened = self._open()
        fresh_receipt, _permit = runtime_module.issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
            runtime=reopened,
            nonce="9" * 64,
            issued_at=NOW + timedelta(minutes=5),
            expires_at=NOW + timedelta(minutes=5, seconds=30),
            witness_signer=self.witness,
        )
        self.assertEqual(3, fresh_receipt.sequence)

    def test_partial_invalid_tail_and_foreign_binding_refuse_without_cleanup(self) -> None:
        current = self._open()
        self._issue(current)
        record_dir = self._ledger_dir() / runtime_module._RECORDS_DIRECTORY
        partial = record_dir / ("00000000000000000002-" + "f" * 64 + ".json")
        partial.write_bytes(b"{}")
        os.chmod(partial, 0o600)
        with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "PARTIAL_TAIL"):
            self._open()
        self.assertTrue(partial.exists())
        invalid_tmp = tempfile.TemporaryDirectory()
        try:
            invalid_root = Path(invalid_tmp.name)
            os.chmod(invalid_root, 0o700)
            invalid_config = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=invalid_root, evidence_binding=self.binding, enabled=True
            )
            invalid_runtime = runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(
                invalid_config
            )
            self._issue(invalid_runtime)
            invalid_ledger = invalid_root / runtime_module._LEDGER_DIRECTORY
            invalid_record = invalid_ledger / runtime_module._RECORDS_DIRECTORY / (
                "00000000000000000002-" + "d" * 64 + ".json"
            )
            invalid_head = invalid_ledger / runtime_module._HEADS_DIRECTORY / (
                "00000000000000000002-" + "e" * 64 + ".head"
            )
            invalid_record.write_bytes(b"{}")
            invalid_head.write_bytes(b"{}")
            os.chmod(invalid_record, 0o600)
            os.chmod(invalid_head, 0o600)
            with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "RECORD_INVALID"):
                runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(invalid_config)
        finally:
            invalid_tmp.cleanup()
        separate = tempfile.TemporaryDirectory()
        try:
            other_root = Path(separate.name)
            os.chmod(other_root, 0o700)
            other = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=other_root,
                evidence_binding=self.binding,
                enabled=True,
            )
            runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(other)
            foreign = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=other_root,
                evidence_binding=evidence.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
                    campaign_id=CAMPAIGN,
                    release_sha=RELEASE,
                    normal_route_scope_sha256="2" * 64,
                    reverse_route_scope_sha256="3" * 64,
                    four_role_binding_sha256="4" * 64,
                    fi_publisher_identity_sha256="a" * 64,
                    ir_receiver_identity_sha256="b" * 64,
                    ir_publisher_identity_sha256="c" * 64,
                    fi_receiver_identity_sha256="d" * 64,
                    fi_publisher_signer_public_key=_public_key(Ed25519PrivateKey.generate()),
                    ir_receiver_signer_public_key=_public_key(Ed25519PrivateKey.generate()),
                    ir_publisher_signer_public_key=_public_key(Ed25519PrivateKey.generate()),
                    fi_receiver_signer_public_key=_public_key(Ed25519PrivateKey.generate()),
                ),
                enabled=True,
            )
            with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "FOREIGN_BINDING"):
                runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(foreign)
        finally:
            separate.cleanup()

    def test_replay_fork_rollback_and_concurrent_stale_handle_fail_closed(self) -> None:
        first = self._open()
        second = self._open()
        self._issue(first)
        with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "HEAD_ROLLBACK_OR_FORK"):
            runtime_module.read_physical_arvan_s3_four_role_live_iam_witness_ledger_state(second)
        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError):
            self._issue(first)
        records = self._ledger_dir() / runtime_module._RECORDS_DIRECTORY
        first_record = next(records.iterdir())
        duplicate = records / ("00000000000000000001-" + "e" * 64 + ".json")
        duplicate.write_bytes(first_record.read_bytes())
        os.chmod(duplicate, 0o600)
        fresh = runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime
        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError):
            fresh(self.config)

        # A second root-owned state shows that flock serializes concurrent
        # writers. One stale runtime may win, while the other must fail rather
        # than issue a second live nonce from an obsolete head.
        concurrent_tmp = tempfile.TemporaryDirectory()
        try:
            concurrent_root = Path(concurrent_tmp.name)
            os.chmod(concurrent_root, 0o700)
            concurrent_config = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=concurrent_root, evidence_binding=self.binding, enabled=True
            )
            runtimes = [
                runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(concurrent_config),
                runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(concurrent_config),
            ]
            successes: list[object] = []
            failures: list[BaseException] = []

            def worker(index: int) -> None:
                try:
                    successes.append(
                        runtime_module.issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
                            runtime=runtimes[index],
                            nonce=("a" if index == 0 else "b") * 64,
                            issued_at=NOW,
                            expires_at=NOW + timedelta(minutes=5),
                            witness_signer=self.witness,
                        )
                    )
                except BaseException as exc:  # test captures the exact fail-closed race loser.
                    failures.append(exc)

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(1, len(successes))
            self.assertEqual(1, len(failures))
            self.assertIsInstance(failures[0], runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError)
        finally:
            concurrent_tmp.cleanup()

    def test_active_runtime_detects_an_actual_tail_rollback(self) -> None:
        current = self._open()
        self._issue(current)
        runtime_module.expire_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce(
            runtime=current,
            nonce=NONCE,
            retired_at=NOW + timedelta(minutes=5),
        )
        record_tail = sorted((self._ledger_dir() / runtime_module._RECORDS_DIRECTORY).iterdir())[-1]
        head_tail = sorted((self._ledger_dir() / runtime_module._HEADS_DIRECTORY).iterdir())[-1]
        # This simulates a storage rollback attack in a temporary test root;
        # production code never deletes or repairs either immutable record.
        record_tail.unlink()
        head_tail.unlink()
        with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "HEAD_ROLLBACK_OR_FORK"):
            runtime_module.read_physical_arvan_s3_four_role_live_iam_witness_ledger_state(current)

    def test_disabled_nonroot_symlink_and_outside_root_are_rejected(self) -> None:
        disabled = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
            state_root=self.state_root, evidence_binding=self.binding
        )
        with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "DISABLED"):
            runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(disabled)
        with mock.patch.object(runtime_module.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError, "ROOT_REQUIRED"):
                self._open()
        link_parent = self.state_root / "link-parent"
        link_target = self.state_root / "target"
        link_target.mkdir(mode=0o700)
        link_parent.symlink_to(link_target, target_is_directory=True)
        linked = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
            state_root=link_parent, evidence_binding=self.binding, enabled=True
        )
        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError):
            runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(linked)
        outside = runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
            state_root=self.state_root / "..", evidence_binding=self.binding, enabled=True
        )
        with self.assertRaises(runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError):
            runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(outside)

    def test_source_has_no_network_sdk_or_direct_site_api(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {"boto3", "botocore", "socket", "subprocess", "requests", "urllib", "http", "paramiko"}
        )
        self.assertNotIn("boto3", source)
        self.assertNotIn("direct_site", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
