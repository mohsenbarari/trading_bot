"""Focused no-I/O tests for root-pinned writer-quiescence receipts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pickle
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_release_candidate_writer_quiescence_receipt as quiescence


NOW = datetime(2026, 7, 31, 14, 30, tzinfo=timezone.utc)
SOURCE_ROOT = Path("/srv/trading-bot-three-site/review-source")
OTHER_ROOT = Path("/srv/trading-bot-three-site/other-source")
INVENTORY_SHA = "a" * 64
GENERATION_SHA = "b" * 64
EVIDENCE_SHA = "c" * 64


def policy(root: Path = SOURCE_ROOT):
    return quiescence.PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy(
        source_root=root,
        required_mode=0o750,
    )


class PhysicalReleaseCandidateWriterQuiescenceReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        public_key = self.signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.config = quiescence.RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig(
            source_root_policy=policy(),
            authority=quiescence.PhysicalReleaseCandidateWriterQuiescenceAuthorityPin(
                public_key=public_key,
                key_id="ed25519-sha256:" + __import__("hashlib").sha256(public_key).hexdigest(),
            ),
            enabled=True,
            maximum_receipt_age_seconds=120,
        )

    def receipt(
        self,
        *,
        root_policy=None,
        generation: str = GENERATION_SHA,
        issued_at: datetime = NOW - timedelta(seconds=10),
        expires_at: datetime = NOW + timedelta(seconds=100),
    ) -> bytes:
        return quiescence.build_signed_physical_release_candidate_writer_quiescence_receipt(
            source_root_policy=policy() if root_policy is None else root_policy,
            inventory_manifest_sha256=INVENTORY_SHA,
            frozen_generation_sha256=generation,
            quiescence_evidence_sha256=EVIDENCE_SHA,
            writer_lease_id="release-candidate-quiesced-lease-20260731",
            issued_at=issued_at,
            expires_at=expires_at,
            authority_signer=self.signer,
        )

    def verify(self, raw: bytes, **changes: object):
        values: dict[str, object] = {
            "config": self.config,
            "source_root": SOURCE_ROOT,
            "inventory_manifest_sha256": INVENTORY_SHA,
            "frozen_generation_sha256": GENERATION_SHA,
            "quiescence_evidence_sha256": EVIDENCE_SHA,
            "now": NOW,
        }
        values.update(changes)
        return quiescence.verify_physical_release_candidate_writer_quiescence_receipt(raw, **values)

    def test_signed_pinned_receipt_verifies_and_is_nonserializable(self) -> None:
        verified = self.verify(self.receipt())
        required = quiescence.require_verified_physical_release_candidate_writer_quiescence_receipt(
            verified,
            config=self.config,
            source_root=SOURCE_ROOT,
            inventory_manifest_sha256=INVENTORY_SHA,
            frozen_generation_sha256=GENERATION_SHA,
            quiescence_evidence_sha256=EVIDENCE_SHA,
            now=NOW + timedelta(seconds=1),
        )
        self.assertIs(verified, required)
        self.assertEqual(GENERATION_SHA, verified.frozen_generation_sha256)
        self.assertNotIn(str(SOURCE_ROOT).encode("ascii"), verified.canonical_receipt)
        with self.assertRaises(TypeError):
            pickle.dumps(verified)

    def test_stale_wrong_root_and_wrong_generation_fail_closed(self) -> None:
        stale = self.receipt(
            issued_at=NOW - timedelta(seconds=121),
            expires_at=NOW - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(
            quiescence.PhysicalReleaseCandidateWriterQuiescenceReceiptError,
            "STALE_OR_FUTURE|EXPIRED",
        ):
            self.verify(stale)
        with self.assertRaisesRegex(
            quiescence.PhysicalReleaseCandidateWriterQuiescenceReceiptError,
            "BINDING_MISMATCH",
        ):
            self.verify(self.receipt(), source_root=OTHER_ROOT)
        with self.assertRaisesRegex(
            quiescence.PhysicalReleaseCandidateWriterQuiescenceReceiptError,
            "BINDING_MISMATCH",
        ):
            self.verify(self.receipt(), frozen_generation_sha256="d" * 64)

    def test_forged_signature_and_disabled_config_fail_closed(self) -> None:
        forged = bytearray(self.receipt())
        forged[-2] = ord("A") if forged[-2] != ord("A") else ord("B")
        with self.assertRaisesRegex(
            quiescence.PhysicalReleaseCandidateWriterQuiescenceReceiptError,
            "INVALID|SIGNATURE_INVALID",
        ):
            self.verify(bytes(forged))
        with self.assertRaisesRegex(
            quiescence.PhysicalReleaseCandidateWriterQuiescenceReceiptError,
            "VERIFIER_DISABLED",
        ):
            self.verify(
                self.receipt(),
                config=quiescence.RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig(
                    source_root_policy=policy(),
                    authority=self.config.authority,
                ),
            )


if __name__ == "__main__":
    unittest.main()
