"""Adversarial local-state tests for the isolated V2R replay foundation."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_contract as v2r
from core import physical_wal_v2r_witness_roundtrip_durable_anti_replay as registry


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_durable_anti_replay.py"
)


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _MonotonicCheckpoint:
    """Test double for the separate root-owned monotonic persistence seam."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str, str, str], tuple[int, str, str]] = {}
        self.calls: list[tuple[str, str, str, str, int, str, str]] = []

    def attest_v2r_roundtrip_anti_replay_state(
        self,
        *,
        binding_sha256: str,
        receiving_role: str,
        state_namespace: str,
        reservation_prefix: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None:
        key = (binding_sha256, receiving_role, state_namespace, reservation_prefix)
        prior = self.states.get(key)
        self.calls.append((*key, sequence, previous_record_sha256, record_sha256))
        if prior is None:
            if (
                sequence != 0
                or previous_record_sha256 != "0" * 64
                or record_sha256 != "0" * 64
            ):
                raise RuntimeError("checkpoint must begin at empty state")
            self.states[key] = (sequence, previous_record_sha256, record_sha256)
            return
        if prior == (sequence, previous_record_sha256, record_sha256):
            return
        if (
            sequence == prior[0] + 1
            and previous_record_sha256 == prior[2]
            and record_sha256 != prior[2]
        ):
            self.states[key] = (sequence, previous_record_sha256, record_sha256)
            return
        raise RuntimeError("checkpoint rejected rollback or branch")


class PhysicalWalV2rWitnessRoundtripDurableAntiReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = Ed25519PrivateKey.generate()
        self.witness_forward = Ed25519PrivateKey.generate()
        self.fi = Ed25519PrivateKey.generate()
        self.witness_return = Ed25519PrivateKey.generate()
        self.roundtrip_config = v2r.PhysicalWalV2rWitnessRoundtripConfig(
            cluster_id="gold-trade-prod",
            release_sha="a" * 40,
            stream_generation_id="v2r-generation-000001",
            route_commitment_sha256="1" * 64,
            reverse_frontier_sha256="2" * 64,
            recovery_frontier_sha256="3" * 64,
            blob_frontier_sha256="4" * 64,
            v2r_iam_policy_sha256="5" * 64,
            normal_v2_protocol_domain="gold-trade-physical-wal-v2-normal-v1",
            normal_v2_mailbox_prefix="physical-wal-v2-normal/",
            normal_v2_iam_policy_sha256="6" * 64,
            normal_v2_public_key_sha256s=("7" * 64,),
            ir_export_public_key=_public(self.ir),
            witness_forward_public_key=_public(self.witness_forward),
            fi_ack_public_key=_public(self.fi),
            witness_return_public_key=_public(self.witness_return),
            enabled=True,
        )
        self.checkpoint = _MonotonicCheckpoint()
        self.registry_config = (
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistryConfig(
                enabled=True,
                receiving_role=(
                    registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_REVERSE_INGRESS
                ),
                roundtrip_config=self.roundtrip_config,
            )
        )
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v2r-durable-anti-replay-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.state_root = Path(self._temporary.name) / "state"
        self.state_root.mkdir(mode=0o700)
        self._fixed_root = (
            registry.FIXED_PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_STATE_ROOT
        )
        registry.FIXED_PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_STATE_ROOT = (
            self.state_root
        )
        self.service = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            self.registry_config,
            rollback_checkpoint=self.checkpoint,
        )

    def tearDown(self) -> None:
        registry.FIXED_PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_STATE_ROOT = (
            self._fixed_root
        )
        self._temporary.cleanup()

    @property
    def _namespace(self) -> Path:
        return self.state_root / "witness-reverse-ingress"

    def _reserve(
        self,
        correlation_id: str,
        *,
        service: registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry | None = None,
        config: v2r.PhysicalWalV2rWitnessRoundtripConfig | None = None,
        stage: str = "export",
    ) -> registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationReceipt:
        selected = self.service if service is None else service
        return selected.reserve_before_receive(
            roundtrip_config=self.roundtrip_config if config is None else config,
            stage=stage,
            correlation_id=correlation_id,
        )

    def test_reserves_exact_receiver_correlation_before_acceptance_without_authority(self) -> None:
        receipt = self._reserve("v2r-correlation-000001")

        self.assertEqual(1, receipt.reservation_sequence)
        self.assertEqual(
            registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_REVERSE_INGRESS,
            receipt.receiving_role,
        )
        self.assertEqual("witness", receipt.local_site)
        self.assertEqual("witness-v2r-reverse-ingress", receipt.local_role)
        self.assertEqual("export", receipt.stage)
        self.assertEqual("witness-reverse-ingress", receipt.state_namespace)
        self.assertEqual(
            hashlib.sha256(b"v2r-correlation-000001").hexdigest(),
            receipt.correlation_id_sha256,
        )
        for flag in (
            "object_storage_election_authority",
            "object_storage_lease_authority",
            "object_storage_writer_authority",
            "writer_authorized",
            "traffic_authorized",
            "promotion_authorized",
            "execution_authorized",
        ):
            self.assertFalse(getattr(receipt, flag))
        self.assertEqual(2, len(self.checkpoint.calls))
        self.assertEqual(0, self.checkpoint.calls[0][-3])
        self.assertEqual(1, self.checkpoint.calls[1][-3])
        self.assertEqual(0o700, self._namespace.stat().st_mode & 0o777)
        self.assertEqual(0o700, (self._namespace / "reservations").stat().st_mode & 0o777)
        self.assertEqual(0o600, (self._namespace / "binding.json").stat().st_mode & 0o777)
        self.assertEqual(0o600, (self._namespace / "current.json").stat().st_mode & 0o777)
        self.assertEqual(
            0o600,
            (self.state_root / "v2r-configuration-binding.json").stat().st_mode & 0o777,
        )

    def test_reuse_is_rejected_and_survives_a_restart(self) -> None:
        first = self._reserve("v2r-correlation-000002")
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "CORRELATION_REUSED",
        ):
            self._reserve("v2r-correlation-000002")

        restarted = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            self.registry_config,
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "CORRELATION_REUSED",
        ):
            self._reserve("v2r-correlation-000002", service=restarted)
        second = self._reserve("v2r-correlation-000003", service=restarted)

        self.assertEqual(1, first.reservation_sequence)
        self.assertEqual(2, second.reservation_sequence)
        self.assertEqual(2, len(list((self._namespace / "reservations").glob("*.json"))))

    def test_only_the_four_fixed_receivers_are_available_and_stage_is_bound(self) -> None:
        expected = (
            (
                registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_REVERSE_INGRESS,
                "export",
                "witness-reverse-ingress",
            ),
            (
                registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_FI_RECOVERY_INBOX,
                "forward",
                "wa-fi-recovery-inbox",
            ),
            (
                registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_FI_ACK_INGRESS,
                "ack",
                "witness-fi-ack-ingress",
            ),
            (
                registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_IR_RETURN_INBOX,
                "return",
                "wa-ir-return-inbox",
            ),
        )
        for index, (role, stage, namespace) in enumerate(expected, start=1):
            with self.subTest(role=role):
                service = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
                    replace(self.registry_config, receiving_role=role),
                    rollback_checkpoint=self.checkpoint,
                )
                receipt = self._reserve(
                    "v2r-correlation-fixed-%03d" % index,
                    service=service,
                    stage=stage,
                )
                self.assertEqual(role, receipt.receiving_role)
                self.assertEqual(namespace, receipt.state_namespace)
                self.assertTrue((self.state_root / namespace / "reservations").is_dir())

        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "RECEIVING_ROLE_INVALID",
        ):
            self._reserve(
                "v2r-correlation-unknown-001",
                service=registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
                    replace(self.registry_config, receiving_role="wa-fi-direct-ir-inbox"),
                    rollback_checkpoint=self.checkpoint,
                ),
            )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "STAGE_ROLE_MISMATCH",
        ):
            self._reserve("v2r-correlation-stage-001", stage="forward")

    def test_chain_correlation_is_receiver_local_but_cross_role_substitution_is_rejected(self) -> None:
        correlation = "v2r-correlation-chain-001"
        first = self._reserve(correlation)
        forward_service = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            replace(
                self.registry_config,
                receiving_role=(
                    registry.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_FI_RECOVERY_INBOX
                ),
            ),
            rollback_checkpoint=self.checkpoint,
        )
        second = self._reserve(
            correlation,
            service=forward_service,
            stage="forward",
        )
        self.assertEqual(1, first.reservation_sequence)
        self.assertEqual(1, second.reservation_sequence)
        self.assertNotEqual(first.state_namespace, second.state_namespace)
        self.assertNotEqual(first.stage, second.stage)
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "STAGE_ROLE_MISMATCH",
        ):
            self._reserve(correlation, service=forward_service, stage="export")

    def test_exact_v2r_config_identity_and_root_cross_role_pin_reject_switches(self) -> None:
        self._reserve("v2r-correlation-config-001")
        changed = replace(self.roundtrip_config, release_sha="b" * 40)
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "V2R_CONFIGURATION_MISMATCH",
        ):
            self._reserve("v2r-correlation-config-002", config=changed)

        switched = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            replace(self.registry_config, roundtrip_config=changed),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "ROOT_CONFIGURATION_MISMATCH",
        ):
            self._reserve(
                "v2r-correlation-config-003",
                service=switched,
                config=changed,
            )

    def test_disabled_nonroot_missing_checkpoint_and_disabled_v2r_fail_before_state_access(self) -> None:
        disabled = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            replace(self.registry_config, enabled=False),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "ANTI_REPLAY_DISABLED",
        ):
            self._reserve("v2r-correlation-disabled-01", service=disabled)
        self.assertEqual([], list(self.state_root.iterdir()))

        missing = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            self.registry_config,
            rollback_checkpoint=None,
        )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "CHECKPOINT_MISSING",
        ):
            self._reserve("v2r-correlation-missing-001", service=missing)
        self.assertEqual([], list(self.state_root.iterdir()))

        with mock.patch.object(registry.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                self._reserve("v2r-correlation-nonroot-01")
        self.assertEqual([], list(self.state_root.iterdir()))

        disabled_v2r = registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry(
            replace(self.registry_config, roundtrip_config=replace(self.roundtrip_config, enabled=False)),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "V2R_CONFIGURATION_INVALID",
        ):
            self._reserve("v2r-correlation-disabled-v2r", service=disabled_v2r)
        self.assertEqual([], list(self.state_root.iterdir()))

    def test_current_pointer_and_whole_tree_rollback_fail_closed(self) -> None:
        self._reserve("v2r-correlation-rollback-01")
        old_current = (self._namespace / "current.json").read_bytes()
        self._reserve("v2r-correlation-rollback-02")
        (self._namespace / "current.json").write_bytes(old_current)
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "CURRENT_ROLLBACK",
        ):
            self._reserve("v2r-correlation-rollback-03")

        records = self._namespace / "reservations"
        for path in records.glob("00000000000000000002-*.json"):
            path.unlink()
        (self._namespace / "current.json").write_bytes(old_current)
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "CHECKPOINT_REJECTED",
        ):
            self._reserve("v2r-correlation-rollback-04")

    def test_symlink_and_temporary_residue_are_never_recovered_or_ignored(self) -> None:
        namespace = self._namespace
        namespace.mkdir(mode=0o700)
        os.symlink("/tmp", namespace / "reservations")
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "RECORDS_UNSAFE",
        ):
            self._reserve("v2r-correlation-symlink-01")
        (namespace / "reservations").unlink()
        self._reserve("v2r-correlation-clean-001")
        (self._namespace / ".current-interrupted.tmp").write_bytes(b"interrupted")
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "TEMP_RESIDUE",
        ):
            self._reserve("v2r-correlation-temp-0001")
        (self._namespace / ".current-interrupted.tmp").unlink()
        (self._namespace / "reservations" / ".record-interrupted.tmp").write_bytes(
            b"interrupted"
        )
        with self.assertRaisesRegex(
            registry.PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
            "TEMP_RESIDUE",
        ):
            self._reserve("v2r-correlation-temp-0002")

    def test_source_is_local_only_and_documents_the_unintegrated_guard_gap(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
            for module in [node.module]
        )
        self.assertTrue(
            imported
            <= {
                "__future__",
                "collections",
                "contextlib",
                "dataclasses",
                "fcntl",
                "hashlib",
                "json",
                "os",
                "pathlib",
                "re",
                "secrets",
                "stat",
                "typing",
                "core",
            }
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("physical_full_matrix_v4", source)
        self.assertNotIn("physical_wal_v2_witness_roundtrip_", source)
        self.assertIn("Production integration", registry.__doc__ or "")
        self.assertIn("ReplayGuard", registry.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
