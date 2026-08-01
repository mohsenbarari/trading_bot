from __future__ import annotations

import ast
import base64
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import inspect
import pickle
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as strict
from tests import test_physical_wal_v2_witness_roundtrip_contract as contract_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


@dataclass(frozen=True)
class _Term:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    proof_sha256: str
    witness_transition_id: str


@dataclass(frozen=True)
class _BridgeTerm:
    """Live V2 term shape including the certificate bridge validity window."""

    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    proof_sha256: str
    witness_transition_id: str
    issued_at: datetime
    expires_at: datetime


class _ReceiptFactory:
    """A pure stand-in for the already-committed root-owned transaction."""

    def __init__(self, signer: Ed25519PrivateKey) -> None:
        self.signer = signer

    def receipt(self, instruction, *, committed_at=NOW) -> bytes:  # noqa: ANN001
        unsigned = strict._runtime_unsigned(
            instruction,
            local_commit_record_id="v2-witness-local-commit-prepare-000001",
            local_response_id="v2-witness-local-response-prepare-000001",
            attestation_consumption_id=strict._attestation_consumption_id(instruction),
            committed_at=committed_at,
        )
        signature = self.signer.sign(
            strict._COMMIT_DOMAIN + strict._canonical(unsigned, code="test")
        )
        return strict._canonical(
            {
                **unsigned,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
            code="test",
        )


class PhysicalWalV2WitnessRoundtripStrictWriterPrepareFinalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.chain = contract_tests.PhysicalWalV2WitnessRoundtripContractTests(
            "runTest"
        )
        cls.chain.setUp()
        _certificate, _envelope, assertion, _issued = cls.chain._full_chain()
        attestation_raw = cls.chain._attestation(assertion)
        cls.attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation_raw,
            config=cls.chain.config,
            now=NOW,
        )
        cls.local_signer = Ed25519PrivateKey.generate()
        cls.config = strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig(
            roundtrip_config=cls.chain.config,
            local_commit_signer_public_key=_public(cls.local_signer),
            enabled=True,
            maximum_evidence_age_seconds=45,
        )
        cls.term = _Term(
            holder_site=cls.attestation.writer_holder_site,
            writer_epoch=cls.attestation.writer_epoch,
            writer_lease_id=cls.attestation.writer_lease_id,
            proof_sha256=cls.attestation.witnessed_term_proof_sha256,
            witness_transition_id=cls.attestation.witness_transition_id,
        )
        cls.activation = object()
        cls.live = strict._LiveActivationFacts(
            mode=cls.attestation.activation_mode,
            stream_generation_id=cls.attestation.activation_stream_generation_id,
            route_artifact_sha256=cls.attestation.activation_route_artifact_sha256,
            source_cutover_attestation_sha256=(
                cls.attestation.activation_source_cutover_attestation_sha256
            ),
            receiver_permit_sha256=cls.attestation.activation_receiver_permit_sha256,
            witness_transition_id=cls.attestation.witness_transition_id,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.chain.tearDown()

    def _prepared(self):
        return strict.prepare_physical_wal_v2_witness_roundtrip_strict_writer_response(
            config=self.config,
            attestation=self.attestation,
            witnessed_term=self.term,
            activation=self.activation,
        )

    def test_prepare_then_finalize_revalidates_and_returns_exact_observation(self) -> None:
        factory = _ReceiptFactory(self.local_signer)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            prepared = self._prepared()
            instruction = (
                strict.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    prepared,
                    config=self.config,
                )
            )
            self.assertIs(prepared.instruction, instruction)
            receipt = factory.receipt(instruction)
            observation = (
                strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    prepared,
                    config=self.config,
                    runtime_receipt=receipt,
                    witnessed_term=self.term,
                    activation=self.activation,
                )
            )
            projection = (
                strict.project_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
                    observation,
                    config=self.config,
                )
            )
        self.assertEqual(prepared.instruction.commit_id, observation.commit_id)
        self.assertEqual(
            strict._attestation_consumption_id(prepared.instruction),
            observation.attestation_consumption_id,
        )
        self.assertEqual(observation.observation_sha256, projection.observation_sha256)
        self.assertEqual(self.attestation.attestation_sha256, projection.attestation_sha256)

    def test_bridge_intent_projection_comes_only_from_fresh_opaque_prepare(self) -> None:
        bridge_term = _BridgeTerm(
            holder_site=self.term.holder_site,
            writer_epoch=self.term.writer_epoch,
            writer_lease_id=self.term.writer_lease_id,
            proof_sha256=self.term.proof_sha256,
            witness_transition_id=self.term.witness_transition_id,
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=50),
        )
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(bridge_term, self.activation, self.live),
        ):
            prepared = strict.prepare_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation,
                witnessed_term=bridge_term,
                activation=self.activation,
            )
            projection = (
                strict.project_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bridge_intent(
                    prepared,
                    config=self.config,
                )
            )
        self.assertEqual(prepared.instruction.commit_id, projection.commit_id)
        self.assertEqual(self.attestation.issued_at, projection.attestation_issued_at)
        self.assertEqual(self.attestation.expires_at, projection.attestation_expires_at)
        self.assertEqual(bridge_term.issued_at, projection.term_issued_at)
        self.assertEqual(bridge_term.expires_at, projection.term_expires_at)
        self.assertNotIn("canonical_attestation", projection.__dataclass_fields__)

    def test_forged_tampered_or_serialized_prepared_is_never_accepted(self) -> None:
        factory = _ReceiptFactory(self.local_signer)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            prepared = self._prepared()
            receipt = factory.receipt(prepared.instruction)
            with self.assertRaisesRegex(TypeError, "PREPARED_SERIALIZATION_FORBIDDEN"):
                pickle.dumps(prepared)
            with self.assertRaisesRegex(TypeError, "PREPARED_CONSTRUCTION_FORBIDDEN"):
                strict.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse(
                    instruction=prepared.instruction,
                    capability=object(),
                )
            forged = object.__new__(
                strict.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
            )
            object.__setattr__(forged, "instruction", prepared.instruction)
            object.__setattr__(forged, "_capability", strict._PREPARED_CAPABILITY)
            with self.assertRaisesRegex(
                strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
                "PREPARED_CAPABILITY_REQUIRED",
            ):
                strict.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    forged,
                    config=self.config,
                )
            with self.assertRaisesRegex(
                strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
                "PREPARED_CAPABILITY_REQUIRED",
            ):
                strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    forged,
                    config=self.config,
                    runtime_receipt=receipt,
                    witnessed_term=self.term,
                    activation=self.activation,
                )
            object.__setattr__(
                prepared,
                "instruction",
                replace(prepared.instruction, writer_epoch=prepared.instruction.writer_epoch + 1),
            )
            with self.assertRaisesRegex(
                strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
                "PREPARED_TAMPERED",
            ):
                strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    prepared,
                    config=self.config,
                    runtime_receipt=receipt,
                    witnessed_term=self.term,
                    activation=self.activation,
                )

    def test_finalize_rejects_stale_term_flip_and_configuration_change(self) -> None:
        factory = _ReceiptFactory(self.local_signer)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            stale_prepared = self._prepared()
            stale_receipt = factory.receipt(stale_prepared.instruction)
        with patch.object(
            strict,
            "_trusted_now",
            return_value=NOW + timedelta(seconds=16),
        ), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "ATTESTATION_INVALID",
        ):
            strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                stale_prepared,
                config=self.config,
                runtime_receipt=stale_receipt,
                witnessed_term=self.term,
                activation=self.activation,
            )

        changed_term = replace(self.term, proof_sha256="e" * 64)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            side_effect=[
                (self.term, self.activation, self.live),
                (changed_term, self.activation, self.live),
            ],
        ):
            changed_prepared = self._prepared()
            changed_receipt = factory.receipt(changed_prepared.instruction)
            with self.assertRaisesRegex(
                strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
                "INPUT_CHANGED_DURING_FINALIZE",
            ):
                strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    changed_prepared,
                    config=self.config,
                    runtime_receipt=changed_receipt,
                    witnessed_term=changed_term,
                    activation=self.activation,
                )

        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            config_prepared = self._prepared()
            config_receipt = factory.receipt(config_prepared.instruction)
            changed_config = replace(
                self.config,
                local_commit_signer_public_key=_public(Ed25519PrivateKey.generate()),
            )
            with self.assertRaisesRegex(
                strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
                "PREPARED_CONFIG_MISMATCH",
            ):
                strict.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    config_prepared,
                    config=changed_config,
                )
            with self.assertRaisesRegex(
                strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
                "PREPARED_CONFIG_MISMATCH",
            ):
                strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    config_prepared,
                    config=changed_config,
                    runtime_receipt=config_receipt,
                    witnessed_term=self.term,
                    activation=self.activation,
                )

    def test_split_boundary_has_no_async_runtime_or_transport_surface(self) -> None:
        source = inspect.getsource(strict)
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
        for forbidden in (
            "asyncio",
            "sqlalchemy",
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
            "urllib",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("asyncio.run", source)
        for boundary in (
            strict.prepare_physical_wal_v2_witness_roundtrip_strict_writer_response,
            strict.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response,
            strict.finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response,
        ):
            parameters = set(inspect.signature(boundary).parameters)
            self.assertNotIn("runtime", parameters)
            self.assertNotIn("session", parameters)
            self.assertNotIn("engine", parameters)
            self.assertNotIn("database", parameters)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
