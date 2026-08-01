"""Adversarial tests for the pure Witness-routed four-role IAM contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_live_iam_evidence as iam


CAMPAIGN = "four-role-live-iam-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 10, 0, 0, tzinfo=timezone.utc)
NONCE = "1" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_live_iam_evidence.py"
)


def _public_key(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _outcomes(role: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        [{"operation": item, "outcome": "allowed"} for item in iam._ROLE_ALLOWED[role]],
        [{"operation": item, "outcome": "denied"} for item in iam._ROLE_DENIED[role]],
    )


class PhysicalArvanS3FourRoleLiveIamEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.witness = Ed25519PrivateKey.generate()
        self.signers = {
            "fi-publisher": Ed25519PrivateKey.generate(),
            "ir-receiver": Ed25519PrivateKey.generate(),
            "ir-publisher": Ed25519PrivateKey.generate(),
            "fi-receiver": Ed25519PrivateKey.generate(),
        }
        self.binding = iam.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
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

    def _permit(self, *, nonce: str = NONCE) -> tuple[iam.PhysicalArvanS3FourRoleLiveIamNonceLedger, iam.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit]:
        ledger = iam.make_physical_arvan_s3_four_role_live_iam_nonce_ledger(binding=self.binding)
        ledger, raw = iam.issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
            binding=self.binding,
            ledger=ledger,
            nonce=nonce,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            witness_signer=self.witness,
        )
        permit = iam.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
            raw,
            binding=self.binding,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW,
        )
        return ledger, permit

    def _direction(
        self,
        *,
        publisher_role: str,
        permit: iam.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
        offset: int,
    ) -> tuple[
        iam.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
        iam.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
        iam.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
        bytes,
        bytes,
        bytes,
    ]:
        receiver_role = iam._RECEIVER_BY_DIRECTION[iam._DIRECTION_BY_PUBLISHER[publisher_role]]
        publisher_allowed, publisher_denied = _outcomes(publisher_role)
        receiver_allowed, receiver_denied = _outcomes(receiver_role)
        first = NOW + timedelta(seconds=offset)
        locator = iam.make_physical_arvan_s3_live_iam_probe_locator(
            binding=self.binding,
            nonce=permit.nonce,
            publisher_role=publisher_role,
            object_version_id=f"version-{publisher_role}-{offset}",
            content_sha256=("a" if publisher_role == "fi-publisher" else "b") * 64,
            content_bytes=103 + offset,
        )
        publisher_raw = iam.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.binding,
            nonce_permit=permit,
            publisher_role=publisher_role,
            observed_at=first,
            probe_locator=locator,
            allowed_operation_outcomes=publisher_allowed,
            denied_operation_outcomes=publisher_denied,
            role_signer=self.signers[publisher_role],
        )
        publisher = iam.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
            publisher_raw,
            binding=self.binding,
            nonce_permit=permit,
            observed_at=first,
        )
        forward_at = first + timedelta(seconds=1)
        forward_raw = iam.seal_physical_arvan_s3_four_role_live_iam_witness_forward(
            binding=self.binding,
            nonce_permit=permit,
            publisher_observation=publisher,
            forwarded_at=forward_at,
            witness_signer=self.witness,
        )
        forward = iam.verify_physical_arvan_s3_four_role_live_iam_witness_forward(
            forward_raw,
            binding=self.binding,
            nonce_permit=permit,
            witness_public_key=_public_key(self.witness),
            observed_at=forward_at,
        )
        receiver_at = forward_at + timedelta(seconds=1)
        receiver_raw = iam.seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
            binding=self.binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=receiver_at,
            allowed_operation_outcomes=receiver_allowed,
            denied_operation_outcomes=receiver_denied,
            role_signer=self.signers[receiver_role],
        )
        receiver = iam.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
            receiver_raw,
            binding=self.binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=receiver_at,
        )
        return publisher, forward, receiver, publisher_raw, forward_raw, receiver_raw

    def _full_campaign(self) -> tuple[
        iam.PhysicalArvanS3FourRoleLiveIamNonceLedger,
        iam.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
        tuple[object, ...],
    ]:
        ledger, permit = self._permit()
        normal = self._direction(publisher_role="fi-publisher", permit=permit, offset=1)
        reverse = self._direction(publisher_role="ir-publisher", permit=permit, offset=10)
        return ledger, permit, (*normal, *reverse)

    @staticmethod
    def _resign(
        raw: bytes,
        *,
        signer: Ed25519PrivateKey,
        signer_field: str,
        signature_field: str,
        kind: str,
        mutate: object,
    ) -> bytes:
        payload = json.loads(raw.decode("ascii"))
        del payload[signature_field]
        mutate(payload)
        return iam._seal(
            unsigned=payload,
            signer=signer,
            signer_field=signer_field,
            signature_field=signature_field,
            kind=kind,
        )

    def test_full_two_direction_evidence_commits_and_verifies_with_durable_state(self) -> None:
        ledger, permit, values = self._full_campaign()
        normal_publisher, normal_forward, normal_receiver = values[:3]
        reverse_publisher, reverse_forward, reverse_receiver = values[6:9]
        committed_ledger, aggregate_raw = iam.seal_physical_arvan_s3_four_role_live_iam_witness_aggregate(
            binding=self.binding,
            ledger=ledger,
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
        aggregate_payload = json.loads(aggregate_raw.decode("ascii"))
        self.assertEqual(
            aggregate_payload["prior_ledger_sha256"],
            committed_ledger.records[0].commit_prior_ledger_sha256,
        )
        self.assertNotEqual(
            committed_ledger.records[0].prior_ledger_sha256,
            committed_ledger.records[0].commit_prior_ledger_sha256,
        )
        durable = iam.serialize_physical_arvan_s3_four_role_live_iam_nonce_ledger(
            committed_ledger, binding=self.binding
        )
        restored = iam.parse_physical_arvan_s3_four_role_live_iam_nonce_ledger(
            durable, binding=self.binding
        )
        verified = iam.verify_physical_arvan_s3_four_role_live_iam_witness_aggregate(
            aggregate_raw,
            binding=self.binding,
            ledger=restored,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW + timedelta(seconds=21),
        )
        self.assertEqual(NONCE, verified.nonce)
        self.assertEqual(self.binding.evidence_binding_sha256, verified.evidence_binding_sha256)
        self.assertIs(
            verified,
            iam.require_verified_physical_arvan_s3_four_role_live_iam_witness_aggregate(
                verified,
                binding=self.binding,
                observed_at=NOW + timedelta(seconds=21),
            ),
        )
        self.assertEqual(1, len(restored.records))
        self.assertEqual("committed", restored.records[0].status)

    def test_expired_open_nonce_can_only_be_retired_then_replaced(self) -> None:
        ledger, _permit = self._permit()
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "NONCE_ALREADY_OPEN"):
            iam.issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
                binding=self.binding,
                ledger=ledger,
                nonce="9" * 64,
                issued_at=NOW + timedelta(seconds=1),
                expires_at=NOW + timedelta(minutes=5),
                witness_signer=self.witness,
            )
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "NONCE_RETIRE_EARLY"):
            iam.expire_physical_arvan_s3_four_role_live_iam_nonce(
                binding=self.binding,
                ledger=ledger,
                nonce=NONCE,
                retired_at=NOW + timedelta(minutes=4, seconds=59),
            )
        retired = iam.expire_physical_arvan_s3_four_role_live_iam_nonce(
            binding=self.binding,
            ledger=ledger,
            nonce=NONCE,
            retired_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual("expired", retired.records[0].status)
        fresh, raw = iam.issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
            binding=self.binding,
            ledger=retired,
            nonce="9" * 64,
            issued_at=NOW + timedelta(minutes=5),
            expires_at=NOW + timedelta(minutes=5, seconds=30),
            witness_signer=self.witness,
        )
        self.assertEqual(2, len(fresh.records))
        self.assertEqual(
            "9" * 64,
            iam.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
                raw,
                binding=self.binding,
                witness_public_key=_public_key(self.witness),
                observed_at=NOW + timedelta(minutes=5),
            ).nonce,
        )

    def test_stale_and_forged_permits_fail_before_role_observation(self) -> None:
        _ledger, permit = self._permit()
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "PERMIT_STALE"):
            iam.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
                iam._seal(
                    unsigned=iam._permit_unsigned(
                        binding=self.binding,
                        nonce=permit.nonce,
                        issued_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        expires_at=(NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                    signer=self.witness,
                    signer_field="witness_signer",
                    signature_field="witness_signature",
                    kind=iam._PERMIT_KIND,
                ),
                binding=self.binding,
                witness_public_key=_public_key(self.witness),
                observed_at=NOW + timedelta(minutes=5),
            )
        rogue = Ed25519PrivateKey.generate()
        forged = iam._seal(
            unsigned=iam._permit_unsigned(
                binding=self.binding,
                nonce=permit.nonce,
                issued_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires_at=(NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            signer=rogue,
            signer_field="witness_signer",
            signature_field="witness_signature",
            kind=iam._PERMIT_KIND,
        )
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "PERMIT_SIGNATURE_INVALID"):
            iam.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
                forged,
                binding=self.binding,
                witness_public_key=_public_key(self.witness),
                observed_at=NOW,
            )

    def test_route_mismatch_and_unpinned_role_are_rejected(self) -> None:
        _ledger, permit = self._permit()
        raw = iam.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.binding,
            nonce_permit=permit,
            publisher_role="fi-publisher",
            observed_at=NOW + timedelta(seconds=1),
            probe_locator=iam.make_physical_arvan_s3_live_iam_probe_locator(
                binding=self.binding,
                nonce=permit.nonce,
                publisher_role="fi-publisher",
                object_version_id="version-one",
                content_sha256="a" * 64,
                content_bytes=5,
            ),
            allowed_operation_outcomes=_outcomes("fi-publisher")[0],
            denied_operation_outcomes=_outcomes("fi-publisher")[1],
            role_signer=self.signers["fi-publisher"],
        )
        altered = self._resign(
            raw,
            signer=self.signers["fi-publisher"],
            signer_field="role_signer",
            signature_field="role_signature",
            kind=iam._PUBLISHER_KIND,
            mutate=lambda item: item.__setitem__("route_scope_sha256", "9" * 64),
        )
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "ROUTE_MISMATCH"):
            iam.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
                altered, binding=self.binding, nonce_permit=permit, observed_at=NOW + timedelta(seconds=1)
            )
        swapped_key = self._resign(
            raw,
            signer=self.signers["ir-publisher"],
            signer_field="role_signer",
            signature_field="role_signature",
            kind=iam._PUBLISHER_KIND,
            mutate=lambda _item: None,
        )
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "ROLE_SIGNER_NOT_PINNED"):
            iam.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
                swapped_key, binding=self.binding, nonce_permit=permit, observed_at=NOW + timedelta(seconds=1)
            )

    def test_cross_site_direct_fields_and_arbitrary_provider_hash_are_forbidden(self) -> None:
        _ledger, permit = self._permit()
        raw = iam.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.binding,
            nonce_permit=permit,
            publisher_role="fi-publisher",
            observed_at=NOW + timedelta(seconds=1),
            probe_locator=iam.make_physical_arvan_s3_live_iam_probe_locator(
                binding=self.binding,
                nonce=permit.nonce,
                publisher_role="fi-publisher",
                object_version_id="version-two",
                content_sha256="a" * 64,
                content_bytes=5,
            ),
            allowed_operation_outcomes=_outcomes("fi-publisher")[0],
            denied_operation_outcomes=_outcomes("fi-publisher")[1],
            role_signer=self.signers["fi-publisher"],
        )
        for field in ("direct_site_url", "provider_permission_sha256"):
            with self.subTest(field=field):
                altered = self._resign(
                    raw,
                    signer=self.signers["fi-publisher"],
                    signer_field="role_signer",
                    signature_field="role_signature",
                    kind=iam._PUBLISHER_KIND,
                    mutate=lambda item, field=field: item.__setitem__(field, "https://forbidden.invalid"),
                )
                with self.assertRaises(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError):
                    iam.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
                        altered, binding=self.binding, nonce_permit=permit, observed_at=NOW + timedelta(seconds=1)
                    )

    def test_missing_denied_outcome_and_role_swap_fail_closed(self) -> None:
        _ledger, permit = self._permit()
        allowed, denied = _outcomes("fi-publisher")
        locator = iam.make_physical_arvan_s3_live_iam_probe_locator(
            binding=self.binding,
            nonce=permit.nonce,
            publisher_role="fi-publisher",
            object_version_id="version-three",
            content_sha256="a" * 64,
            content_bytes=5,
        )
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "OUTCOME_MATRIX_INVALID"):
            iam.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
                binding=self.binding,
                nonce_permit=permit,
                publisher_role="fi-publisher",
                observed_at=NOW + timedelta(seconds=1),
                probe_locator=locator,
                allowed_operation_outcomes=allowed,
                denied_operation_outcomes=denied[:-1],
                role_signer=self.signers["fi-publisher"],
            )
        raw = iam.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.binding,
            nonce_permit=permit,
            publisher_role="fi-publisher",
            observed_at=NOW + timedelta(seconds=1),
            probe_locator=locator,
            allowed_operation_outcomes=allowed,
            denied_operation_outcomes=denied,
            role_signer=self.signers["fi-publisher"],
        )
        role_swapped = self._resign(
            raw,
            signer=self.signers["fi-publisher"],
            signer_field="role_signer",
            signature_field="role_signature",
            kind=iam._PUBLISHER_KIND,
            mutate=lambda item: item.__setitem__("role", "ir-publisher"),
        )
        with self.assertRaises(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError):
            iam.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
                role_swapped, binding=self.binding, nonce_permit=permit, observed_at=NOW + timedelta(seconds=1)
            )

    def test_receiver_rejects_mismatched_version_and_hash_selectors(self) -> None:
        _ledger, permit = self._permit()
        _publisher, forward, _receiver, _publisher_raw, _forward_raw, receiver_raw = self._direction(
            publisher_role="fi-publisher", permit=permit, offset=1
        )
        for field, value in (("object_version_id", "other-version"), ("content_sha256", "c" * 64)):
            with self.subTest(field=field):
                altered = self._resign(
                    receiver_raw,
                    signer=self.signers["ir-receiver"],
                    signer_field="role_signer",
                    signature_field="role_signature",
                    kind=iam._RECEIVER_KIND,
                    mutate=lambda item, field=field, value=value: item["probe_locator"].__setitem__(field, value),
                )
                with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "PROBE_SELECTOR_MISMATCH"):
                    iam.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
                        altered,
                        binding=self.binding,
                        nonce_permit=permit,
                        witness_forward=forward,
                        observed_at=NOW + timedelta(seconds=3),
                    )

    def test_duplicate_and_replayed_nonce_cannot_be_committed_again(self) -> None:
        ledger, permit, values = self._full_campaign()
        normal_publisher, normal_forward, normal_receiver = values[:3]
        reverse_publisher, reverse_forward, reverse_receiver = values[6:9]
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "NONCE_REPLAY"):
            iam.issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
                binding=self.binding,
                ledger=ledger,
                nonce=NONCE,
                issued_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
                witness_signer=self.witness,
            )
        committed, aggregate = iam.seal_physical_arvan_s3_four_role_live_iam_witness_aggregate(
            binding=self.binding,
            ledger=ledger,
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
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "NONCE_NOT_OPEN"):
            iam.seal_physical_arvan_s3_four_role_live_iam_witness_aggregate(
                binding=self.binding,
                ledger=committed,
                nonce_permit=permit,
                normal_publisher_observation=normal_publisher,
                normal_witness_forward=normal_forward,
                normal_receiver_observation=normal_receiver,
                reverse_publisher_observation=reverse_publisher,
                reverse_witness_forward=reverse_forward,
                reverse_receiver_observation=reverse_receiver,
                committed_at=NOW + timedelta(seconds=21),
                witness_signer=self.witness,
            )
        with self.assertRaisesRegex(iam.PhysicalArvanS3FourRoleLiveIamEvidenceError, "NONCE_COMMIT_MISSING"):
            iam.verify_physical_arvan_s3_four_role_live_iam_witness_aggregate(
                aggregate,
                binding=self.binding,
                ledger=ledger,
                witness_public_key=_public_key(self.witness),
                observed_at=NOW + timedelta(seconds=21),
            )

    def test_source_has_no_sdk_network_or_credential_loader_dependency(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "socket",
                "subprocess",
                "requests",
                "os",
                "pathlib",
                "urllib",
            }
        )
        self.assertNotIn("credential_loader", MODULE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
