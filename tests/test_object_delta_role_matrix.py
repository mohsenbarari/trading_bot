from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    controller_key_id_from_public_key,
)
from core.object_delta_receiver_delivery_binding import (
    ObjectDeltaReceiverDeliveryBinding,
)
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
    OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
    ObjectDeltaRoleMatrixError,
    ObjectDeltaRoleMatrixRoute,
    ObjectDeltaRoleMatrixWriterTerm,
    VerifiedObjectDeltaRoleMatrix,
    active_object_delta_role_matrix_route,
    authorize_object_delta_role_matrix,
    object_delta_role_matrix_site_role,
    require_verified_object_delta_role_matrix,
)
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_batch_attestation import source_key_id_from_public_key
from core.object_delta_source_cutover_publication_gate import (
    ObjectDeltaSourceCutoverPublicationPin,
)
from core.object_delta_transport_binding import ObjectDeltaTransportPolicy, destination_age_recipient


CAMPAIGN = "wa-ir-role-matrix-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
FINGERPRINT = "0123456789abcdef"
NORMAL_GENERATION = "fi-ir-role-matrix-stream-20260731"
PROMOTED_GENERATION = "ir-fi-role-matrix-stream-20260731"


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def policy(*, bucket: str = "private-delta-bucket") -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket=bucket,
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "a" * 30,
        webapp_ir_age_recipient="age1" + "c" * 30,
    )


def source_pin(
    *,
    source_site: str,
    destination_site: str,
    stream_generation_id: str,
    source_public_key: bytes,
    transport_policy: ObjectDeltaTransportPolicy,
    campaign_id: str = CAMPAIGN,
    release_sha: str = RELEASE,
    registry_fingerprint: str = FINGERPRINT,
) -> ObjectDeltaSourceCutoverPublicationPin:
    return ObjectDeltaSourceCutoverPublicationPin(
        binding=ObjectDeltaSourceRuntimeBinding(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=campaign_id,
            release_sha=release_sha,
            stream_generation_id=stream_generation_id,
            expected_registry_fingerprint=registry_fingerprint,
        ),
        expected_source_public_key=source_public_key,
        transport_policy=transport_policy,
    )


def receiver_binding(
    *,
    source_site: str,
    destination_site: str,
    stream_generation_id: str,
    source_public_key: bytes,
    controller_public_key: bytes,
    transport_policy: ObjectDeltaTransportPolicy,
    writer_epoch: int,
    writer_lease_id: str,
    campaign_id: str = CAMPAIGN,
    release_sha: str = RELEASE,
    registry_fingerprint: str = FINGERPRINT,
) -> ObjectDeltaReceiverDeliveryBinding:
    return ObjectDeltaReceiverDeliveryBinding(
        policy=transport_policy,
        permit=ObjectDeltaReceiverDeliveryPermit(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=campaign_id,
            release_sha=release_sha,
            stream_generation_id=stream_generation_id,
            bucket=transport_policy.bucket,
            destination_age_recipient=destination_age_recipient(
                transport_policy,
                destination_site=destination_site,
            ),
            controller_key_id=controller_key_id_from_public_key(controller_public_key),
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
        ),
        source_public_key=source_public_key,
        source_key_id=source_key_id_from_public_key(source_public_key),
        controller_public_key=controller_public_key,
        expected_registry_fingerprint=registry_fingerprint,
    )


class ObjectDeltaRoleMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_signer = Ed25519PrivateKey.generate()
        self.ir_signer = Ed25519PrivateKey.generate()
        self.controller_signer = Ed25519PrivateKey.generate()
        self.fi_key = public_key(self.fi_signer)
        self.ir_key = public_key(self.ir_signer)
        self.controller_key = public_key(self.controller_signer)
        self.policy = policy()
        self.normal = ObjectDeltaRoleMatrixRoute(
            source_pin=source_pin(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                stream_generation_id=NORMAL_GENERATION,
                source_public_key=self.fi_key,
                transport_policy=self.policy,
            ),
            receiver_binding=receiver_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                stream_generation_id=NORMAL_GENERATION,
                source_public_key=self.fi_key,
                controller_public_key=self.controller_key,
                transport_policy=self.policy,
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
            ),
        )
        self.promoted = ObjectDeltaRoleMatrixRoute(
            source_pin=source_pin(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                stream_generation_id=PROMOTED_GENERATION,
                source_public_key=self.ir_key,
                transport_policy=self.policy,
            ),
            receiver_binding=receiver_binding(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                stream_generation_id=PROMOTED_GENERATION,
                source_public_key=self.ir_key,
                controller_public_key=self.controller_key,
                transport_policy=self.policy,
                writer_epoch=8,
                writer_lease_id="writer-lease-8",
            ),
        )

    def authorize_normal(self):
        return authorize_object_delta_role_matrix(
            normal_route=self.normal,
            promoted_route=self.promoted,
            active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
            active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                holder_site="webapp_fi",
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
            ),
        )

    def test_normal_fi_writer_and_promoted_ir_writer_are_exact_mirror_roles(self):
        normal = self.authorize_normal()
        promoted = authorize_object_delta_role_matrix(
            normal_route=self.normal,
            promoted_route=self.promoted,
            active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
            active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                holder_site="webapp_ir",
                writer_epoch=8,
                writer_lease_id="writer-lease-8",
            ),
        )

        self.assertEqual("webapp_fi", active_object_delta_role_matrix_route(normal).source_pin.binding.source_site)
        self.assertEqual("webapp_ir", active_object_delta_role_matrix_route(promoted).source_pin.binding.source_site)
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
            object_delta_role_matrix_site_role(normal, site="webapp_fi").role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
            object_delta_role_matrix_site_role(normal, site="webapp_ir").role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
            object_delta_role_matrix_site_role(promoted, site="webapp_ir").role,
        )
        self.assertEqual(
            OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
            object_delta_role_matrix_site_role(promoted, site="webapp_fi").role,
        )

    def test_release_campaign_registry_route_policy_key_and_term_mismatches_fail_closed(self):
        bad_release = ObjectDeltaRoleMatrixRoute(
            source_pin=self.normal.source_pin,
            receiver_binding=replace(
                self.normal.receiver_binding,
                permit=replace(self.normal.receiver_binding.permit, release_sha="f" * 40),
            ),
        )
        bad_campaign = ObjectDeltaRoleMatrixRoute(
            source_pin=self.normal.source_pin,
            receiver_binding=replace(
                self.normal.receiver_binding,
                permit=replace(
                    self.normal.receiver_binding.permit,
                    campaign_id="different-role-matrix-campaign-20260731",
                ),
            ),
        )
        bad_registry = ObjectDeltaRoleMatrixRoute(
            source_pin=self.normal.source_pin,
            receiver_binding=replace(
                self.normal.receiver_binding,
                expected_registry_fingerprint="f" * 16,
            ),
        )
        bad_policy = ObjectDeltaRoleMatrixRoute(
            source_pin=self.normal.source_pin,
            receiver_binding=receiver_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                stream_generation_id=NORMAL_GENERATION,
                source_public_key=self.fi_key,
                controller_public_key=self.controller_key,
                transport_policy=policy(bucket="other-private-delta-bucket"),
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
            ),
        )
        bad_key = ObjectDeltaRoleMatrixRoute(
            source_pin=self.normal.source_pin,
            receiver_binding=receiver_binding(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                stream_generation_id=NORMAL_GENERATION,
                source_public_key=self.ir_key,
                controller_public_key=self.controller_key,
                transport_policy=self.policy,
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
            ),
        )
        bad_term = ObjectDeltaRoleMatrixRoute(
            source_pin=self.normal.source_pin,
            receiver_binding=replace(
                self.normal.receiver_binding,
                permit=replace(
                    self.normal.receiver_binding.permit,
                    writer_epoch=9,
                    writer_lease_id="writer-lease-9",
                ),
            ),
        )
        cases = (
            (bad_release, "routes do not match"),
            (bad_campaign, "routes do not match"),
            (bad_registry, "registry"),
            (bad_policy, "transport policies"),
            (bad_key, "source keys"),
            (bad_term, "Writer Witness term"),
        )
        for route, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ObjectDeltaRoleMatrixError, pattern):
                    authorize_object_delta_role_matrix(
                        normal_route=route,
                        promoted_route=self.promoted,
                        active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
                        active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                            holder_site="webapp_fi",
                            writer_epoch=7,
                            writer_lease_id="writer-lease-7",
                        ),
                    )

    def test_reversed_role_overlap_split_brain_and_dual_term_use_are_rejected(self):
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixError, "normal FI-writer source route"):
            authorize_object_delta_role_matrix(
                normal_route=self.promoted,
                promoted_route=self.normal,
                active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
                active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                    holder_site="webapp_fi",
                    writer_epoch=7,
                    writer_lease_id="writer-lease-7",
                ),
            )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixError, "holder"):
            authorize_object_delta_role_matrix(
                normal_route=self.normal,
                promoted_route=self.promoted,
                active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
                active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                    holder_site="webapp_ir",
                    writer_epoch=7,
                    writer_lease_id="writer-lease-7",
                ),
            )

        dual_term_promoted = ObjectDeltaRoleMatrixRoute(
            source_pin=self.promoted.source_pin,
            receiver_binding=replace(
                self.promoted.receiver_binding,
                permit=replace(
                    self.promoted.receiver_binding.permit,
                    writer_epoch=7,
                    writer_lease_id="writer-lease-7",
                ),
            ),
        )
        with self.assertRaisesRegex(ObjectDeltaRoleMatrixError, "cannot authorize both directions"):
            authorize_object_delta_role_matrix(
                normal_route=self.normal,
                promoted_route=dual_term_promoted,
                active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
                active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                    holder_site="webapp_fi",
                    writer_epoch=7,
                    writer_lease_id="writer-lease-7",
                ),
            )

    def test_direct_or_replaced_verified_matrix_cannot_activate_a_role(self):
        verified = self.authorize_normal()
        direct = VerifiedObjectDeltaRoleMatrix(
            normal_route=verified.normal_route,
            promoted_route=verified.promoted_route,
            active_mode=verified.active_mode,
            active_writer_term=verified.active_writer_term,
            site_roles=verified.site_roles,
        )
        for candidate in (direct, replace(verified)):
            with self.subTest(candidate=candidate.__class__.__name__):
                with self.assertRaisesRegex(ObjectDeltaRoleMatrixError, "not authorized"):
                    require_verified_object_delta_role_matrix(candidate)

    def test_contract_has_no_runtime_database_or_transport_adapter_dependency(self):
        source = (
            Path(__file__).resolve().parents[1] / "core/object_delta_role_matrix.py"
        ).read_text(encoding="utf-8")
        forbidden_imports = (
            "import sqlalchemy",
            "from sqlalchemy",
            "import boto",
            "from boto",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import aiohttp",
            "from aiohttp",
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "models.",
        )
        self.assertFalse([item for item in forbidden_imports if item in source])


if __name__ == "__main__":
    unittest.main()
