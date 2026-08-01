from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_baseline_manifest import build_object_delta_baseline_manifest
from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    controller_key_id_from_public_key,
)
from core.object_delta_receiver_delivery_binding import ObjectDeltaReceiverDeliveryBinding
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
    OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
    ObjectDeltaRoleMatrixRoute,
    ObjectDeltaRoleMatrixWriterTerm,
    authorize_object_delta_role_matrix,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixActivation,
    VerifiedObjectDeltaRoleMatrixRouteGenerations,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    bootstrap_object_delta_role_matrix_activation,
    build_object_delta_role_matrix_witnessed_term_proof,
    project_active_object_delta_role_matrix_role,
    require_live_object_delta_role_matrix_activation,
    require_live_object_delta_role_matrix_witnessed_term,
    require_verified_object_delta_role_matrix_activation,
    require_verified_object_delta_role_matrix_route_generations,
    rollover_object_delta_role_matrix_activation,
    verify_object_delta_role_matrix_route_generations,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_attestation import source_key_id_from_public_key
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
)
from core.object_delta_source_cutover_publication_gate import ObjectDeltaSourceCutoverPublicationPin
from core.object_delta_transport_binding import ObjectDeltaTransportPolicy, destination_age_recipient


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "wa-role-rollover-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
FINGERPRINT = "0123456789abcdef"


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def transport_policy(*, bucket: str = "private-delta-bucket") -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket=bucket,
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "a" * 30,
        webapp_ir_age_recipient="age1" + "c" * 30,
    )


class ObjectDeltaRoleMatrixRolloverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_signer = Ed25519PrivateKey.generate()
        self.ir_signer = Ed25519PrivateKey.generate()
        self.controller_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.fi_key = public_key(self.fi_signer)
        self.ir_key = public_key(self.ir_signer)
        self.controller_key = public_key(self.controller_signer)
        self.witness_key = public_key(self.witness_signer)
        self.policy = transport_policy()

        self.normal7, self.normal7_cutover = self.route_artifact(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            stream_generation_id="fi-ir-normal-term-7",
            source_signer=self.fi_signer,
            source_public_key=self.fi_key,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
        )
        self.promoted6, self.promoted6_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-promoted-term-6",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=6,
            writer_lease_id="writer-lease-6",
        )
        self.initial_routes = self.verify_routes(
            normal_route=self.normal7,
            normal_cutover=self.normal7_cutover,
            promoted_route=self.promoted6,
            promoted_cutover=self.promoted6_cutover,
        )
        self.term7 = self.witnessed_term(
            holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
        )
        self.initial_matrix = authorize_object_delta_role_matrix(
            normal_route=self.normal7,
            promoted_route=self.promoted6,
            active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
            active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                holder_site="webapp_fi",
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
            ),
        )
        self.initial_activation = bootstrap_object_delta_role_matrix_activation(
            prior_verified_matrix=self.initial_matrix,
            witnessed_term=self.term7,
            route_generations=self.initial_routes,
            now=NOW,
        )

    def witnessed_term(
        self,
        *,
        holder_site: str,
        writer_epoch: int,
        writer_lease_id: str,
        expires_at: datetime | None = None,
    ) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site=holder_site,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            witness_transition_id=f"transition-{writer_epoch}-{writer_lease_id}",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=expires_at or NOW + timedelta(seconds=50),
            witness_signer=self.witness_signer,
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=self.witness_key,
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )

    def source_pin(
        self,
        *,
        source_site: str,
        destination_site: str,
        stream_generation_id: str,
        source_public_key: bytes,
        policy: ObjectDeltaTransportPolicy | None = None,
        campaign_id: str = CAMPAIGN,
    ) -> ObjectDeltaSourceCutoverPublicationPin:
        return ObjectDeltaSourceCutoverPublicationPin(
            binding=ObjectDeltaSourceRuntimeBinding(
                source_site=source_site,
                destination_site=destination_site,
                campaign_id=campaign_id,
                release_sha=RELEASE,
                stream_generation_id=stream_generation_id,
                expected_registry_fingerprint=FINGERPRINT,
            ),
            expected_source_public_key=source_public_key,
            transport_policy=policy or self.policy,
        )

    def receiver_binding(
        self,
        *,
        source_site: str,
        destination_site: str,
        stream_generation_id: str,
        source_public_key: bytes,
        writer_epoch: int,
        writer_lease_id: str,
        policy: ObjectDeltaTransportPolicy | None = None,
        controller_public_key: bytes | None = None,
        campaign_id: str = CAMPAIGN,
    ) -> ObjectDeltaReceiverDeliveryBinding:
        selected_policy = policy or self.policy
        controller_key = controller_public_key or self.controller_key
        return ObjectDeltaReceiverDeliveryBinding(
            policy=selected_policy,
            permit=ObjectDeltaReceiverDeliveryPermit(
                source_site=source_site,
                destination_site=destination_site,
                campaign_id=campaign_id,
                release_sha=RELEASE,
                stream_generation_id=stream_generation_id,
                bucket=selected_policy.bucket,
                destination_age_recipient=destination_age_recipient(
                    selected_policy,
                    destination_site=destination_site,
                ),
                controller_key_id=controller_key_id_from_public_key(controller_key),
                writer_epoch=writer_epoch,
                writer_lease_id=writer_lease_id,
            ),
            source_public_key=source_public_key,
            source_key_id=source_key_id_from_public_key(source_public_key),
            controller_public_key=controller_key,
            expected_registry_fingerprint=FINGERPRINT,
        )

    def route_artifact(
        self,
        *,
        source_site: str,
        destination_site: str,
        stream_generation_id: str,
        source_signer: Ed25519PrivateKey,
        source_public_key: bytes,
        writer_epoch: int,
        writer_lease_id: str,
        policy: ObjectDeltaTransportPolicy | None = None,
        controller_public_key: bytes | None = None,
        campaign_id: str = CAMPAIGN,
    ) -> tuple[ObjectDeltaRoleMatrixRoute, dict]:
        selected_policy = policy or self.policy
        pin = self.source_pin(
            source_site=source_site,
            destination_site=destination_site,
            stream_generation_id=stream_generation_id,
            source_public_key=source_public_key,
            policy=selected_policy,
            campaign_id=campaign_id,
        )
        route = ObjectDeltaRoleMatrixRoute(
            source_pin=pin,
            receiver_binding=self.receiver_binding(
                source_site=source_site,
                destination_site=destination_site,
                stream_generation_id=stream_generation_id,
                source_public_key=source_public_key,
                writer_epoch=writer_epoch,
                writer_lease_id=writer_lease_id,
                policy=selected_policy,
                controller_public_key=controller_public_key,
                campaign_id=campaign_id,
            ),
        )
        snapshot = {
            "source_generation": f"{source_site}-{stream_generation_id}-baseline",
            "snapshot_id": "20260731T120000Z-0123456789abcdef",
            "release_sha": RELEASE,
            "alembic_revision": "f2c7d8e9a0b1",
            "manifest_object_key": f"campaigns/three-site/{stream_generation_id}/snapshot.age",
            "manifest_object_version_id": f"snapshot-version-{writer_epoch}",
            "manifest_ciphertext_sha256": "a" * 64,
            "manifest_ciphertext_bytes": 1024,
            "database_sha256": "b" * 64,
            "uploads_sha256": "c" * 64,
        }
        gate_id = str(uuid4())
        baseline = build_object_delta_baseline_manifest(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=campaign_id,
            release_sha=RELEASE,
            stream_generation_id=stream_generation_id,
            registry_fingerprint=FINGERPRINT,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            snapshot=snapshot,
            write_gate_id=gate_id,
            source_signer=source_signer,
        )
        record = ObjectDeltaSourceCutoverRecord(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=campaign_id,
            release_sha=RELEASE,
            stream_generation_id=stream_generation_id,
            state="baseline_published",
            registry_fingerprint=FINGERPRINT,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            write_gate_id=gate_id,
            source_generation=snapshot["source_generation"],
            snapshot_id=snapshot["snapshot_id"],
            alembic_revision=snapshot["alembic_revision"],
            snapshot_manifest_object_key=snapshot["manifest_object_key"],
            snapshot_manifest_object_version_id=snapshot["manifest_object_version_id"],
            snapshot_manifest_ciphertext_sha256=snapshot["manifest_ciphertext_sha256"],
            snapshot_manifest_ciphertext_bytes=snapshot["manifest_ciphertext_bytes"],
            database_sha256=snapshot["database_sha256"],
            uploads_sha256=snapshot["uploads_sha256"],
            baseline_manifest_object_key=f"campaigns/three-site/{stream_generation_id}/baseline.age",
            baseline_manifest_object_version_id=f"baseline-version-{writer_epoch}",
            baseline_manifest_ciphertext_sha256="d" * 64,
            baseline_manifest_ciphertext_bytes=2048,
        )
        return route, build_object_delta_source_cutover_attestation(
            cutover=record,
            baseline_manifest=baseline,
            source_signer=source_signer,
        )

    def verify_routes(
        self,
        *,
        normal_route: ObjectDeltaRoleMatrixRoute,
        normal_cutover: dict,
        promoted_route: ObjectDeltaRoleMatrixRoute,
        promoted_cutover: dict,
    ) -> VerifiedObjectDeltaRoleMatrixRouteGenerations:
        return verify_object_delta_role_matrix_route_generations(
            normal_route=normal_route,
            normal_source_cutover_attestation=normal_cutover,
            promoted_route=promoted_route,
            promoted_source_cutover_attestation=promoted_cutover,
        )

    def promoted8(self) -> tuple[ObjectDeltaRoleMatrixRoute, dict]:
        return self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-promoted-term-8",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
        )

    def promotion_activation(self) -> VerifiedObjectDeltaRoleMatrixActivation:
        promoted8, promoted8_cutover = self.promoted8()
        return rollover_object_delta_role_matrix_activation(
            prior_activation=self.initial_activation,
            witnessed_term=self.witnessed_term(
                holder_site="webapp_ir",
                writer_epoch=8,
                writer_lease_id="writer-lease-8",
            ),
            fresh_route_generations=self.verify_routes(
                normal_route=self.normal7,
                normal_cutover=self.normal7_cutover,
                promoted_route=promoted8,
                promoted_cutover=promoted8_cutover,
            ),
            next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
            now=NOW,
        )

    def test_normal_to_ir_promotion_to_fi_failback_projects_only_current_roles(self) -> None:
        promoted = self.promotion_activation()
        normal9, normal9_cutover = self.route_artifact(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            stream_generation_id="fi-ir-normal-term-9",
            source_signer=self.fi_signer,
            source_public_key=self.fi_key,
            writer_epoch=9,
            writer_lease_id="writer-lease-9",
        )
        # The promoted counterpart remains exactly the verified active
        # promotion artifact; failback may replace only FI's normal route.
        promoted_routes = promoted._route_generations
        failback = rollover_object_delta_role_matrix_activation(
            prior_activation=promoted,
            witnessed_term=self.witnessed_term(
                holder_site="webapp_fi",
                writer_epoch=9,
                writer_lease_id="writer-lease-9",
            ),
            fresh_route_generations=self.verify_routes(
                normal_route=normal9,
                normal_cutover=normal9_cutover,
                promoted_route=promoted_routes.promoted_route,
                promoted_cutover=promoted_routes.promoted_source_cutover_attestation,
            ),
            next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
            now=NOW,
        )

        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
            project_active_object_delta_role_matrix_role(
                self.initial_activation, site="webapp_fi", now=NOW
            ).role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
            project_active_object_delta_role_matrix_role(
                self.initial_activation, site="webapp_ir", now=NOW
            ).role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
            project_active_object_delta_role_matrix_role(promoted, site="webapp_ir", now=NOW).role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
            project_active_object_delta_role_matrix_role(promoted, site="webapp_fi", now=NOW).role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
            project_active_object_delta_role_matrix_role(failback, site="webapp_fi", now=NOW).role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
            project_active_object_delta_role_matrix_role(failback, site="webapp_ir", now=NOW).role,
        )
        self.assertEqual(3, len(failback._history))
        require_live_object_delta_role_matrix_activation(failback, now=NOW)

    def test_term_regression_same_epoch_and_old_permit_are_rejected(self) -> None:
        promoted6_new, promoted6_new_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-promoted-term-6-new",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=6,
            writer_lease_id="writer-lease-6-new",
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "term regression"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=self.initial_activation,
                witnessed_term=self.witnessed_term(
                    holder_site="webapp_ir",
                    writer_epoch=6,
                    writer_lease_id="writer-lease-6-new",
                ),
                fresh_route_generations=self.verify_routes(
                    normal_route=self.normal7,
                    normal_cutover=self.normal7_cutover,
                    promoted_route=promoted6_new,
                    promoted_cutover=promoted6_new_cutover,
                ),
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
                now=NOW,
            )

        promoted7_new, promoted7_new_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-promoted-term-7-new",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=7,
            writer_lease_id="writer-lease-7-other",
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "term regression"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=self.initial_activation,
                witnessed_term=self.witnessed_term(
                    holder_site="webapp_ir",
                    writer_epoch=7,
                    writer_lease_id="writer-lease-7-other",
                ),
                fresh_route_generations=self.verify_routes(
                    normal_route=self.normal7,
                    normal_cutover=self.normal7_cutover,
                    promoted_route=promoted7_new,
                    promoted_cutover=promoted7_new_cutover,
                ),
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
                now=NOW,
            )

        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "do not admit"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=self.initial_activation,
                witnessed_term=self.witnessed_term(
                    holder_site="webapp_ir",
                    writer_epoch=8,
                    writer_lease_id="writer-lease-8",
                ),
                fresh_route_generations=self.initial_routes,
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
                now=NOW,
            )

    def test_old_generation_and_old_cutover_artifacts_cannot_be_reenabled(self) -> None:
        promoted = self.promotion_activation()
        normal9_replayed_generation, normal9_replayed_cutover = self.route_artifact(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            # The term is new but the generation was already active at term 7.
            stream_generation_id="fi-ir-normal-term-7",
            source_signer=self.fi_signer,
            source_public_key=self.fi_key,
            writer_epoch=9,
            writer_lease_id="writer-lease-9",
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "generation replay"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=promoted,
                witnessed_term=self.witnessed_term(
                    holder_site="webapp_fi",
                    writer_epoch=9,
                    writer_lease_id="writer-lease-9",
                ),
                fresh_route_generations=self.verify_routes(
                    normal_route=normal9_replayed_generation,
                    normal_cutover=normal9_replayed_cutover,
                    promoted_route=promoted._route_generations.promoted_route,
                    promoted_cutover=promoted._route_generations.promoted_source_cutover_attestation,
                ),
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
                now=NOW,
            )

        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "does not match its receiver permit term"):
            self.verify_routes(
                normal_route=normal9_replayed_generation,
                # A signed older cutover has the old term and is never enough.
                normal_cutover=self.normal7_cutover,
                promoted_route=promoted._route_generations.promoted_route,
                promoted_cutover=promoted._route_generations.promoted_source_cutover_attestation,
            )

    def test_split_brain_and_policy_key_campaign_mismatch_fail_closed(self) -> None:
        normal8, normal8_cutover = self.route_artifact(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            stream_generation_id="fi-ir-normal-term-8",
            source_signer=self.fi_signer,
            source_public_key=self.fi_key,
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
        )
        promoted8, promoted8_cutover = self.promoted8()
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "valid two-direction"):
            self.verify_routes(
                normal_route=normal8,
                normal_cutover=normal8_cutover,
                promoted_route=promoted8,
                promoted_cutover=promoted8_cutover,
            )

        changed_policy_route, changed_policy_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-policy-mismatch-term-8",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
            policy=transport_policy(bucket="other-private-delta-bucket"),
        )
        with self.assertRaises(ObjectDeltaRoleMatrixRolloverError):
            self.verify_routes(
                normal_route=self.normal7,
                normal_cutover=self.normal7_cutover,
                promoted_route=changed_policy_route,
                promoted_cutover=changed_policy_cutover,
            )

        other_ir_signer = Ed25519PrivateKey.generate()
        key_changed_route, key_changed_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-key-mismatch-term-8",
            source_signer=other_ir_signer,
            source_public_key=public_key(other_ir_signer),
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "source key changed"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=self.initial_activation,
                witnessed_term=self.witnessed_term(
                    holder_site="webapp_ir",
                    writer_epoch=8,
                    writer_lease_id="writer-lease-8",
                ),
                fresh_route_generations=self.verify_routes(
                    normal_route=self.normal7,
                    normal_cutover=self.normal7_cutover,
                    promoted_route=key_changed_route,
                    promoted_cutover=key_changed_cutover,
                ),
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
                now=NOW,
            )

        campaign_changed_route, campaign_changed_cutover = self.route_artifact(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-campaign-mismatch-term-8",
            source_signer=self.ir_signer,
            source_public_key=self.ir_key,
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
            campaign_id="other-role-rollover-20260731",
        )
        with self.assertRaises(ObjectDeltaRoleMatrixRolloverError):
            self.verify_routes(
                normal_route=self.normal7,
                normal_cutover=self.normal7_cutover,
                promoted_route=campaign_changed_route,
                promoted_cutover=campaign_changed_cutover,
            )

    def test_inactive_route_swap_same_role_replay_and_expired_term_fail_closed(self) -> None:
        promoted8, promoted8_cutover = self.promoted8()
        changed_normal, changed_normal_cutover = self.route_artifact(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            stream_generation_id="fi-ir-normal-should-not-change-term-7",
            source_signer=self.fi_signer,
            source_public_key=self.fi_key,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "inactive normal"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=self.initial_activation,
                witnessed_term=self.witnessed_term(
                    holder_site="webapp_ir",
                    writer_epoch=8,
                    writer_lease_id="writer-lease-8",
                ),
                fresh_route_generations=self.verify_routes(
                    normal_route=changed_normal,
                    normal_cutover=changed_normal_cutover,
                    promoted_route=promoted8,
                    promoted_cutover=promoted8_cutover,
                ),
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
                now=NOW,
            )

        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "same-role"):
            rollover_object_delta_role_matrix_activation(
                prior_activation=self.initial_activation,
                witnessed_term=self.term7,
                fresh_route_generations=self.initial_routes,
                next_active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
                now=NOW,
            )

        expired_proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_ir",
            writer_epoch=8,
            writer_lease_id="writer-lease-8",
            witness_transition_id="expired-transition-8",
            issued_at=NOW - timedelta(seconds=90),
            expires_at=NOW - timedelta(seconds=10),
            witness_signer=self.witness_signer,
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "expired"):
            verify_object_delta_role_matrix_witnessed_term(
                expired_proof,
                witness_public_key=self.witness_key,
                maximum_lease_duration_seconds=90,
                safety_margin_seconds=5,
                now=NOW,
            )

    def test_direct_or_replaced_term_route_and_activation_capabilities_are_rejected(self) -> None:
        direct_term = VerifiedObjectDeltaRoleMatrixWitnessedTerm(
            canonical_proof=self.term7.canonical_proof,
            witness_public_key=self.term7.witness_public_key,
            maximum_lease_duration_seconds=self.term7.maximum_lease_duration_seconds,
            safety_margin_seconds=self.term7.safety_margin_seconds,
            holder_site=self.term7.holder_site,
            writer_epoch=self.term7.writer_epoch,
            writer_lease_id=self.term7.writer_lease_id,
            witness_transition_id=self.term7.witness_transition_id,
            issued_at=self.term7.issued_at,
            expires_at=self.term7.expires_at,
            proof_sha256=self.term7.proof_sha256,
        )
        for candidate in (direct_term, replace(self.term7)):
            with self.subTest(capability="term"):
                with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "not authorized"):
                    require_live_object_delta_role_matrix_witnessed_term(candidate, now=NOW)

        direct_routes = VerifiedObjectDeltaRoleMatrixRouteGenerations(
            normal_route=self.initial_routes.normal_route,
            normal_source_cutover_attestation=self.initial_routes.normal_source_cutover_attestation,
            promoted_route=self.initial_routes.promoted_route,
            promoted_source_cutover_attestation=self.initial_routes.promoted_source_cutover_attestation,
        )
        for candidate in (direct_routes, replace(self.initial_routes)):
            with self.subTest(capability="routes"):
                with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "not authorized"):
                    require_verified_object_delta_role_matrix_route_generations(candidate)

        direct_activation = VerifiedObjectDeltaRoleMatrixActivation(
            _matrix=self.initial_activation._matrix,
            _witnessed_term=self.initial_activation._witnessed_term,
            _route_generations=self.initial_activation._route_generations,
            _history=self.initial_activation._history,
        )
        for candidate in (direct_activation, replace(self.initial_activation)):
            with self.subTest(capability="activation"):
                with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "not authorized"):
                    require_verified_object_delta_role_matrix_activation(candidate, now=NOW)

    def test_raw_witness_proof_duplicate_and_noncanonical_forms_fail_closed(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "duplicate JSON fields"):
            verify_object_delta_role_matrix_witnessed_term(
                b'{"holder_site":"webapp_fi","holder_site":"webapp_ir"}',
                witness_public_key=self.witness_key,
                maximum_lease_duration_seconds=90,
                safety_margin_seconds=5,
                now=NOW,
            )

        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "not canonical"):
            verify_object_delta_role_matrix_witnessed_term(
                b" " + self.term7.canonical_proof,
                witness_public_key=self.witness_key,
                maximum_lease_duration_seconds=90,
                safety_margin_seconds=5,
                now=NOW,
            )

    def test_raw_writer_term_is_not_a_rollover_authority(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixRolloverError, "Witness term proof is invalid"):
            verify_object_delta_role_matrix_witnessed_term(
                ObjectDeltaRoleMatrixWriterTerm(
                    holder_site="webapp_fi",
                    writer_epoch=7,
                    writer_lease_id="writer-lease-7",
                ),
                witness_public_key=self.witness_key,
                maximum_lease_duration_seconds=90,
                safety_margin_seconds=5,
                now=NOW,
            )

    def test_contract_has_no_runtime_database_network_or_filesystem_adapter_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "core/object_delta_role_matrix_rollover.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "import sqlalchemy",
            "from sqlalchemy",
            "models.",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import aiohttp",
            "from aiohttp",
            "import socket",
            "from socket",
            "import subprocess",
            "from subprocess",
            "import os",
            "from os",
            "from scripts",
            "import scripts",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":
    unittest.main()
