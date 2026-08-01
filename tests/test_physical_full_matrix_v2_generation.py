from __future__ import annotations

from datetime import datetime, timezone
import ast
import inspect
from unittest.mock import patch
import unittest
from uuid import uuid4

from core import physical_full_matrix_execution_driver_v3 as driver_v3
from core import physical_full_matrix_v2_ack_chain as ack_chain_module
from core import physical_full_matrix_v2_campaign_readiness as readiness_module
from core.physical_full_matrix_v2_ack_chain import (
    PhysicalFullMatrixV2AckChainConfig,
    PhysicalFullMatrixV2AckChainError,
    PhysicalFullMatrixV2AckChainInputs,
    mint_verified_physical_full_matrix_v2_ack_chain,
)
from core.physical_full_matrix_v2_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_BLOCKED,
    PhysicalFullMatrixV2CampaignBinding,
    PhysicalFullMatrixV2CampaignInputs,
    PhysicalFullMatrixV2CampaignReadinessConfig,
    assess_physical_full_matrix_v2_campaign_readiness,
)
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckConfig,
    VerifiedPhysicalWalV2RemoteAckEvidence,
    VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckRequest,
)
from core.physical_wal_v2_remote_ack_receiver_ledger import (
    PhysicalWalV2RemoteAckReceiverLedgerConfig,
    VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
)
from core.physical_wal_v2_strict_remote_ack_writer_response import (
    PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation,
)
from core.physical_full_matrix_v2_recovery_evidence import (
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
)


NOW = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)


def _uninitialised(cls: type[object]) -> object:
    """A type-correct but untrusted capability for boundary-order tests."""

    return object.__new__(cls)


