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
    """Test double for an external atomic unique-consumption transaction."""

    def __init__(self, signer: Ed25519PrivateKey) -> None:
        self.signer = signer
        self.calls = 0
        self.instructions = []
        self._receipts: dict[str, bytes] = {}
        self.mutate_unsigned = None

    def commit_after_verified_witness_roundtrip_attestation(self, *, instruction):
        self.calls += 1
        self.instructions.append(instruction)
        existing = self._receipts.get(instruction.commit_id)
        if existing is not None:
            return existing
        unsigned = strict._runtime_unsigned(
            instruction,
            local_commit_record_id="v2-witness-local-commit-000001",
            local_response_id="v2-witness-local-response-000001",
            attestation_consumption_id=strict._attestation_consumption_id(instruction),
            committed_at=NOW,
        )
        if self.mutate_unsigned is not None:
            unsigned = self.mutate_unsigned(unsigned)
        signature = self.signer.sign(
            strict._COMMIT_DOMAIN + strict._canonical(unsigned, code="test")
        )
        receipt = strict._canonical(
            {
                **unsigned,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
            code="test",
        )
        self._receipts[instruction.commit_id] = receipt
        return receipt


class PhysicalWalV2WitnessRoundtripStrictWriterResponseTests(unittest.TestCase):
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

    def _commit(self, runtime: _AtomicRuntime, *, liveness=None):
        live_value = liveness or (self.term, self.activation, self.live)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=live_value,
        ):
            return strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=runtime,
            )

    def test_verified_witness_attestation_commits_once_and_projects(self) -> None:
        runtime = _AtomicRuntime(self.local_signer)
        observation = self._commit(runtime)
        retry = self._commit(runtime)
        self.assertEqual(2, runtime.calls)
        self.assertEqual(1, len(runtime._receipts))
        self.assertEqual(observation.commit_id, retry.commit_id)
        self.assertEqual(
            strict._attestation_consumption_id(runtime.instructions[0]),
            observation.attestation_consumption_id,
        )
        self.assertEqual(self.attestation.attestation_sha256, observation.attestation_sha256)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ):
            projection = (
                strict.project_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
                    observation,
                    config=self.config,
                )
            )
        self.assertEqual(observation.observation_sha256, projection.observation_sha256)
        self.assertEqual(
            self.attestation.witness_ledger_previous_head_sha256,
            projection.witness_ledger_previous_head_sha256,
        )

    def test_raw_or_forged_attestation_never_reaches_runtime(self) -> None:
        runtime = _AtomicRuntime(self.local_signer)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "ATTESTATION_INVALID",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation.canonical_attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=runtime,
            )
        self.assertEqual(0, runtime.calls)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "ATTESTATION_INVALID",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=replace(self.attestation, attestation_sha256="f" * 64),
                witnessed_term=self.term,
                activation=self.activation,
                runtime=runtime,
            )
        self.assertEqual(0, runtime.calls)

    def test_post_commit_term_or_activation_flip_fences_response(self) -> None:
        runtime = _AtomicRuntime(self.local_signer)
        changed_live = replace(self.live, route_artifact_sha256="f" * 64)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            side_effect=[
                (self.term, self.activation, self.live),
                (self.term, self.activation, changed_live),
            ],
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "INPUT_CHANGED_DURING_COMMIT",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=runtime,
            )
        self.assertEqual(1, runtime.calls)
        changed_term = replace(self.term, proof_sha256="e" * 64)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            side_effect=[
                (self.term, self.activation, self.live),
                (changed_term, self.activation, self.live),
            ],
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "INPUT_CHANGED_DURING_COMMIT",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=_AtomicRuntime(self.local_signer),
            )

    def test_pre_commit_live_term_or_activation_failure_never_calls_runtime(self) -> None:
        runtime = _AtomicRuntime(self.local_signer)
        with patch.object(strict, "_trusted_now", return_value=NOW), patch.object(
            strict,
            "_live_activation_facts",
            side_effect=strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError(
                "V2_WITNESS_STRICT_WRITER_LIVE_TERM_CROSS_PIN_MISMATCH"
            ),
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "LIVE_TERM_CROSS_PIN_MISMATCH",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=runtime,
            )
        self.assertEqual(0, runtime.calls)

    def test_signed_runtime_receipt_must_bind_exact_attestation_and_consumption(self) -> None:
        runtime = _AtomicRuntime(self.local_signer)

        def mismatch(unsigned: dict[str, object]) -> dict[str, object]:
            result = dict(unsigned)
            result["context_certificate_sha256"] = "f" * 64
            return result

        runtime.mutate_unsigned = mismatch
        with self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "RUNTIME_RECEIPT_BINDING_MISMATCH",
        ):
            self._commit(runtime)
        runtime = _AtomicRuntime(self.local_signer)

        def wrong_consume(unsigned: dict[str, object]) -> dict[str, object]:
            result = dict(unsigned)
            result["attestation_consumption_id"] = "v2-witness-consume-wrong-000001"
            return result

        runtime.mutate_unsigned = wrong_consume
        with self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "RUNTIME_RECEIPT_CONSUMPTION_MISMATCH",
        ):
            self._commit(runtime)

    def test_role_key_swap_stale_attestation_and_observation_reflection_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "CONFIG_ROLE_KEY_REUSE",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=replace(
                    self.config,
                    local_commit_signer_public_key=(
                        self.chain.config.witness_public_key
                    ),
                ),
                attestation=self.attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=_AtomicRuntime(self.local_signer),
            )
        with patch.object(strict, "_trusted_now", return_value=NOW + timedelta(seconds=16)), patch.object(
            strict,
            "_live_activation_facts",
            return_value=(self.term, self.activation, self.live),
        ), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "ATTESTATION_INVALID",
        ):
            strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
                config=self.config,
                attestation=self.attestation,
                witnessed_term=self.term,
                activation=self.activation,
                runtime=_AtomicRuntime(self.local_signer),
            )
        observation = self._commit(_AtomicRuntime(self.local_signer))
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(observation)
        with patch.object(strict, "_trusted_now", return_value=NOW), self.assertRaisesRegex(
            strict.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
            "OBSERVATION_CAPABILITY_REQUIRED",
        ):
            strict.require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
                replace(observation, attestation_sha256="f" * 64),
                config=self.config,
            )

    def test_module_has_no_legacy_raw_protocol_or_transport_dependency(self) -> None:
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
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
            "urllib",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_wal_v2_strict_remote_ack_writer_response", source)
        self.assertNotIn("remote_ack_receiver_ledger", source)
        self.assertNotIn("physical_full_matrix_v2_recovery_evidence", source)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("connect(", source)
        self.assertNotIn("open(", source)
        public_parameters = set(
            inspect.signature(
                strict.commit_physical_wal_v2_witness_roundtrip_strict_writer_response
            ).parameters
        )
        self.assertIn("attestation", public_parameters)
        for forbidden_parameter in (
            "remote_ack_evidence",
            "receiver_ledger_receipt",
            "receiver_recovery_evidence",
            "target_recovery_evidence",
        ):
            self.assertNotIn(forbidden_parameter, public_parameters)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
