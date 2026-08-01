"""Adversarial tests for V4 signed installed-adapter provenance.

The fixture intentionally composes V4 with inert in-process phase adapters.
Those callbacks may pass the older static composition/preflight shape checks,
but they cannot become host-installation evidence without all eight signed
attestations under the opaque, root-built issuer policy.
"""

from __future__ import annotations

import base64
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_execution_driver_v4 as driver
from core import physical_full_matrix_v2_gen2_witnessed_campaign_readiness as readiness_owner
from core import physical_full_matrix_v4_materialization_preflight as materialization
from core import physical_full_matrix_v4_phase_installation_provenance as subject
from core import physical_full_matrix_v4_root_composition as composition
from core import physical_full_matrix_v4_witness_anchor_wire as wire
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase_installation_provenance.py"
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.calls = 0

    def now_utc(self) -> datetime:
        self.calls += 1
        return self.value


class _InProcessPhaseAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute_phase(self, *, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("installation provenance invoked a phase adapter")


class _NeverCalledPostEffectVerifier:
    def __init__(self, phase: object) -> None:
        self.phase_name = phase.name
        self.phase_sequence = phase.sequence
        self.oracle = phase.oracle
        self.transport_profile = phase.transport_profile
        self.calls = 0

    def require_post_effect_completion(self, **kwargs: object) -> None:
        del kwargs
        self.calls += 1
        raise AssertionError("installation provenance invoked a post-effect verifier")


class _NeverCalledJournal:
    def _called(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("installation provenance invoked a journal")

    read_receipts = _called
    claim_phase = _called
    mark_effect_started = _called
    project_effect_start_anchor_proof = _called
    project_predecessor_phase_completion_anchor_proof = _called
    append_started = _called


class _NeverCalledResolver:
    def resolve_readiness(self, *, binding: object) -> object:
        del binding
        raise AssertionError("installation provenance invoked a resolver")


class _NeverCalledContinuity:
    def verify_campaign_continuity(self, **kwargs: object) -> None:
        del kwargs
        raise AssertionError("installation provenance invoked continuity")


class PhysicalFullMatrixV4PhaseInstallationProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Gen2WitnessedAckChainFixture()
        cls.fixture.setUp()
        cls.now = cls.fixture.now
        chain = cls.fixture.mint_chain(now=cls.now)
        readiness_binding = readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
            **{
                item.name: getattr(chain, item.name)
                for item in fields(
                    readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding
                )
            }
        )
        readiness_config = readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
            binding=readiness_binding,
            gen2_witnessed_ack_chain_config=cls.fixture.config,
            enabled=True,
        )
        readiness_inputs = readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
            gen2_witnessed_ack_chain=chain,
        )
        with cls.fixture._all_owner_clocks(now=cls.now):
            cls.readiness = (
                readiness_owner.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                    config=readiness_config,
                    inputs=readiness_inputs,
                    now=cls.now,
                )
            )
        cls.binding = driver.PhysicalFullMatrixV4ExecutionBinding(
            campaign_id=readiness_binding.campaign_id,
            release_sha=readiness_binding.release_sha,
            readiness_binding_sha256=cls.readiness.report.binding_sha256,
            route_commitment_sha256=readiness_binding.route_commitment_sha256,
            four_role_binding_sha256=readiness_binding.four_role_binding_sha256,
            writer_holder_site=readiness_binding.writer_holder_site,
            writer_epoch=readiness_binding.writer_epoch,
            writer_lease_id=readiness_binding.writer_lease_id,
            witnessed_term_proof_sha256=readiness_binding.witnessed_term_proof_sha256,
            source_site=readiness_binding.source_site,
            destination_site=readiness_binding.destination_site,
            roundtrip_attestation_sha256=readiness_binding.roundtrip_attestation_sha256,
            roundtrip_configuration_sha256=(
                readiness_binding.roundtrip_configuration_sha256
            ),
            witness_transition_id=readiness_binding.witness_transition_id,
            witness_sequence=readiness_binding.witness_sequence,
        )
        cls.run_id = UUID("85e980e5-1491-4d37-82d1-8e9feb3fe896")
        cls.execution_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=cls.binding,
            readiness=cls.readiness,
            run_id=cls.run_id,
            enabled=True,
        )
        cls.plan = driver.build_physical_full_matrix_v4_execution_plan(
            config=cls.execution_config
        )
        cls.root_policy_sha256 = (
            composition.derive_physical_full_matrix_v4_root_composition_policy_sha256(
                binding=cls.binding,
                run_id=cls.run_id,
                maximum_oracle_age_seconds=cls.execution_config.maximum_oracle_age_seconds,
            )
        )
        cls.root_config = composition.PhysicalFullMatrixV4RootCompositionConfig(
            enabled=True,
            campaign_id=cls.binding.campaign_id,
            release_sha=cls.binding.release_sha,
            run_id=cls.run_id,
            maximum_oracle_age_seconds=cls.execution_config.maximum_oracle_age_seconds,
            policy_sha256=cls.root_policy_sha256,
        )
        cls.clock = _Clock(cls.now)
        cls.phase_adapters = {
            phase.name: _InProcessPhaseAdapter()
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        cls.phase_bindings = {
            phase.name: composition.PhysicalFullMatrixV4RootPhaseAdapterBinding(
                phase_name=phase.name,
                phase_sequence=phase.sequence,
                oracle=phase.oracle,
                transport_profile=phase.transport_profile,
                destructive=phase.destructive,
                campaign_id=cls.binding.campaign_id,
                release_sha=cls.binding.release_sha,
                policy_sha256=cls.root_policy_sha256,
                phase_adapter=cls.phase_adapters[phase.name],
            )
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        cls.phase_post_effect_verifiers = {
            phase.name: _NeverCalledPostEffectVerifier(phase)
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        with patch.object(composition.os, "geteuid", return_value=0):
            cls.composition = composition.build_physical_full_matrix_v4_root_composition(
                root_config=cls.root_config,
                execution_config=cls.execution_config,
                plan=cls.plan,
                phase_adapters=cls.phase_bindings,
                phase_post_effect_verifiers=cls.phase_post_effect_verifiers,
                receipt_journal=_NeverCalledJournal(),
                readiness_resolver=_NeverCalledResolver(),
                trusted_clock=cls.clock,
                campaign_continuity_gate=_NeverCalledContinuity(),
            )
        cls.anchor_identity = wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
            schema=wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA,
            journal_binding_sha256=_sha("journal-binding"),
            baseline_plan_binding_sha256=_sha("baseline-binding"),
            run_id=cls.run_id,
            plan_sha256=cls.plan.plan_sha256,
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256=_sha("anchor-head"),
            canonical_genesis_sha256=_sha("canonical-genesis"),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.tearDown()

    def setUp(self) -> None:
        self.clock.value = self.now
        self.clock.calls = 0
        self.preflight_config = materialization.PhysicalFullMatrixV4MaterializationPreflightConfig(
            enabled=True
        )
        self.preflight_inputs = materialization.PhysicalFullMatrixV4MaterializationPreflightInputs(
            composition=self.composition,
            readiness=self.readiness,
            phase_adapter_material=self.composition.phase_bindings,
            witness_anchor=materialization.PhysicalFullMatrixV4MaterializationWitnessAnchorBinding(
                identity=self.anchor_identity,
                anchor=object_with_anchor_methods(),
            ),
            trusted_clock=self.clock,
        )
        with self.fixture._all_owner_clocks(now=self.now):
            self.preflight = materialization.prepare_physical_full_matrix_v4_materialization_preflight(
                config=self.preflight_config,
                inputs=self.preflight_inputs,
            )
        self.private_keys = {
            "webapp_fi": Ed25519PrivateKey.generate(),
            "webapp_ir": Ed25519PrivateKey.generate(),
            "witness": Ed25519PrivateKey.generate(),
        }
        public_keys = {
            site: private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            for site, private_key in self.private_keys.items()
        }
        self.policy_config = subject.PhysicalFullMatrixV4PhaseInstallationIssuerPolicyConfig(
            enabled=True,
            materialization_preflight_config=self.preflight_config,
            issuer_public_keys=public_keys,
            maximum_attestation_lifetime_seconds=60,
        )
        with patch.object(subject.os, "geteuid", return_value=0), self.fixture._all_owner_clocks(
            now=self.now
        ):
            self.issuer_policy = (
                subject.build_physical_full_matrix_v4_phase_installation_issuer_policy(
                    config=self.policy_config,
                    composition=self.composition,
                    materialization_preflight=self.preflight,
                )
            )
        self.config = subject.PhysicalFullMatrixV4PhaseInstallationProvenanceConfig(
            issuer_policy=self.issuer_policy,
            enabled=True,
        )

    def _attestations(
        self,
        *,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, bytes]:
        issued = self.now if issued_at is None else issued_at
        expires = issued + timedelta(seconds=30) if expires_at is None else expires_at
        result: dict[str, bytes] = {}
        for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES:
            site = subject._PHASE_ISSUER_SITE[phase.name]
            result[phase.name] = subject.build_physical_full_matrix_v4_phase_installation_attestation(
                issuer_policy=self.issuer_policy,
                phase_name=phase.name,
                adapter_implementation_sha256=_sha("implementation:" + phase.name),
                adapter_configuration_sha256=_sha("configuration:" + phase.name),
                attested_at=issued,
                expires_at=expires,
                issuer_private_key=self.private_keys[site],
            )
        return result

    def _resign(
        self,
        raw: bytes,
        *,
        phase: driver.PhysicalFullMatrixV4ExecutionPhase,
        changes: dict[str, object],
    ) -> bytes:
        parsed = json.loads(raw.decode("ascii"))
        parsed.update(changes)
        parsed.pop("signature")
        signature = self.private_keys[subject._PHASE_ISSUER_SITE[phase.name]].sign(
            subject._SIGNING_DOMAIN + subject._canonical(parsed, code="test")
        )
        parsed["signature"] = base64.b64encode(signature).decode("ascii")
        return subject._canonical(parsed, code="test") + b"\n"

    def _verify(
        self,
        attestations: dict[str, bytes],
        *,
        now: datetime | None = None,
    ) -> subject.PhysicalFullMatrixV4PhaseInstallationProvenance:
        observed = self.now if now is None else now
        self.clock.value = observed
        with self.fixture._all_owner_clocks(now=observed):
            return subject.verify_physical_full_matrix_v4_phase_installation_provenance(
                config=self.config,
                phase_attestations=attestations,
            )

    def _require(
        self,
        value: subject.PhysicalFullMatrixV4PhaseInstallationProvenance,
        *,
        now: datetime,
    ) -> subject.PhysicalFullMatrixV4PhaseInstallationProvenance:
        self.clock.value = now
        with self.fixture._all_owner_clocks(now=now):
            return subject.require_verified_physical_full_matrix_v4_phase_installation_provenance(
                value,
                config=self.config,
            )

    def test_all_eight_signed_pinned_attestations_are_observed_but_never_authorize(self) -> None:
        result = self._verify(self._attestations())
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
            result.schema,
        )
        self.assertEqual(
            subject.PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES,
            tuple(name for name, _digest in result.phase_attestation_sha256es),
        )
        self.assertIn("not-authorized", result.status)
        self.assertTrue(result.signed_host_installation_observed)
        self.assertFalse(result.host_provider_installation_authorized)
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertIs(result, self._require(result, now=self.now))
        object.__setattr__(result, "host_provider_installation_authorized", True)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "RESULT_INVALID",
        ):
            self._require(result, now=self.now)
        self.assertTrue(all(item.calls == 0 for item in self.phase_adapters.values()))

    def test_in_process_callbacks_raw_dicts_and_lookalike_policy_cannot_substitute_for_signed_evidence(self) -> None:
        fake_callbacks = {
            phase.name: self.phase_adapters[phase.name]
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "ATTESTATION_BYTES_INVALID",
        ):
            self._verify(fake_callbacks)  # type: ignore[arg-type]

        raw_policy = subject.PhysicalFullMatrixV4PhaseInstallationIssuerPolicy(
            schema=subject.PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
            issuer_policy_sha256=_sha("raw-policy"),
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            run_id=self.run_id,
            plan_sha256=self.plan.plan_sha256,
            policy_sha256=self.root_policy_sha256,
            preflight_sha256=self.preflight.preflight_sha256,
            issuer_key_ids={},
            maximum_attestation_lifetime_seconds=60,
        )
        object.__setattr__(raw_policy, "_capability", subject._POLICY_CAPABILITY)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "POLICY_INVALID",
        ):
            subject.verify_physical_full_matrix_v4_phase_installation_provenance(
                config=subject.PhysicalFullMatrixV4PhaseInstallationProvenanceConfig(
                    issuer_policy=raw_policy,
                    enabled=True,
                ),
                phase_attestations=self._attestations(),
            )
        self.assertTrue(all(item.calls == 0 for item in self.phase_adapters.values()))

    def test_exact_signature_and_non_authorizing_status_are_both_required(self) -> None:
        attestations = self._attestations()
        phase = driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
        attestations[phase.name] = self._resign(
            attestations[phase.name],
            phase=phase,
            changes={"execution_authorized": True},
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "ATTESTATION_BINDING_MISMATCH",
        ):
            self._verify(attestations)

        forged = self._attestations()
        parsed = json.loads(forged[phase.name].decode("ascii"))
        parsed["release_sha"] = "f" * 40
        forged[phase.name] = subject._canonical(parsed, code="test") + b"\n"
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "SIGNATURE_INVALID",
        ):
            self._verify(forged)

        signed_release_mismatch = self._attestations()
        signed_release_mismatch[phase.name] = self._resign(
            signed_release_mismatch[phase.name],
            phase=phase,
            changes={"release_sha": "f" * 40},
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "ATTESTATION_BINDING_MISMATCH",
        ):
            self._verify(signed_release_mismatch)

    def test_exact_phase_issuer_and_monotonic_trusted_clock_are_required(self) -> None:
        phase = driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "ISSUER_SIGNER_MISMATCH",
        ):
            subject.build_physical_full_matrix_v4_phase_installation_attestation(
                issuer_policy=self.issuer_policy,
                phase_name=phase.name,
                adapter_implementation_sha256=_sha("implementation:" + phase.name),
                adapter_configuration_sha256=_sha("configuration:" + phase.name),
                attested_at=self.now,
                expires_at=self.now + timedelta(seconds=30),
                issuer_private_key=self.private_keys["webapp_ir"],
            )

        self._verify(self._attestations(), now=self.now + timedelta(seconds=10))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "TRUSTED_CLOCK_REGRESSION",
        ):
            self._verify(self._attestations(), now=self.now + timedelta(seconds=9))

    def test_missing_phase_and_stale_opaque_result_fail_closed(self) -> None:
        attestations = self._attestations(expires_at=self.now + timedelta(seconds=1))
        omitted = dict(attestations)
        omitted.pop(next(iter(omitted)))
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "ATTESTATION_SET_INVALID",
        ):
            self._verify(omitted)

        result = self._verify(attestations)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "RESULT_EXPIRED",
        ):
            self._require(result, now=self.now + timedelta(seconds=2))

    def test_default_off_root_and_duplicate_issuer_keys_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "POLICY_DISABLED",
        ):
            subject.build_physical_full_matrix_v4_phase_installation_issuer_policy(
                config=replace(self.policy_config, enabled=False),
                composition=self.composition,
                materialization_preflight=self.preflight,
            )
        duplicate = next(iter(self.private_keys.values())).public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        with patch.object(subject.os, "geteuid", return_value=0), self.fixture._all_owner_clocks(
            now=self.now
        ), self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "ISSUER_KEYS_INVALID",
        ):
            subject.build_physical_full_matrix_v4_phase_installation_issuer_policy(
                config=replace(
                    self.policy_config,
                    issuer_public_keys={
                        "webapp_fi": duplicate,
                        "webapp_ir": duplicate,
                        "witness": duplicate,
                    },
                ),
                composition=self.composition,
                materialization_preflight=self.preflight,
            )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4PhaseInstallationProvenanceError,
            "DISABLED",
        ):
            subject.verify_physical_full_matrix_v4_phase_installation_provenance(
                config=replace(self.config, enabled=False),
                phase_attestations=self._attestations(),
            )

    def test_module_has_no_operational_host_or_network_import(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("socket", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("boto", source)


class object_with_anchor_methods:
    """Minimal static Witness interface; materialization preflight never calls it."""

    def read_head(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("materialization preflight invoked a Witness anchor")

    def append_commitment(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("materialization preflight invoked a Witness anchor")


if __name__ == "__main__":
    unittest.main()