class PhysicalFullMatrixV2GenerationTests(unittest.TestCase):
    def _v3_binding(
        self,
        *,
        source_site: str = "webapp_fi",
        destination_site: str = "webapp_ir",
        writer_epoch: int = 7,
        writer_lease_id: str = "v2-lease-0001",
        term: str = "f" * 64,
        route: str = "d" * 64,
    ) -> driver_v3.PhysicalFullMatrixV3ExecutionBinding:
        return driver_v3.PhysicalFullMatrixV3ExecutionBinding(
            campaign_id="physical-v2-campaign-0001",
            release_sha="a" * 40,
            release_manifest_sha256="b" * 64,
            readiness_binding_sha256="c" * 64,
            route_commitment_sha256=route,
            four_role_binding_sha256="e" * 64,
            writer_holder_site=source_site,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            witnessed_term_proof_sha256=term,
            source_site=source_site,
            destination_site=destination_site,
        )

    def _ack_chain_config(self) -> PhysicalFullMatrixV2AckChainConfig:
        remote = PhysicalWalV2RemoteAckConfig(enabled=True)
        ledger = PhysicalWalV2RemoteAckReceiverLedgerConfig(
            remote_ack_config=remote,
            enabled=True,
        )
        strict = PhysicalWalV2StrictRemoteAckWriterResponseConfig(
            remote_ack_config=remote,
            receiver_ledger_config=ledger,
            enabled=True,
        )
        return PhysicalFullMatrixV2AckChainConfig(
            remote_ack_config=remote,
            receiver_ledger_config=ledger,
            strict_writer_config=strict,
            enabled=True,
        )

    def test_raw_process_local_receiver_ledger_can_never_mint_v2_readiness(self) -> None:
        """A local IR ledger object is not a FI transfer or readiness permit."""

        inputs = PhysicalFullMatrixV2AckChainInputs(
            recovery_evidence=_uninitialised(VerifiedPhysicalFullMatrixV2RecoveryEvidence),  # type: ignore[arg-type]
            source_request=_uninitialised(VerifiedPhysicalWalV2RemoteAckRequest),  # type: ignore[arg-type]
            receiver_recovery_evidence=_uninitialised(VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence),  # type: ignore[arg-type]
            remote_ack_evidence=_uninitialised(VerifiedPhysicalWalV2RemoteAckEvidence),  # type: ignore[arg-type]
            receiver_ledger_receipt=_uninitialised(VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt),  # type: ignore[arg-type]
            strict_writer_response=_uninitialised(VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation),  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(
            PhysicalFullMatrixV2AckChainError,
            "WITNESS_MEDIATED_ROUNDTRIP_REQUIRED",
        ):
            mint_verified_physical_full_matrix_v2_ack_chain(
                config=self._ack_chain_config(),
                inputs=inputs,
                now=NOW,
            )

    def test_v2_readiness_rejects_legacy_artifacts_without_parsing_them(self) -> None:
        binding = PhysicalFullMatrixV2CampaignBinding(
            campaign_id="physical-v2-campaign-0001",
            release_sha="a" * 40,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            route_commitment_sha256="b" * 64,
            four_role_binding_sha256="c" * 64,
            writer_holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="v2-lease-0001",
            witnessed_term_proof_sha256="d" * 64,
        )
        report = assess_physical_full_matrix_v2_campaign_readiness(
            PhysicalFullMatrixV2CampaignReadinessConfig(
                binding=binding,
                ack_chain_config=self._ack_chain_config(),
                enabled=True,
            ),
            PhysicalFullMatrixV2CampaignInputs(
                legacy_runner_artifacts="production_full_matrix_runner_plan_v1"
            ),
            now=NOW,
        )
        self.assertEqual(PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_BLOCKED, report.status)
        self.assertEqual(("legacy-v1-artifact-rejected",), report.reason_codes)
        self.assertEqual((), report.observed_slots)

    def test_v3_catalog_and_plan_are_separate_from_v1_generation(self) -> None:
        binding = self._v3_binding()
        config = driver_v3.PhysicalFullMatrixV3ExecutionConfig(
            binding=binding,
            readiness=None,
            run_id=uuid4(),
            enabled=True,
        )
        # Planning is deliberately non-effectful.  Patch only the owning V2
        # provenance check so this test can exercise the standalone catalog;
        # the real boundary remains unreachable until the Witness bridge is
        # implemented and mints a V2 readiness capability.
        with patch.object(driver_v3, "_validate_readiness"):
            plan = driver_v3.build_physical_full_matrix_v3_execution_plan(config=config)
        self.assertIs(plan, driver_v3.require_physical_full_matrix_v3_execution_plan(plan))
        self.assertEqual(
            "gold-trade-physical-full-matrix-v3-plan-v1",
            __import__("json").loads(plan.canonical_plan)["schema"],
        )
        self.assertTrue(all("v2" in phase.name for phase in plan.phases))
        profiles = {phase.name: phase.transport_profile for phase in plan.phases}
        self.assertEqual(
            "fi-v2-witness-roundtrip-strict-ack-v1",
            profiles["normal-fi-writer-v2-strict-ack-matrix"],
        )
        self.assertEqual(
            "ir-v2-witness-roundtrip-strict-ack-v1",
            profiles["ir-writer-v2-strict-ack-matrix"],
        )
        self.assertNotIn("object-storage-ack-v1", tuple(profiles.values()))
        source = inspect.getsource(driver_v3)
        imported_modules = {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("core.physical_full_matrix_execution_driver", imported_modules)
        self.assertNotIn("core.physical_full_matrix_campaign_readiness", imported_modules)
        self.assertNotIn("core.physical_wal_remote_ack", imported_modules)

    def test_new_generation_has_no_v1_or_direct_transport_imports(self) -> None:
        for module in (ack_chain_module, readiness_module, driver_v3):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                imported_modules = {
                    alias.name
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Import)
                    for alias in node.names
                } | {
                    node.module
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.ImportFrom) and node.module is not None
                }
                for forbidden in (
                    "core.physical_full_matrix_campaign_readiness",
                    "core.physical_full_matrix_execution_driver",
                    "core.physical_wal_remote_ack",
                    "core.physical_wal_remote_ack_receiver_ledger",
                    "core.physical_strict_remote_ack_writer_response",
                    "socket",
                    "subprocess",
                    "requests",
                    "boto3",
                ):
                    self.assertNotIn(forbidden, imported_modules)
                self.assertNotIn("connect(", source)

    def test_v3_rejects_legacy_artifacts_before_plan_materialization(self) -> None:
        binding = self._v3_binding()
        config = driver_v3.PhysicalFullMatrixV3ExecutionConfig(
            binding=binding,
            readiness=None,
            run_id=uuid4(),
            enabled=True,
            legacy_runner_artifacts=("staging_two_server_full_matrix_runner_v1",),
        )
        with patch.object(driver_v3, "_validate_readiness"):
            with self.assertRaisesRegex(
                driver_v3.PhysicalFullMatrixV3ExecutionDriverError,
                "LEGACY_RUNNER_REJECTED",
            ):
                driver_v3.build_physical_full_matrix_v3_execution_plan(config=config)

    def test_promotion_requires_a_new_reverse_v2_term_not_normal_precredit(self) -> None:
        predecessor = driver_v3._binding_snapshot(self._v3_binding(), direction=None)
        promote = next(
            item
            for item in driver_v3._phase_snapshots()
            if item.name == "witness-promote-ir-v2"
        )
        with self.assertRaisesRegex(
            driver_v3.PhysicalFullMatrixV3ExecutionDriverError,
            "SUCCESSOR_REQUIRED",
        ):
            driver_v3._successor(
                None,
                predecessor=predecessor,
                phase=promote,
                now=None,
            )
        # Reusing the normal term cannot create the phase-five IR writer
        # binding, even when an attacker relabels it as a successor direction.
        precredited = driver_v3.PhysicalFullMatrixV3ReadinessEvidence(
            binding=self._v3_binding(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                writer_epoch=7,
                writer_lease_id="v2-lease-0001",
                term="f" * 64,
                route="1" * 64,
            ),
            readiness=object(),  # never read when now=None in this structural test
        )
        with self.assertRaisesRegex(
            driver_v3.PhysicalFullMatrixV3ExecutionDriverError,
            "SUCCESSOR_NON_MONOTONIC",
        ):
            driver_v3._successor(
                precredited,
                predecessor=predecessor,
                phase=promote,
                now=None,
            )
        fresh = driver_v3.PhysicalFullMatrixV3ReadinessEvidence(
            binding=self._v3_binding(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                writer_epoch=8,
                writer_lease_id="v2-lease-0002",
                term="1" * 64,
                route="2" * 64,
            ),
            readiness=object(),
        )
        successor = driver_v3._successor(
            fresh,
            predecessor=predecessor,
            phase=promote,
            now=None,
        )
        self.assertEqual(("webapp_ir", "webapp_fi"), (successor.source_site, successor.destination_site))
        self.assertEqual(8, successor.writer_epoch)

    def test_phase_five_requires_fresh_reverse_readiness_evidence(self) -> None:
        reverse = driver_v3._binding_snapshot(
            self._v3_binding(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                writer_epoch=8,
                writer_lease_id="v2-lease-0002",
                term="1" * 64,
                route="2" * 64,
            ),
            direction=None,
        )
        phase = next(
            item
            for item in driver_v3._phase_snapshots()
            if item.name == "ir-writer-v2-strict-ack-matrix"
        )
        request = driver_v3._request(
            snapshot=driver_v3._PlanSnapshot(
                canonical_plan=b"",
                plan_sha256="3" * 64,
                run_id=uuid4(),
                binding=reverse,
                phases=driver_v3._phase_snapshots(),
                maximum_oracle_age_seconds=120,
            ),
            phase=phase,
            binding=reverse,
        )
        oracle = driver_v3.PhysicalFullMatrixV3PhaseOracle(
            schema=driver_v3.PHYSICAL_FULL_MATRIX_V3_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=phase.name,
            oracle=phase.oracle,
            transport_profile=phase.transport_profile,
            evidence_sha256="4" * 64,
            observed_at=NOW,
            readiness_evidence=None,
        )
        with self.assertRaisesRegex(
            driver_v3.PhysicalFullMatrixV3ExecutionDriverError,
            "PHASE_READINESS_REQUIRED",
        ):
            driver_v3._validate_oracle(
                value=oracle,
                request=request,
                phase=phase,
                now=NOW,
                maximum_age=120,
            )


if __name__ == "__main__":
    unittest.main()
