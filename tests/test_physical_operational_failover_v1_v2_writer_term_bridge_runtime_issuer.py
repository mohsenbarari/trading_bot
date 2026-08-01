"""Adversarial tests for the opaque two-stage V1/V2 bridge runtime issuer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import pickle
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as wire
from core import physical_operational_failover_v1_v2_writer_term_bridge as bridge
from core import physical_operational_failover_v1_v2_writer_term_bridge_runtime_issuer as subject
from core import physical_operational_failover_v1_witness_ledger as ledger
from core import physical_operational_failover_v1_witness_term_revalidator as revalidator
from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as sql
from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as strict
from tests import test_physical_operational_failover_v1_witness_term_revalidator as v1_support
from tests import test_physical_wal_v2_witness_roundtrip_contract as v2_support
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW, RELEASE


def _id(prefix: str) -> str:
    return prefix + "-" + "x" * 24


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


@dataclass(frozen=True)
class _BridgeTerm:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    proof_sha256: str
    witness_transition_id: str
    issued_at: datetime
    expires_at: datetime


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def now_utc(self) -> datetime:
        return self.now


class PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.v2_chain = v2_support.PhysicalWalV2WitnessRoundtripContractTests("runTest")
        cls.v2_chain.setUp()
        _certificate, _envelope, assertion, _issued = cls.v2_chain._full_chain()
        raw = cls.v2_chain._attestation(assertion)
        cls.v2_attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            raw,
            config=cls.v2_chain.config,
            now=NOW,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.v2_chain.tearDown()

    def setUp(self) -> None:
        self.clock = _Clock()
        self.v1_attestation_key = Ed25519PrivateKey.generate()
        self.v1_promotion_key = Ed25519PrivateKey.generate()
        self.bridge_signer = Ed25519PrivateKey.generate()
        self.v2_local_signer = Ed25519PrivateKey.generate()
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id="gold-trade-three-site-prod",
            local_site="webapp_fi",
            release_sha=RELEASE,
            generation_id=_id("bridge-generation"),
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=_id("writer-runtime"),
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.v1_config = revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=self.writer_config.runtime_instance_id,
            witness_current_term_signer_public_key=_public(self.v1_attestation_key),
            witness_promotion_signer_public_key=_public(self.v1_promotion_key),
            witness_current_term_signer_key_id=_id("v1-current-key"),
            durable_guard_id=_id("v1-durable-guard"),
            safety_margin_seconds=5,
            maximum_attestation_age_seconds=30,
            maximum_attestation_duration_seconds=90,
            maximum_reservation_duration_seconds=90,
        )
        self.v2_term = _BridgeTerm(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id=_id("bridge-writer-lease"),
            proof_sha256="b" * 64,
            # The V2 live activation must retain the Witness transition that
            # the independently verified V2 attestation carries.
            witness_transition_id=self.v2_attestation.witness_transition_id,
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=30),
        )
        self.v1_term = wire.PhysicalOperationalFailoverV1Term(
            holder_site=self.v2_term.holder_site,
            writer_epoch=self.v2_term.writer_epoch,
            writer_lease_id=self.v2_term.writer_lease_id,
            witness_transition_id=self.v2_term.witness_transition_id,
            witnessed_term_proof_sha256=self.v2_term.proof_sha256,
            issued_at=self.v2_term.issued_at,
            expires_at=self.v2_term.expires_at,
        )
        term_sha = ledger._term_sha256(self.v1_term, code="test")
        state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=1,
            phase="fi-active",
            clock_floor=NOW - timedelta(seconds=10),
            active_term=self.v1_term,
            active_term_sha256=term_sha,
        )
        entry = ledger._make_entry(
            sequence=1,
            previous_head_sha256="0" * 64,
            observed_at=NOW - timedelta(seconds=10),
            event="bootstrap-fi-active",
            state=state,
        )
        self.snapshot = ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
            version=1,
            head_sha256=entry.entry_sha256,
            entry=entry,
            state=state,
        )
        self.v1_revalidator = revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidator(
            config=self.v1_config,
            fetcher=v1_support._Fetcher(
                signing_config=self.v1_config,
                private_key=self.v1_attestation_key,
                snapshot=self.snapshot,
                issued_at=NOW - timedelta(seconds=1),
                expires_at=NOW + timedelta(seconds=20),
            ),
            durable_guard=v1_support._Guard(),
            clock=self.clock,
        )
        self.v2_config = strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig(
            roundtrip_config=self.v2_chain.config,
            local_commit_signer_public_key=_public(self.v2_local_signer),
            enabled=True,
            maximum_evidence_age_seconds=45,
        )
        self.v2_live = strict._LiveActivationFacts(
            mode="normal_fi_writer",
            stream_generation_id=self.v2_attestation.activation_stream_generation_id,
            route_artifact_sha256=self.v2_attestation.activation_route_artifact_sha256,
            source_cutover_attestation_sha256=(
                self.v2_attestation.activation_source_cutover_attestation_sha256
            ),
            receiver_permit_sha256=self.v2_attestation.activation_receiver_permit_sha256,
            witness_transition_id=self.v2_term.witness_transition_id,
        )
        self.sql_config = sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig(
            enabled=True,
            writer_admission_config=self.writer_config,
            control_role_label="writer-control",
            control_policy_sha256="f" * 64,
        )
        v2_facts = strict._config(self.v2_config)
        self.bridge_config = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig(
            enabled=True,
            cluster_id=self.binding.cluster_id,
            local_site=self.binding.local_site,
            release_sha=self.binding.release_sha,
            generation_id=self.binding.generation_id,
            expected_v1_revalidator_configuration_sha256=(
                revalidator.physical_operational_failover_v1_witness_current_term_revalidator_configuration_sha256(
                    config=self.v1_config
                )
            ),
            expected_v2_strict_writer_configuration_sha256=v2_facts.configuration_sha256,
            expected_v2_context_sha256=self.v2_attestation.context_sha256,
            expected_v2_activation_mode="normal_fi_writer",
            expected_v2_stream_generation_id=self.v2_attestation.activation_stream_generation_id,
            bridge_signer_public_key=_public(self.bridge_signer),
            bridge_signer_key_id=_id("bridge-signer-key"),
            v1_current_term_signer_public_key=_public(self.v1_attestation_key),
            v1_promotion_signer_public_key=_public(self.v1_promotion_key),
            v2_witness_public_key=self.v2_chain.config.witness_public_key,
            v2_fi_outbox_public_key=self.v2_chain.config.fi_outbox_public_key,
            v2_ir_recovery_exporter_public_key=(
                self.v2_chain.config.ir_recovery_exporter_public_key
            ),
            v2_ir_durable_assertion_public_key=(
                self.v2_chain.config.ir_durable_assertion_public_key
            ),
            v2_remote_source_public_key=(
                self.v2_chain.config.remote_ack_config.expected_source_public_key
            ),
            v2_remote_destination_public_key=(
                self.v2_chain.config.remote_ack_config.expected_destination_public_key
            ),
            v2_local_commit_signer_public_key=_public(self.v2_local_signer),
            safety_margin_seconds=5,
            maximum_certificate_age_seconds=30,
        )
        self.runtime_config = subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerConfig(
            enabled=True,
            bridge_config=self.bridge_config,
            v1_revalidator_config=self.v1_config,
            v1_sqlalchemy_transaction_config=self.sql_config,
            v2_strict_writer_config=self.v2_config,
        )
        self.issuer = subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuer(
            config=self.runtime_config,
            bridge_signer_private_key=self.bridge_signer,
            clock=self.clock,
        )

    def _v1_admission_and_provenance(self):
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        revalidation = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=startup,
            evidence_revalidator=self.v1_revalidator,
            current_term_provenance_binder=self.v1_revalidator,
            revalidation_id=_id("bridge-revalidation"),
            now=NOW,
        )
        assert revalidation is not None
        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=revalidation.next_state,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=NOW,
        )
        assert operation is not None
        writer_admission = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=revalidation.next_state,
            operation=operation,
            now=NOW,
        )
        assert writer_admission is not None
        provenance = self.v1_revalidator.bind_current_term_provenance_to_writer_admission(
            writer_admission=writer_admission,
            writer_admission_config=self.writer_config,
        )
        return writer_admission, provenance

    def _v2_prepared(self):
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.v2_term, object(), self.v2_live),
        ):
            return strict.prepare_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.v2_config,
                attestation=self.v2_attestation,
                witnessed_term=self.v2_term,
                activation=object(),
            )

    def _issuer_issue(self, writer_admission, provenance, prepared):
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.v2_term, object(), self.v2_live),
        ):
            return self.issuer.issue_pre_transaction(
                writer_admission=writer_admission,
                v1_current_term_provenance=provenance,
                v2_prepared=prepared,
            )

    def _sql_receipt(self, writer_admission, *, writer_epoch=None):
        transition = writer_admission.state_transition
        prior = transition.prior_state
        facts = sql._facts(self.sql_config)
        assert facts is not None
        return sql._mint_commit_receipt(
            facts=facts,
            commit_id=uuid4(),
            commit_sha256="c" * 64,
            receipt_sha256="d" * 64,
            cluster_id=self.binding.cluster_id,
            local_site=self.binding.local_site,
            release_sha=self.binding.release_sha,
            generation_id=self.binding.generation_id,
            prior_revision=prior.revision,
            next_revision=transition.next_state.revision,
            fence_generation=prior.fence_generation,
            writer_epoch=(
                writer_admission.term.writer_epoch
                if writer_epoch is None
                else writer_epoch
            ),
            writer_lease_id=writer_admission.term.writer_lease_id,
            evidence_id=writer_admission.term.evidence_id,
            revalidation_id=writer_admission.term.revalidation_id,
            admitted_at=writer_admission.admitted_at,
        )

    def test_two_stage_flow_uses_only_opaque_capabilities_and_binds_after_receipt(self) -> None:
        writer_admission, provenance = self._v1_admission_and_provenance()
        prepared = self._v2_prepared()
        issued = self._issuer_issue(writer_admission, provenance, prepared)
        self.assertIs(
            writer_admission,
            self.issuer.require_writer_admission_for_transaction(issued),
        )
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.v2_term, object(), self.v2_live),
        ):
            self.assertIs(
                prepared,
                self.issuer.require_v2_prepared_for_transaction(issued),
            )
        self.assertEqual({"certificate_id", "certificate_sha256", "intent_sha256", "issued_at", "expires_at", "_capability"}, set(vars(issued)))
        with self.assertRaises(TypeError):
            pickle.dumps(issued)

        receipt = self._sql_receipt(writer_admission)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.v2_term, object(), self.v2_live),
        ):
            bound = self.issuer.bind_post_flush(
                issued=issued,
                v1_sql_commit_receipt=receipt,
            )
        projection = bridge.require_bound_physical_operational_failover_v1_v2_writer_term_bridge_intent(
            value=bound,
            config=self.bridge_config,
            now=NOW,
        )
        self.assertEqual(str(receipt.commit_id), projection.parent_commit_id)
        self.assertEqual(issued.certificate_sha256, projection.certificate_sha256)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "ISSUED_CAPABILITY_REQUIRED|ISSUED_REPLAYED",
        ):
            self.issuer.bind_post_flush(issued=issued, v1_sql_commit_receipt=receipt)

    def test_equal_but_foreign_v1_admission_cannot_consume_provenance(self) -> None:
        writer_admission, provenance = self._v1_admission_and_provenance()
        prepared = self._v2_prepared()
        foreign = object.__new__(type(writer_admission))
        for name in writer_admission.__dataclass_fields__:
            object.__setattr__(foreign, name, getattr(writer_admission, name))
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "V1_PROVENANCE_INVALID",
        ):
            self._issuer_issue(foreign, provenance, prepared)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "V1_PROVENANCE_INVALID",
        ):
            self._issuer_issue(writer_admission, provenance, prepared)

    def test_parent_mismatch_burns_issued_capability_without_raw_parent_fallback(self) -> None:
        writer_admission, provenance = self._v1_admission_and_provenance()
        prepared = self._v2_prepared()
        issued = self._issuer_issue(writer_admission, provenance, prepared)
        bad_receipt = self._sql_receipt(
            writer_admission,
            writer_epoch=writer_admission.term.writer_epoch + 1,
        )
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.v2_term, object(), self.v2_live),
        ), self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "PARENT_BINDING_INVALID",
        ):
            self.issuer.bind_post_flush(
                issued=issued,
                v1_sql_commit_receipt=bad_receipt,
            )
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "ISSUED_CAPABILITY_REQUIRED|ISSUED_REPLAYED",
        ):
            self.issuer.require_writer_admission_for_transaction(issued)

    def test_raw_substitution_forged_issued_and_signer_mismatch_fail_closed(self) -> None:
        writer_admission, provenance = self._v1_admission_and_provenance()
        prepared = self._v2_prepared()
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "OPAQUE_CAPABILITY_REQUIRED",
        ):
            self.issuer.issue_pre_transaction(
                writer_admission=object(),
                v1_current_term_provenance=provenance,
                v2_prepared=prepared,
            )
        # Input-type rejection happened before the provenance was consumed.
        issued = self._issuer_issue(writer_admission, provenance, prepared)
        forged = object.__new__(
            subject.IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate
        )
        for name in issued.__dataclass_fields__:
            object.__setattr__(forged, name, getattr(issued, name))
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "ISSUED_CAPABILITY_REQUIRED",
        ):
            self.issuer.require_writer_admission_for_transaction(forged)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "SIGNER_INVALID",
        ):
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuer(
                config=self.runtime_config,
                bridge_signer_private_key=Ed25519PrivateKey.generate(),
                clock=self.clock,
            )

    def test_stale_issued_certificate_fails_closed(self) -> None:
        writer_admission, provenance = self._v1_admission_and_provenance()
        prepared = self._v2_prepared()
        issued = self._issuer_issue(writer_admission, provenance, prepared)
        self.clock.now = NOW + timedelta(seconds=10)
        with self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "ISSUED_CERTIFICATE_INVALID",
        ):
            self.issuer.require_writer_admission_for_transaction(issued)

    def test_replayed_sql_receipt_fails_closed_without_raw_fallback(self) -> None:
        writer_admission, provenance = self._v1_admission_and_provenance()
        prepared = self._v2_prepared()
        issued = self._issuer_issue(writer_admission, provenance, prepared)
        receipt = self._sql_receipt(writer_admission)
        sql.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
            receipt,
            config=self.sql_config,
        )
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.v2_term, object(), self.v2_live),
        ), self.assertRaisesRegex(
            subject.PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError,
            "V1_SQL_RECEIPT_INVALID",
        ):
            self.issuer.bind_post_flush(
                issued=issued,
                v1_sql_commit_receipt=receipt,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
