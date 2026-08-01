from __future__ import annotations

import ast
import base64
from dataclasses import dataclass, replace
from datetime import timedelta
import inspect
import json
import pickle
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_full_matrix_v2_witnessed_ack_chain as witnessed_chain
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


class _AtomicRuntime:
    def __init__(self, signer: Ed25519PrivateKey) -> None:
        self._signer = signer
        self._receipts: dict[str, bytes] = {}

    def commit_after_verified_witness_roundtrip_attestation(self, *, instruction):
        existing = self._receipts.get(instruction.commit_id)
        if existing is not None:
            return existing
        unsigned = strict._runtime_unsigned(
            instruction,
            local_commit_record_id="v2-witness-chain-local-commit-000001",
            local_response_id="v2-witness-chain-local-response-000001",
            attestation_consumption_id=strict._attestation_consumption_id(instruction),
            committed_at=NOW,
        )
        signature = self._signer.sign(
            strict._COMMIT_DOMAIN + strict._canonical(unsigned, code="test")
        )
        result = strict._canonical(
            {
                **unsigned,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
            code="test",
        )
        self._receipts[instruction.commit_id] = result
        return result


class PhysicalFullMatrixV2WitnessedAckChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = contract_tests.PhysicalWalV2WitnessRoundtripContractTests(
            "runTest"
        )
        cls.contract.setUp()
        _certificate, _envelope, assertion, _issued = cls.contract._full_chain()
        attestation_raw = cls.contract._attestation(assertion)
        cls.attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation_raw,
            config=cls.contract.config,
            now=NOW,
        )
        cls.local_signer = Ed25519PrivateKey.generate()
        cls.strict_config = (
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig(
                roundtrip_config=cls.contract.config,
                local_commit_signer_public_key=_public(cls.local_signer),
                enabled=True,
                maximum_evidence_age_seconds=45,
            )
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
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(cls.term, cls.activation, cls.live),
        ):
            cls.strict_observation = (
                strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                    config=cls.strict_config,
                    attestation=cls.attestation,
                    witnessed_term=cls.term,
                    activation=cls.activation,
                    runtime=_AtomicRuntime(cls.local_signer),
                )
            )
        cls.config = witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainConfig(
            roundtrip_config=cls.contract.config,
            strict_writer_config=cls.strict_config,
            enabled=True,
            maximum_evidence_age_seconds=45,
        )
        cls.inputs = witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainInputs(
            recovery_evidence=cls.contract.fixture.target_recovery,
            witness_roundtrip_attestation=cls.attestation,
            strict_writer_response=cls.strict_observation,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.contract.tearDown()

    def _mint(self, *, now=NOW, inputs=None, config=None):
        with patch.object(strict, "_trusted_now", return_value=now), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            return witnessed_chain.mint_verified_physical_full_matrix_v2_witnessed_ack_chain(
                config=config or self.config,
                inputs=inputs or self.inputs,
                now=now,
            )

    def test_happy_chain_revalidates_all_three_owners_and_projects_requested_pins(self) -> None:
        result = self._mint()
        self.assertFalse(result.recovery_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertEqual(
            self.attestation.attestation_sha256,
            result.roundtrip_attestation_sha256,
        )
        self.assertEqual(
            self.strict_observation.observation_sha256,
            result.strict_observation_sha256,
        )
        self.assertEqual(
            self.attestation.witness_ledger_previous_head_sha256,
            result.witness_ledger_previous_head_sha256,
        )
        self.assertEqual(
            self.contract.fixture.target_recovery.target_replay_lsn,
            result.receiver_replay_lsn,
        )
        signed_attestation = json.loads(self.attestation.canonical_attestation)
        self.assertEqual(
            signed_attestation["configuration_sha256"],
            result.roundtrip_configuration_sha256,
        )
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            self.assertIs(
                result,
                witnessed_chain.require_verified_physical_full_matrix_v2_witnessed_ack_chain(
                    result,
                    config=self.config,
                    now=NOW,
                ),
            )
            projection = (
                witnessed_chain.project_verified_physical_full_matrix_v2_witnessed_ack_chain(
                    result,
                    config=self.config,
                    now=NOW,
                )
            )
        self.assertEqual(result.chain_sha256, projection.chain_sha256)
        self.assertEqual(
            result.activation_receiver_permit_sha256,
            projection.activation_receiver_permit_sha256,
        )

    def test_raw_forged_or_owner_stale_capabilities_fail_closed(self) -> None:
        raw = replace(
            self.inputs,
            witness_roundtrip_attestation=self.attestation.canonical_attestation,
        )
        with self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "INPUTS_INVALID",
        ):
            self._mint(inputs=raw)
        forged = replace(
            self.inputs,
            witness_roundtrip_attestation=replace(
                self.attestation,
                witness_ledger_entry_sha256="f" * 64,
            ),
        )
        with self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "UPSTREAM_INVALID",
        ):
            self._mint(inputs=forged)
        with self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "UPSTREAM_INVALID",
        ):
            self._mint(now=NOW + timedelta(seconds=16))

    def test_recovery_and_strict_live_term_swaps_are_explicit_cross_pin_failures(self) -> None:
        altered_recovery = replace(
            self.contract.fixture.target_recovery,
            route_commitment_sha256="f" * 64,
        )
        with patch.object(
            witnessed_chain,
            "require_verified_physical_full_matrix_v2_recovery_evidence",
            return_value=altered_recovery,
        ), patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ), self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "CROSS_PIN_MISMATCH",
        ):
            witnessed_chain.mint_verified_physical_full_matrix_v2_witnessed_ack_chain(
                config=self.config,
                inputs=self.inputs,
                now=NOW,
            )
        altered_strict = replace(
            self.strict_observation,
            writer_epoch=self.strict_observation.writer_epoch + 1,
        )
        with patch.object(
            witnessed_chain,
            "require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation",
            return_value=altered_strict,
        ), self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "CROSS_PIN_MISMATCH",
        ):
            witnessed_chain.mint_verified_physical_full_matrix_v2_witnessed_ack_chain(
                config=self.config,
                inputs=self.inputs,
                now=NOW,
            )

    def test_portable_assertion_byte_substitution_and_chain_reflection_fail_closed(self) -> None:
        result = self._mint()
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(result)
        with patch.object(strict, "_trusted_now", return_value=NOW), self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "CAPABILITY_REQUIRED",
        ):
            witnessed_chain.require_verified_physical_full_matrix_v2_witnessed_ack_chain(
                replace(result, receiver_replay_lsn="0/1"),
                config=self.config,
                now=NOW,
            )
        raw_assertion = bytearray(self.attestation.canonical_ir_durable_assertion)
        raw_assertion[-1] = ord(" ")
        forged_attestation = replace(
            self.attestation,
            canonical_ir_durable_assertion=bytes(raw_assertion),
        )
        with patch.object(
            witnessed_chain,
            "require_verified_physical_wal_v2_witness_roundtrip_attestation",
            return_value=forged_attestation,
        ), patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ), self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "PORTABLE_ASSERTION_INVALID",
        ):
            witnessed_chain.mint_verified_physical_full_matrix_v2_witnessed_ack_chain(
                config=self.config,
                inputs=self.inputs,
                now=NOW,
            )

    def test_config_is_default_off_and_module_has_no_raw_or_transport_dependency(self) -> None:
        with self.assertRaisesRegex(
            witnessed_chain.PhysicalFullMatrixV2WitnessedAckChainError,
            "CONFIG_INVALID",
        ):
            self._mint(
                config=replace(self.config, enabled=False),
            )
        source = inspect.getsource(witnessed_chain)
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
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
            "urllib",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_full_matrix_v2_ack_chain", source)
        self.assertNotIn("remote_ack_receiver_ledger", source)
        self.assertNotIn("strict_remote_ack_writer_response", source)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("connect(", source)
        self.assertNotIn("open(", source)
        public_parameters = set(
            inspect.signature(
                witnessed_chain.mint_verified_physical_full_matrix_v2_witnessed_ack_chain
            ).parameters
        )
        self.assertEqual({"config", "inputs", "now"}, public_parameters)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
