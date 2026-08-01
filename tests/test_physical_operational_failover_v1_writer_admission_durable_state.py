"""Focused adversarial tests for root-local V1 writer-admission state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_durable_state as subject


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_operational_failover_v1_writer_admission_durable_state.py"
)


@dataclass(frozen=True)
class _Evidence:
    cluster_id: str = "gold-trade-three-site"
    holder_site: str = "webapp_fi"
    writer_epoch: int = 7
    writer_lease_id: str = "writer-lease-0007"
    release_sha: str = RELEASE_SHA
    generation_id: str = "physical-generation-0007"
    evidence_id: str = "witness-evidence-0001"
    revalidation_id: str = "revalidation-id-0001"
    issued_at: datetime = NOW - timedelta(seconds=10)
    expires_at: datetime = NOW + timedelta(seconds=60)


class _Revalidator:
    def __init__(self, *results: object) -> None:
        self._results = list(results)
        self.requests: list[admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest] = []

    def revalidate_writer_term(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
    ) -> object:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("unexpected revalidation")
        return self._results.pop(0)


class _Checkpoint:
    """Separate monotonic test double; the state tree is not its authority."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str, str], tuple[int, int, str, str, str]] = {}
        self.calls: list[tuple[str, str, str, int, int, str, str, str]] = []

    def attest_v1_writer_admission_state(self, **kwargs: object) -> None:
        key = (
            kwargs["binding_sha256"],
            kwargs["writer_admission_schema"],
            kwargs["config_identity_sha256"],
        )
        value = (
            kwargs["revision"],
            kwargs["fence_generation"],
            kwargs["previous_record_sha256"],
            kwargs["state_sha256"],
            kwargs["record_sha256"],
        )
        if (
            not all(type(item) is str for item in key)
            or type(value[0]) is not int
            or type(value[1]) is not int
            or any(type(item) is not str for item in value[2:])
        ):
            raise RuntimeError("invalid checkpoint input")
        self.calls.append((*key, *value))
        previous = self.states.get(key)
        zero = "0" * 64
        if previous is None:
            if value != (0, 0, zero, zero, zero):
                raise RuntimeError("checkpoint must begin with empty head")
            self.states[key] = value
            return
        if value == previous:
            return
        if (
            value[0] == previous[0] + 1
            and previous[1] <= value[1] <= previous[1] + 1
            and value[2] == previous[4]
            and value[3] != previous[3]
            and value[4] != previous[4]
        ):
            self.states[key] = value
            return
        raise RuntimeError("checkpoint rejected rollback or divergent branch")


class PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site",
            local_site="webapp_fi",
            release_sha=RELEASE_SHA,
            generation_id="physical-generation-0007",
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id="root-runtime-instance-0001",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.store_config = subject.RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig(
            enabled=True,
            writer_admission_config=self.writer_config,
        )
        self.checkpoint = _Checkpoint()
        self._temporary = tempfile.TemporaryDirectory(
            prefix="v1-writer-admission-state-",
            dir=Path(__file__).resolve().parents[1],
        )
        self.state_root = Path(self._temporary.name) / "state"
        self.state_root.mkdir(mode=0o700)
        self.state_root.chmod(0o700)
        self._fixed_root = subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT = self.state_root
        self.store = subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore(
            self.store_config,
            rollback_checkpoint=self.checkpoint,
        )

    def tearDown(self) -> None:
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT = self._fixed_root
        self._temporary.cleanup()

    def state(self) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        return admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )

    def evidence(self, **overrides: object) -> _Evidence:
        values: dict[str, object] = {}
        values.update(overrides)
        return replace(_Evidence(), **values)

    def transition(
        self,
        *,
        state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
        config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig | None = None,
        evidence: _Evidence | None = None,
        revalidation_id: str = "revalidation-id-0001",
        now: datetime = NOW,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition:
        result = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config if config is None else config,
            state=state,
            evidence_revalidator=_Revalidator(
                self.evidence(revalidation_id=revalidation_id)
                if evidence is None
                else evidence
            ),
            revalidation_id=revalidation_id,
            now=now,
        )
        self.assertIsNotNone(result)
        assert result is not None
        return result

    def activate(
        self,
        *,
        state: admission.PhysicalOperationalFailoverV1WriterAdmissionState | None = None,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        transition = self.transition(state=self.state() if state is None else state)
        self.assertTrue(self.store.persist_state_transition(transition=transition))
        return transition.next_state

    def test_default_off_and_nonroot_fail_before_any_storage_use(self) -> None:
        disabled = subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore(
            subject.RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig(),
            rollback_checkpoint=None,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "DURABLE_STATE_DISABLED",
        ):
            disabled.read_current_structural_state()
        self.assertEqual(list(self.state_root.iterdir()), [])

        with mock.patch.object(subject.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(
                subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
                "ROOT_RUNTIME_REQUIRED",
            ):
                self.store.read_current_structural_state()
        self.assertEqual(list(self.state_root.iterdir()), [])

        missing_checkpoint = subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore(
            self.store_config,
            rollback_checkpoint=None,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "CHECKPOINT_MISSING",
        ):
            missing_checkpoint.read_current_structural_state()
        self.assertEqual(list(self.state_root.iterdir()), [])

    def test_first_root_restore_persists_a_fresh_only_structural_state(self) -> None:
        restored = self.store.restore_for_runtime(now=NOW)
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.fence_generation, 1)
        self.assertTrue(restored.requires_fresh_witness_revalidation)
        self.assertIsNone(restored.revalidated_runtime_instance_id)
        raw = self.store.read_current_structural_state()
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw.revision, restored.revision)
        self.assertIsNone(raw._capability)

    def test_transition_persists_then_root_restore_forces_fresh_revalidation(self) -> None:
        active = self.activate()
        raw = self.store.read_current_structural_state()
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertIsNone(raw._capability)
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "STATE_UNATTESTED",
        ):
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=self.writer_config,
                state=raw,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=1),
            )

        restored = self.store.restore_for_runtime(now=NOW + timedelta(seconds=2))
        self.assertEqual(restored.revision, active.revision + 1)
        self.assertEqual(restored.fence_generation, active.fence_generation + 1)
        self.assertTrue(restored.requires_fresh_witness_revalidation)
        self.assertIsNone(restored.revalidated_runtime_instance_id)
        with self.assertRaisesRegex(
            admission.PhysicalOperationalFailoverV1WriterAdmissionError,
            "FRESH_REVALIDATION_REQUIRED",
        ):
            admission.begin_physical_operational_failover_v1_writer_operation(
                config=self.writer_config,
                state=restored,
                operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                now=NOW + timedelta(seconds=3),
            )

        fresh = self.transition(
            state=restored,
            evidence=self.evidence(
                evidence_id="witness-evidence-0002",
                revalidation_id="revalidation-id-0002",
                issued_at=NOW + timedelta(seconds=3),
                expires_at=NOW + timedelta(seconds=70),
            ),
            revalidation_id="revalidation-id-0002",
            now=NOW + timedelta(seconds=4),
        )
        self.assertTrue(self.store.persist_state_transition(transition=fresh))
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=fresh.next_state,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW + timedelta(seconds=5),
        )
        self.assertIsNotNone(operation)

    def test_exact_prior_revision_fence_and_state_digest_cas_rejects_stale_transition(self) -> None:
        transition = self.transition(state=self.state())
        self.assertTrue(
            self.store.compare_and_swap_state_transition(
                expected_revision=0,
                expected_fence_generation=0,
                transition=transition,
            )
        )
        self.assertFalse(self.store.persist_state_transition(transition=transition))
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "EXPECTED_HEAD_INVALID",
        ):
            self.store.compare_and_swap_state_transition(
                expected_revision=1,
                expected_fence_generation=0,
                transition=transition,
            )

    def test_persist_writer_admission_only_advances_local_state(self) -> None:
        active = self.activate()
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT,
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(operation)
        assert operation is not None
        result = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=active,
            operation=operation,
            now=NOW + timedelta(seconds=2),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(self.store.persist_writer_admission(admission=result))
        raw = self.store.read_current_structural_state()
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw.revision, active.revision + 1)
        self.assertFalse(getattr(raw, "writer_authorized", False))
        self.assertFalse(getattr(raw, "promotion_authorized", False))
        self.assertFalse(getattr(raw, "traffic_authorized", False))

    def test_runtime_change_cannot_persist_old_runtime_and_must_restore_then_revalidate(self) -> None:
        active = self.activate()
        fence = admission.fence_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=active,
            fence_reason="runtime-restart-fence",
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(fence)
        assert fence is not None
        replacement_config = replace(
            self.writer_config,
            runtime_instance_id="root-runtime-instance-0002",
        )
        replacement_store = subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore(
            replace(self.store_config, writer_admission_config=replacement_config),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "TRANSITION_INVALID",
        ):
            replacement_store.persist_state_transition(transition=fence)
        restored = replacement_store.restore_for_runtime(now=NOW + timedelta(seconds=2))
        self.assertTrue(restored.requires_fresh_witness_revalidation)
        self.assertIsNone(restored.revalidated_runtime_instance_id)
        fresh = self.transition(
            state=restored,
            config=replacement_config,
            evidence=self.evidence(
                evidence_id="witness-evidence-0002",
                revalidation_id="revalidation-id-0002",
                writer_epoch=8,
                writer_lease_id="writer-lease-0008",
                issued_at=NOW + timedelta(seconds=3),
                expires_at=NOW + timedelta(seconds=70),
            ),
            revalidation_id="revalidation-id-0002",
            now=NOW + timedelta(seconds=4),
        )
        self.assertTrue(replacement_store.persist_state_transition(transition=fresh))

    def test_checkpoint_rejects_privileged_current_record_rollback(self) -> None:
        active = self.activate()
        old_payload = (self.state_root / "current.json").read_bytes()
        fence = admission.fence_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=active,
            fence_reason="local-safety-fence",
            now=NOW + timedelta(seconds=1),
        )
        self.assertIsNotNone(fence)
        assert fence is not None
        self.assertTrue(self.store.persist_state_transition(transition=fence))
        (self.state_root / "current.json").write_bytes(old_payload)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "CHECKPOINT_REJECTED",
        ):
            self.store.read_current_structural_state()

    def test_binding_configuration_symlink_and_temp_residue_fail_closed(self) -> None:
        self.activate()
        incompatible = subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore(
            replace(
                self.store_config,
                writer_admission_config=replace(self.writer_config, safety_margin_seconds=6),
            ),
            rollback_checkpoint=self.checkpoint,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "BINDING_MISMATCH",
        ):
            incompatible.read_current_structural_state()

        (self.state_root / "current.json").unlink()
        os.symlink("/etc/passwd", self.state_root / "current.json")
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "ROOT_CHILD_UNSAFE",
        ):
            self.store.read_current_structural_state()

    def test_unknown_temp_residue_and_forged_unattested_transition_fail_closed(self) -> None:
        self.store.read_current_structural_state()
        (self.state_root / ".current-forged.tmp").write_bytes(b"forged")
        os.chmod(self.state_root / ".current-forged.tmp", 0o600)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "TEMP_RESIDUE",
        ):
            self.store.read_current_structural_state()

        # Use a fresh root after exercising residue detection so the forged
        # transition assertion is independent of the storage refusal above.
        second_root = Path(self._temporary.name) / "second-state"
        second_root.mkdir(mode=0o700)
        second_root.chmod(0o700)
        subject.FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT = second_root
        forged_prior = admission.PhysicalOperationalFailoverV1WriterAdmissionState(
            schema=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
            binding=self.binding,
            revision=0,
            highest_writer_epoch=0,
            active_term=None,
            revalidated_runtime_instance_id=None,
            clock_floor=None,
            fence_generation=0,
            fenced=True,
            fence_reason="startup-requires-fresh-witness",
            requires_fresh_witness_revalidation=True,
        )
        forged_next = replace(forged_prior, revision=1)
        forged_transition = admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition(
            kind="witness_revalidation",
            prior_state=forged_prior,
            next_state=forged_next,
        )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
            "TRANSITION_INVALID",
        ):
            self.store.persist_state_transition(transition=forged_transition)

    def test_static_boundary_has_no_database_network_or_campaign_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(
            imported
            & {
                "boto3",
                "botocore",
                "requests",
                "socket",
                "subprocess",
                "sqlite3",
                "sqlalchemy",
                "urllib",
            }
        )
        self.assertNotIn("physical_wal_v2", source)
        self.assertNotIn("physical_full_matrix_v4", source)
        self.assertNotIn("promotion_authorized=True", source)
        self.assertNotIn("writer_authorized=True", source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("fdatasync", source)
        self.assertIn("os.rename", source)
