import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.writer_witness_contract import (
    WitnessProofError,
    sign_witness_lease_proof,
    validate_witness_lease_proof,
    witness_public_key_is_valid,
    witness_timing_configuration_is_safe,
)
from core.writer_witness_control import (
    ACTION_ACQUIRE,
    ACTION_DRAIN,
    ACTION_RENEW,
    WriterWitnessError,
    transition_witness_state,
)
from models.webapp_writer_state import WebappWriterWitnessReceipt


NOW = datetime(2026, 7, 14, 23, 0, tzinfo=timezone.utc)


def keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def signed_proof(private_key: str, *, site: str = "webapp_fi", epoch: int = 3):
    return sign_witness_lease_proof(
        holder_site=site,
        writer_epoch=epoch,
        lease_id="lease-3",
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=180),
        witness_transition_id="witness-transition-3",
        private_key_base64=private_key,
    )


class WitnessProofContractTests(unittest.TestCase):
    def test_timing_configuration_requires_room_for_renewal_margin_and_skew(self):
        self.assertTrue(
            witness_timing_configuration_is_safe(
                lease_duration_seconds=180,
                renew_interval_seconds=30,
                safety_margin_seconds=15,
                max_clock_skew_seconds=5,
            )
        )
        self.assertFalse(
            witness_timing_configuration_is_safe(
                lease_duration_seconds=45,
                renew_interval_seconds=30,
                safety_margin_seconds=10,
                max_clock_skew_seconds=10,
            )
        )

    def test_valid_signed_proof_is_verified_and_hashed(self):
        private_key, public_key = keypair()
        self.assertTrue(witness_public_key_is_valid(public_key))
        self.assertFalse(witness_public_key_is_valid("not-base64"))
        proof = validate_witness_lease_proof(
            signed_proof(private_key),
            public_key_base64=public_key,
            expected_site="webapp_fi",
            expected_epoch=3,
            now=NOW,
        )

        self.assertEqual(proof.writer_epoch, 3)
        self.assertEqual(proof.lease_id, "lease-3")
        self.assertEqual(len(proof.proof_hash), 64)

    def test_tampered_wrong_site_and_near_expiry_proofs_fail_closed(self):
        private_key, public_key = keypair()
        tampered = signed_proof(private_key)
        tampered["writer_epoch"] = 4
        wrong_site = signed_proof(private_key)
        near_expiry = sign_witness_lease_proof(
            holder_site="webapp_fi",
            writer_epoch=3,
            lease_id="lease-short",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=10),
            witness_transition_id="witness-short",
            private_key_base64=private_key,
        )

        cases = (
            (tampered, "webapp_fi", 4),
            (wrong_site, "webapp_ir", 3),
            (near_expiry, "webapp_fi", 3),
        )
        for payload, site, epoch in cases:
            with self.subTest(site=site, epoch=epoch):
                with self.assertRaises(WitnessProofError):
                    validate_witness_lease_proof(
                        payload,
                        public_key_base64=public_key,
                        expected_site=site,
                        expected_epoch=epoch,
                        now=NOW,
                    )


def vacant_state():
    return SimpleNamespace(
        authority="webapp",
        holder_site=None,
        writer_epoch=0,
        lease_id=None,
        lease_status="vacant",
        issued_at=None,
        expires_at=None,
        transition_id="bootstrap",
        updated_by="migration",
        reason="bootstrap",
    )


class FakeWitnessSession:
    def __init__(self):
        self.receipts = {}
        self.flush = AsyncMock()

    async def get(self, model, key):
        if model is WebappWriterWitnessReceipt:
            return self.receipts.get(key)
        return None

    def add(self, value):
        if isinstance(value, WebappWriterWitnessReceipt):
            self.receipts[value.request_id] = value


class WitnessStateMachineTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_renew_and_drain_preserve_single_epoch(self):
        private_key, _ = keypair()
        state = vacant_state()
        session = FakeWitnessSession()
        loader = AsyncMock(return_value=state)
        with patch("core.writer_witness_control.load_witness_state", new=loader):
            acquired = await transition_witness_state(
                session,
                action=ACTION_ACQUIRE,
                requester_site="webapp_fi",
                expected_epoch=0,
                expected_lease_id=None,
                request_id="request-acquire",
                operator="operator@example",
                reason="initial writer term",
                private_key_base64=private_key,
                now=NOW,
            )
            renewed = await transition_witness_state(
                session,
                action=ACTION_RENEW,
                requester_site="webapp_fi",
                expected_epoch=1,
                expected_lease_id=acquired.state.lease_id,
                request_id="request-renew",
                operator="operator@example",
                reason="scheduled renewal",
                private_key_base64=private_key,
                now=NOW + timedelta(seconds=30),
            )
            drained = await transition_witness_state(
                session,
                action=ACTION_DRAIN,
                requester_site="webapp_fi",
                expected_epoch=1,
                expected_lease_id=acquired.state.lease_id,
                request_id="request-drain",
                operator="operator@example",
                reason="planned handoff",
                private_key_base64=None,
                now=NOW + timedelta(seconds=40),
            )

            with self.assertRaises(WriterWitnessError):
                await transition_witness_state(
                    session,
                    action=ACTION_RENEW,
                    requester_site="webapp_fi",
                    expected_epoch=1,
                    expected_lease_id=acquired.state.lease_id,
                    request_id="request-renew-after-drain",
                    operator="operator@example",
                    reason="unsafe renewal",
                    private_key_base64=private_key,
                    now=NOW + timedelta(seconds=45),
                )

        self.assertEqual(acquired.state.writer_epoch, 1)
        self.assertEqual(renewed.state.writer_epoch, 1)
        self.assertEqual(renewed.state.lease_id, acquired.state.lease_id)
        self.assertEqual(drained.state.lease_status, "draining")
        self.assertIsNone(drained.proof)

    async def test_expiry_allows_new_site_to_acquire_next_epoch(self):
        private_key, _ = keypair()
        state = vacant_state()
        session = FakeWitnessSession()
        with patch(
            "core.writer_witness_control.load_witness_state",
            new=AsyncMock(return_value=state),
        ):
            first = await transition_witness_state(
                session,
                action=ACTION_ACQUIRE,
                requester_site="webapp_fi",
                expected_epoch=0,
                expected_lease_id=None,
                request_id="request-first",
                operator="operator@example",
                reason="first term",
                private_key_base64=private_key,
                now=NOW,
            )
            second = await transition_witness_state(
                session,
                action=ACTION_ACQUIRE,
                requester_site="webapp_ir",
                expected_epoch=1,
                expected_lease_id=first.state.lease_id,
                request_id="request-second",
                operator="operator@example",
                reason="Iran outage promotion",
                private_key_base64=private_key,
                now=NOW + timedelta(seconds=181),
            )

        self.assertEqual(second.state.holder_site, "webapp_ir")
        self.assertEqual(second.state.writer_epoch, 2)
        self.assertNotEqual(second.state.lease_id, first.state.lease_id)

    async def test_exact_request_replays_but_changed_payload_is_rejected(self):
        private_key, _ = keypair()
        state = vacant_state()
        session = FakeWitnessSession()
        kwargs = dict(
            action=ACTION_ACQUIRE,
            requester_site="webapp_fi",
            expected_epoch=0,
            expected_lease_id=None,
            request_id="stable-request",
            operator="operator@example",
            reason="first term",
            private_key_base64=private_key,
            now=NOW,
        )
        with patch(
            "core.writer_witness_control.load_witness_state",
            new=AsyncMock(return_value=state),
        ):
            first = await transition_witness_state(session, **kwargs)
            replay = await transition_witness_state(session, **kwargs)
            with self.assertRaises(WriterWitnessError):
                await transition_witness_state(session, **{**kwargs, "reason": "changed"})

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.proof, first.proof)
        self.assertEqual(replay.state.transition_id, first.state.transition_id)


if __name__ == "__main__":
    unittest.main()
