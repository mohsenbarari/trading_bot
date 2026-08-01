"""Adversarial pure-contract tests for four-role immutable storage evidence."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_immutability_preflight as immutable
from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as bridge
from core import physical_arvan_s3_four_role_live_iam_evidence as evidence
from core import physical_arvan_s3_four_role_live_iam_witness_ledger_runtime as runtime_module
from core import physical_ir_to_fi_object_storage_failback_preflight as failback


CAMPAIGN = "four-role-immutable-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 18, 0, 0, tzinfo=timezone.utc)
NONCE = "1" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_immutability_preflight.py"
)


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _outcomes(role: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        [{"operation": item, "outcome": "allowed"} for item in evidence._ROLE_ALLOWED[role]],
        [{"operation": item, "outcome": "denied"} for item in evidence._ROLE_DENIED[role]],
    )


class PhysicalArvanS3FourRoleImmutabilityPreflightTests(unittest.TestCase):
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
        self.live_binding = evidence.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
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
        self.failback_binding = failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256=self.live_binding.four_role_binding_sha256,
            normal_route_scope_sha256=self.live_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=self.live_binding.reverse_route_scope_sha256,
            fi_publisher_identity_sha256=self.live_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.live_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.live_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.live_binding.fi_receiver_identity_sha256,
        )
        self.binding = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-four-role-immutability",
            bucket_access_posture="private",
            normal_object_storage_namespace="physical-wal",
            reverse_object_storage_namespace="physical-failback",
            minimum_retention_days=90,
            normal_route_scope_sha256=self.live_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=self.live_binding.reverse_route_scope_sha256,
            four_role_route_binding_sha256=self.live_binding.four_role_binding_sha256,
            fi_publisher_identity_sha256=self.live_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.live_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.live_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.live_binding.fi_receiver_identity_sha256,
        )
        self.config = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightConfig(
            binding=self.binding, enabled=True, maximum_evidence_age_seconds=120
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runtime(self) -> runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime:
        return runtime_module.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(
            runtime_module.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=self.state_root, evidence_binding=self.live_binding, enabled=True
            )
        )

    def _direction_claims(
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
            binding=self.live_binding,
            nonce=permit.nonce,
            publisher_role=publisher_role,
            object_version_id=f"version-{publisher_role}-{offset}",
            content_sha256=("a" if publisher_role == "fi-publisher" else "b") * 64,
            content_bytes=64 + offset,
        )
        publisher_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.live_binding,
            nonce_permit=permit,
            publisher_role=publisher_role,
            observed_at=first,
            probe_locator=locator,
            allowed_operation_outcomes=publisher_allowed,
            denied_operation_outcomes=publisher_denied,
            role_signer=self.signers[publisher_role],
        )
        publisher = evidence.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
            publisher_raw, binding=self.live_binding, nonce_permit=permit, observed_at=first
        )
        forwarded = first + timedelta(seconds=1)
        forward_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_witness_forward(
            binding=self.live_binding,
            nonce_permit=permit,
            publisher_observation=publisher,
            forwarded_at=forwarded,
            witness_signer=self.witness,
        )
        forward = evidence.verify_physical_arvan_s3_four_role_live_iam_witness_forward(
            forward_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            witness_public_key=_public_key(self.witness),
            observed_at=forwarded,
        )
        received = forwarded + timedelta(seconds=1)
        receiver_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
            binding=self.live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received,
            allowed_operation_outcomes=receiver_allowed,
            denied_operation_outcomes=receiver_denied,
            role_signer=self.signers[receiver_role],
        )
        receiver = evidence.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
            receiver_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received,
        )
        return publisher, forward, receiver

    def _admission(self) -> bridge.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
        runtime = self._runtime()
        _state, permit_raw = runtime_module.issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
            runtime=runtime,
            nonce=NONCE,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            witness_signer=self.witness,
        )
        permit = evidence.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
            permit_raw,
            binding=self.live_binding,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW,
        )
        normal_publisher, normal_forward, normal_receiver = self._direction_claims(
            publisher_role="fi-publisher", permit=permit, offset=1
        )
        reverse_publisher, reverse_forward, reverse_receiver = self._direction_claims(
            publisher_role="ir-publisher", permit=permit, offset=10
        )
        _state, aggregate = runtime_module.seal_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
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
        return bridge.admit_physical_arvan_s3_four_role_live_iam_durable_aggregate(
            runtime=runtime,
            aggregate=aggregate,
            witness_public_key=_public_key(self.witness),
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW + timedelta(seconds=21),
        )

    def _direction(
        self,
        *,
        direction: str,
        publisher_role: str,
        receiver_role: str,
        namespace: str,
        publisher_identity: str,
        receiver_identity: str,
        nonce: str,
        content_hash: str,
        version: str,
    ) -> immutable.PhysicalArvanS3FourRoleImmutabilityDirectionObservation:
        object_key = immutable.derive_physical_arvan_s3_four_role_immutability_probe_object_key(
            binding=self.binding, direction=direction, probe_nonce_sha256=nonce
        )
        immutable_version = immutable.PhysicalArvanS3FourRoleImmutableVersionObservation(
            probe_nonce_sha256=nonce,
            object_key=object_key,
            object_version_id=version,
            content_sha256=content_hash,
            content_bytes=4096,
            retention_until=NOW + timedelta(days=91),
            exact_head_version_id=version,
            exact_get_version_id=version,
            exact_get_content_sha256=content_hash,
            exact_get_content_bytes=4096,
        )
        return immutable.PhysicalArvanS3FourRoleImmutabilityDirectionObservation(
            direction=direction,
            publisher_role=publisher_role,
            receiver_role=receiver_role,
            object_storage_namespace=namespace,
            publisher_identity_sha256=publisher_identity,
            receiver_identity_sha256=receiver_identity,
            acl_posture="private-canonical-owner-only-v1",
            versioning_status="Enabled",
            retention_mode="s3-object-lock-compliance-v1",
            retention_policy_evidence_sha256="e" * 64,
            retention_days=90,
            immutable_version=immutable_version,
            publisher_create_only_outcome="create-only-succeeded",
            publisher_overwrite_outcome="access-denied",
            publisher_delete_object_outcome="access-denied",
            publisher_delete_version_outcome="access-denied",
            receiver_exact_head_outcome="exact-version-head-succeeded",
            receiver_exact_get_outcome="exact-version-get-succeeded",
            receiver_put_outcome="access-denied",
            receiver_delete_object_outcome="access-denied",
            receiver_delete_version_outcome="access-denied",
            receiver_list_bucket_outcome="access-denied",
            receiver_list_versions_outcome="access-denied",
        )

    def _directions(self) -> tuple[
        immutable.PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
        immutable.PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
    ]:
        return (
            self._direction(
                direction="fi-publisher-to-ir-receiver",
                publisher_role="fi-publisher",
                receiver_role="ir-receiver",
                namespace="physical-wal",
                publisher_identity=self.binding.fi_publisher_identity_sha256,
                receiver_identity=self.binding.ir_receiver_identity_sha256,
                nonce="a" * 64,
                content_hash="b" * 64,
                version="version-normal-001",
            ),
            self._direction(
                direction="ir-publisher-to-fi-receiver",
                publisher_role="ir-publisher",
                receiver_role="fi-receiver",
                namespace="physical-failback",
                publisher_identity=self.binding.ir_publisher_identity_sha256,
                receiver_identity=self.binding.fi_receiver_identity_sha256,
                nonce="c" * 64,
                content_hash="d" * 64,
                version="version-reverse-001",
            ),
        )

    def _observation(
        self, admission: bridge.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission
    ) -> immutable.PhysicalArvanS3FourRoleImmutabilityPreflightObservation:
        normal, reverse = self._directions()
        return immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
            binding=self.binding,
            admission=admission,
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            normal_direction=normal,
            reverse_direction=reverse,
            observed_at=NOW + timedelta(seconds=22),
        )

    def _verify(
        self,
        observation: immutable.PhysicalArvanS3FourRoleImmutabilityPreflightObservation,
        admission: bridge.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
        *,
        observed_at: datetime | None = None,
    ) -> immutable.VerifiedPhysicalArvanS3FourRoleImmutabilityPreflight:
        return immutable.verify_physical_arvan_s3_four_role_immutability_preflight(
            observation,
            config=self.config,
            admission=admission,
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            observed_at=observed_at or NOW + timedelta(seconds=23),
        )

    def test_both_directions_verify_and_project(self) -> None:
        admission = self._admission()
        verified = self._verify(self._observation(admission), admission)
        projection = immutable.project_verified_physical_arvan_s3_four_role_immutability_preflight(
            verified,
            config=self.config,
            admission=admission,
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW + timedelta(seconds=24),
        )
        self.assertEqual(CAMPAIGN, projection.campaign_id)
        self.assertEqual(90, projection.minimum_retention_days)
        self.assertEqual(admission.aggregate_sha256, projection.admission_aggregate_sha256)

    def test_role_direction_scope_identity_retention_and_version_mutations_fail(self) -> None:
        admission = self._admission()
        normal, reverse = self._directions()
        mutations = (
            replace(normal, publisher_role="ir-publisher"),
            replace(normal, direction="ir-publisher-to-fi-receiver"),
            replace(normal, publisher_identity_sha256=self.binding.ir_publisher_identity_sha256),
            replace(normal, retention_days=89),
            replace(
                normal,
                immutable_version=replace(
                    normal.immutable_version,
                    retention_until=NOW + timedelta(days=90),
                ),
            ),
            replace(
                normal,
                immutable_version=replace(
                    normal.immutable_version,
                    retention_until=(NOW + timedelta(days=91)).astimezone(
                        timezone(timedelta(hours=3, minutes=30))
                    ),
                ),
            ),
            replace(normal, immutable_version=replace(normal.immutable_version, exact_get_version_id="other-version")),
            replace(normal, receiver_list_versions_outcome="succeeded"),
        )
        for changed in mutations:
            with self.subTest(changed=changed):
                with self.assertRaises(immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError):
                    immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                        binding=self.binding,
                        admission=admission,
                        live_iam_binding=self.live_binding,
                        failback_binding=self.failback_binding,
                        normal_direction=changed,
                        reverse_direction=reverse,
                        observed_at=NOW + timedelta(seconds=22),
                    )
        bad_binding = replace(self.binding, normal_route_scope_sha256="9" * 64)
        with self.assertRaisesRegex(immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError, "BINDING_ADMISSION_MISMATCH"):
            immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=bad_binding,
                admission=admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                normal_direction=normal,
                reverse_direction=reverse,
                observed_at=NOW + timedelta(seconds=22),
            )

        incompatible_policy = replace(reverse, retention_policy_evidence_sha256="f" * 64)
        with self.assertRaisesRegex(
            immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError,
            "SHARED_BUCKET_POLICY_MISMATCH",
        ):
            immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=self.binding,
                admission=admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                normal_direction=normal,
                reverse_direction=incompatible_policy,
                observed_at=NOW + timedelta(seconds=22),
            )

        incompatible_retention = replace(
            reverse,
            retention_days=91,
            immutable_version=replace(
                reverse.immutable_version,
                retention_until=NOW + timedelta(days=92),
            ),
        )
        with self.assertRaisesRegex(
            immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError,
            "SHARED_BUCKET_POLICY_MISMATCH",
        ):
            immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=self.binding,
                admission=admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                normal_direction=normal,
                reverse_direction=incompatible_retention,
                observed_at=NOW + timedelta(seconds=22),
            )

        collided_nonce = normal.immutable_version.probe_nonce_sha256
        collided_reverse_version = replace(
            reverse.immutable_version,
            probe_nonce_sha256=collided_nonce,
            object_key=immutable.derive_physical_arvan_s3_four_role_immutability_probe_object_key(
                binding=self.binding,
                direction="ir-publisher-to-fi-receiver",
                probe_nonce_sha256=collided_nonce,
            ),
        )
        with self.assertRaisesRegex(
            immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError,
            "DIRECTION_SELECTOR_COLLISION",
        ):
            immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=self.binding,
                admission=admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                normal_direction=normal,
                reverse_direction=replace(reverse, immutable_version=collided_reverse_version),
                observed_at=NOW + timedelta(seconds=22),
            )

        collided_version = replace(
            reverse.immutable_version,
            object_version_id=normal.immutable_version.object_version_id,
            exact_head_version_id=normal.immutable_version.object_version_id,
            exact_get_version_id=normal.immutable_version.object_version_id,
        )
        with self.assertRaisesRegex(
            immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError,
            "DIRECTION_SELECTOR_COLLISION",
        ):
            immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=self.binding,
                admission=admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                normal_direction=normal,
                reverse_direction=replace(reverse, immutable_version=collided_version),
                observed_at=NOW + timedelta(seconds=22),
            )

    def test_staleness_disabled_config_and_admission_forgery_fail(self) -> None:
        admission = self._admission()
        observation = self._observation(admission)
        with self.assertRaisesRegex(immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError, "STALE"):
            self._verify(observation, admission, observed_at=NOW + timedelta(seconds=143))
        disabled = replace(self.config, enabled=False)
        with self.assertRaisesRegex(immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError, "DISABLED"):
            immutable.verify_physical_arvan_s3_four_role_immutability_preflight(
                observation,
                config=disabled,
                admission=admission,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                observed_at=NOW + timedelta(seconds=23),
            )
        forged = replace(admission)
        with self.assertRaises(immutable.PhysicalArvanS3FourRoleImmutabilityPreflightError):
            immutable.build_physical_arvan_s3_four_role_immutability_preflight_observation(
                binding=self.binding,
                admission=forged,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                normal_direction=self._directions()[0],
                reverse_direction=self._directions()[1],
                observed_at=NOW + timedelta(seconds=22),
            )

    def test_source_has_no_sdk_network_or_old_immutability_dependency(self) -> None:
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
            & {"boto3", "botocore", "socket", "subprocess", "requests", "os", "pathlib", "urllib"}
        )
        self.assertNotIn("physical_arvan_immutability", source)
        self.assertNotIn("paired", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
