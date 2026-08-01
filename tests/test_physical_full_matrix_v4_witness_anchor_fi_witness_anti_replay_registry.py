"""Focused local-state tests for the V4 FI↔Witness anti-replay foundation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import ast
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from core import physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry as registry
from core import physical_full_matrix_v4_witness_anchor_wire as wire


RUN_ID = UUID("133c494c-1cbc-4ab2-9b54-780f74d08c91")
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _identity(*, run_id: UUID = RUN_ID) -> wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
    return wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
        schema=wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA,
        journal_binding_sha256=_hash("anti-replay-journal"),
        baseline_plan_binding_sha256=_hash("anti-replay-baseline"),
        run_id=run_id,
        plan_sha256=_hash("anti-replay-plan"),
        anchor_genesis_sequence=0,
        anchor_genesis_head_sha256=_hash("anti-replay-genesis-head"),
        canonical_genesis_sha256=_hash("anti-replay-genesis"),
    )


@dataclass(frozen=True)
class _LookalikeIdentity:
    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    run_id: UUID
    plan_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    canonical_genesis_sha256: str


class _MonotonicCheckpoint:
    """Test double for the separate root-owned monotonic persistence seam."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str, str, str], tuple[int, str, str]] = {}
        self.calls: list[tuple[str, str, str, str, int, str, str]] = []

    def attest_v4_fi_witness_anti_replay_state(
        self,
        *,
        binding_sha256: str,
        role: str,
        state_namespace: str,
        reservation_prefix: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None:
        key = (binding_sha256, role, state_namespace, reservation_prefix)
        previous = self.states.get(key)
        self.calls.append((*key, sequence, previous_record_sha256, record_sha256))
        if previous is None:
            if (
                sequence != 0
                or previous_record_sha256 != "0" * 64
                or record_sha256 != "0" * 64
            ):
                raise RuntimeError("checkpoint must begin at the empty root")
            self.states[key] = (sequence, previous_record_sha256, record_sha256)
            return
        if previous == (sequence, previous_record_sha256, record_sha256):
            return
        if (
            sequence == previous[0] + 1
            and previous_record_sha256 == previous[2]
            and record_sha256 != previous[2]
        ):
            self.states[key] = (sequence, previous_record_sha256, record_sha256)
            return
        raise RuntimeError("monotonic checkpoint rejected rollback or branch")


class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = _identity()
        self.checkpoint = _MonotonicCheckpoint()
        self.config = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig(
            enabled=True,
            role=registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER,
            policy_identity=self.identity,
        )
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v4-fi-witness-anti-replay-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.state_root = Path(self._temporary.name) / "state"
        self.state_root.mkdir(mode=0o700)
        self._fixed_root = (
            registry.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT
        )
        registry.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT = (
            self.state_root
        )
        self.service = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
            self.config,
            rollback_checkpoint=self.checkpoint,
        )

    def tearDown(self) -> None:
        registry.FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT = (
            self._fixed_root
        )
        self._temporary.cleanup()

    @property
    def _namespace(self) -> Path:
        return self.state_root / "wa-fi-controller"

    def _reserve(
        self,
        label: str,
        *,
        kind: str = registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE,
        service: registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry | None = None,
        identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity | None = None,
    ) -> registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt:
        selected = self.service if service is None else service
        return selected.reserve_before_external_boundary(
            policy_identity=self.identity if identity is None else identity,
            identifier_kind=kind,
            identifier=_hash(label),
        )

    def test_reserves_before_boundary_with_closed_role_namespace_and_no_authority(self) -> None:
        receipt = self._reserve("challenge-1")

        self.assertEqual(1, receipt.reservation_sequence)
        self.assertEqual("wa-fi-controller", receipt.role)
        self.assertEqual("wa-fi-controller", receipt.state_namespace)
        self.assertEqual("wa-fi-controller-v4-anchor-reservation", receipt.reservation_prefix)
        self.assertFalse(receipt.execution_authorized)
        self.assertFalse(receipt.promotion_authorized)
        self.assertFalse(receipt.full_matrix_executed)
        self.assertEqual(2, len(self.checkpoint.calls))
        self.assertEqual(0, self.checkpoint.calls[0][-3])
        self.assertEqual(1, self.checkpoint.calls[1][-3])
        self.assertEqual(0o700, self._namespace.stat().st_mode & 0o777)
        self.assertEqual(0o700, (self._namespace / "reservations").stat().st_mode & 0o777)
        self.assertEqual(0o600, (self._namespace / "binding.json").stat().st_mode & 0o777)
        self.assertEqual(0o600, (self._namespace / "current.json").stat().st_mode & 0o777)

    def test_rejects_identifier_reuse_across_kinds_and_survives_restart(self) -> None:
        first = self._reserve("shared-identifier")
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "IDENTIFIER_REUSED",
        ):
            self._reserve(
                "shared-identifier",
                kind=registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID,
            )

        restarted = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
            self.config,
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "IDENTIFIER_REUSED",
        ):
            self._reserve("shared-identifier", service=restarted)
        second = self._reserve("fresh-after-restart", service=restarted)

        self.assertEqual(1, first.reservation_sequence)
        self.assertEqual(2, second.reservation_sequence)
        self.assertEqual(2, len(list((self._namespace / "reservations").glob("*.json"))))

    def test_roles_have_separate_fixed_namespaces_and_kind_sets(self) -> None:
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "IDENTIFIER_KIND_INVALID",
        ):
            self._reserve("unsupported", kind="not-a-v4-identifier-kind")
        first = self._reserve(
            "observation",
            kind=registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID,
        )

        witness_config = replace(
            self.config,
            role=registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS,
        )
        witness = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
            witness_config,
            rollback_checkpoint=self.checkpoint,
        )
        receipt = self._reserve(
            "observation",
            kind=registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID,
            service=witness,
        )

        self.assertEqual(1, first.reservation_sequence)
        self.assertEqual("witness", receipt.role)
        self.assertTrue((self.state_root / "witness" / "reservations").is_dir())
        self.assertTrue(self._namespace.is_dir())

    def test_rejects_policy_lookalike_and_binding_switch(self) -> None:
        lookalike = _LookalikeIdentity(**self.identity.__dict__)
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "POLICY_IDENTITY_MISMATCH",
        ):
            self.service.reserve_before_external_boundary(
                policy_identity=lookalike,  # type: ignore[arg-type]
                identifier_kind=registry.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE,
                identifier=_hash("lookalike"),
            )
        self._reserve("valid")
        switched_identity = _identity(run_id=UUID("d7d18e51-3d53-4dca-83ec-33caef746630"))
        switched = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
            replace(self.config, policy_identity=switched_identity),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "BINDING_MISMATCH",
        ):
            self._reserve("other", service=switched, identity=switched_identity)

    def test_default_off_nonroot_and_missing_checkpoint_fail_before_state_access(self) -> None:
        disabled = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
            replace(self.config, enabled=False),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "ANTI_REPLAY_DISABLED",
        ):
            self._reserve("disabled", service=disabled)
        self.assertEqual([], list(self.state_root.iterdir()))

        missing = registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry(
            self.config,
            rollback_checkpoint=None,
        )
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "CHECKPOINT_MISSING",
        ):
            self._reserve("missing", service=missing)
        self.assertEqual([], list(self.state_root.iterdir()))

        with mock.patch.object(registry.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                self._reserve("nonroot")
        self.assertEqual([], list(self.state_root.iterdir()))

    def test_stale_current_pointer_and_checkpointed_whole_tree_rollback_fail_closed(self) -> None:
        self._reserve("first")
        old_current = (self._namespace / "current.json").read_bytes()
        self._reserve("second")
        (self._namespace / "current.json").write_bytes(old_current)
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "CURRENT_ROLLBACK",
        ):
            self._reserve("third")

        # Recreate a valid-looking earlier tree.  The local chain alone can no
        # longer distinguish it, so the independent monotonic checkpoint must.
        records = self._namespace / "reservations"
        for path in records.glob("00000000000000000002-*.json"):
            path.unlink()
        (self._namespace / "current.json").write_bytes(old_current)
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "CHECKPOINT_REJECTED",
        ):
            self._reserve("fourth")

    def test_symlink_and_temporary_residue_are_never_recovered_or_ignored(self) -> None:
        namespace = self.state_root / "wa-fi-controller"
        namespace.mkdir(mode=0o700)
        os.symlink("/tmp", namespace / "reservations")
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "RECORDS_UNSAFE",
        ):
            self._reserve("symlink")
        (namespace / "reservations").unlink()
        self._reserve("clean")
        (self._namespace / ".current-interrupted.tmp").write_bytes(b"interrupted")
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "TEMP_RESIDUE",
        ):
            self._reserve("blocked-by-temp")
        (self._namespace / ".current-interrupted.tmp").unlink()
        (self._namespace / "reservations" / ".record-interrupted.tmp").write_bytes(
            b"interrupted"
        )
        with self.assertRaisesRegex(
            registry.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
            "TEMP_RESIDUE",
        ):
            self._reserve("blocked-by-record-temp")

    def test_source_has_no_transport_client_and_documents_callback_integration_gap(self) -> None:
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
        self.assertTrue(imported <= {"__future__", "collections", "contextlib", "dataclasses", "fcntl", "hashlib", "json", "os", "pathlib", "re", "secrets", "stat", "typing", "uuid", "core"})
        self.assertIn("Production integration is still required", registry.__doc__ or "")
        self.assertIn("reserve_before_external_boundary", registry.__doc__ or "")
