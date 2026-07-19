import base64
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import (
    CONTROL_ACTIVE,
    WriterStateSnapshot,
    snapshot_is_local_active,
)
from core.writer_witness_client import (
    WriterWitnessClientError,
    renew_local_writer_lease_once,
    writer_witness_client_configuration_reasons,
    writer_witness_renewal_loop,
)
from core.writer_witness_contract import sign_witness_lease_proof


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
IDENTITY = RuntimeIdentity(
    logical_authority="webapp",
    physical_site="webapp_fi",
    legacy_server_mode="iran",
    compatibility_inferred=False,
)


def keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


def local_snapshot(*, expires_at: datetime) -> WriterStateSnapshot:
    return WriterStateSnapshot(
        active_site="webapp_fi",
        writer_epoch=4,
        control_state=CONTROL_ACTIVE,
        transition_id="local-transition-4",
        readiness_evidence_hash=None,
        readiness_evidence_id=None,
        readiness_approved_by=None,
        readiness_approved_at=None,
        readiness_expires_at=None,
        witness_lease_id="lease-4",
        witness_lease_issued_at=NOW,
        witness_lease_expires_at=expires_at,
        witness_proof_hash="a" * 64,
        witness_transition_id="witness-old",
        witness_local_boot_id="12345678-1234-4234-8234-123456789abc",
        witness_local_boottime_deadline=125.0,
        witness_observed_wall_at=NOW,
        witness_observed_boottime=100.0,
        witness_clock_offset_ms=0,
    )


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeSessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        session = FakeSession()
        self.sessions.append(session)
        return session


class WriterWitnessRenewalTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_configuration_requires_https_pairwise_secret_and_safe_window(self):
        with (
            patch(
                "core.writer_witness_client.settings.writer_witness_internal_url",
                "https://witness.internal",
            ),
            patch(
                "core.writer_witness_client.settings.writer_witness_client_key_id",
                "webapp-fi-v1",
            ),
            patch(
                "core.writer_witness_client.settings.writer_witness_client_secret",
                "fi-secret-0123456789abcdef-0123456789abcdef",
            ),
            patch("core.writer_witness_client.settings.writer_witness_verify_tls", True),
            patch(
                "core.writer_witness_client.settings.writer_witness_http_timeout_seconds",
                3.0,
            ),
            patch(
                "core.writer_witness_client.settings.writer_witness_auth_max_age_seconds",
                15,
            ),
        ):
            self.assertEqual(writer_witness_client_configuration_reasons(IDENTITY), ())
            with patch(
                "core.writer_witness_client.settings.writer_witness_internal_url",
                "http://witness.internal",
            ):
                reasons = writer_witness_client_configuration_reasons(IDENTITY)

        self.assertIn("writer_witness_internal_url_invalid", reasons)

    async def test_ambiguous_transport_retry_reuses_exact_request_id(self):
        renewed = SimpleNamespace(
            writer_epoch=4,
            lease_id="lease-4",
            expires_at=NOW + timedelta(seconds=180),
        )
        renew_once = AsyncMock(
            side_effect=[
                WriterWitnessClientError(
                    "timeout after possible commit",
                    code="writer_witness_unreachable",
                    retryable=True,
                ),
                renewed,
            ]
        )
        sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with (
            patch("core.writer_witness_client.settings.writer_witness_required", True),
            patch(
                "core.writer_witness_client.settings.writer_witness_auto_renew_enabled",
                True,
            ),
            patch("core.writer_witness_client.resolve_runtime_identity", return_value=IDENTITY),
            patch("core.writer_witness_client.writer_witness_client_from_settings"),
            patch("core.writer_witness_client.renew_local_writer_lease_once", new=renew_once),
            patch("core.writer_witness_client.asyncio.sleep", new=sleeps),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await writer_witness_renewal_loop()

        self.assertEqual(renew_once.await_count, 2)
        first_id = renew_once.await_args_list[0].kwargs["request_id"]
        second_id = renew_once.await_args_list[1].kwargs["request_id"]
        self.assertEqual(first_id, second_id)

    async def test_signed_renewal_is_atomically_imported_without_changing_term(self):
        private_key, public_key = keypair()
        snapshot = local_snapshot(expires_at=NOW + timedelta(seconds=90))
        proof = sign_witness_lease_proof(
            holder_site="webapp_fi",
            writer_epoch=4,
            lease_id="lease-4",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=180),
            witness_transition_id="witness-renewed",
            private_key_base64=private_key,
        )
        remote = AsyncMock()
        remote.transition.return_value = {
            "contract_version": 1,
            "accepted": True,
            "request_id": "renew-request",
            "proof": proof,
        }
        sessions = FakeSessionFactory()
        local_transition = AsyncMock()
        injected_http_client = SimpleNamespace()

        with (
            patch(
                "core.writer_witness_client.load_writer_snapshot",
                new=AsyncMock(return_value=snapshot),
            ),
            patch(
                "core.writer_witness_client.transition_writer_state",
                new=local_transition,
            ),
        ):
            validated = await renew_local_writer_lease_once(
                client=remote,
                request_id="renew-request",
                identity=IDENTITY,
                now=NOW,
                session_factory=sessions,
                http_client=injected_http_client,
                public_key_base64=public_key,
                lease_duration_seconds=180,
                safety_margin_seconds=15,
                max_clock_skew_seconds=5,
            )

        self.assertEqual(validated.writer_epoch, 4)
        self.assertEqual(validated.lease_id, "lease-4")
        self.assertIs(remote.transition.await_args.kwargs["client"], injected_http_client)
        local_transition.assert_awaited_once()
        kwargs = local_transition.await_args.kwargs
        self.assertEqual(kwargs["expected_epoch"], 4)
        self.assertEqual(kwargs["expected_active_site"], "webapp_fi")
        self.assertEqual(kwargs["witness_proof"].witness_transition_id, "witness-renewed")
        sessions.sessions[-1].commit.assert_awaited_once()

    async def test_partition_never_refreshes_local_proof_and_writer_fails_closed(self):
        snapshot = local_snapshot(expires_at=NOW + timedelta(seconds=40))
        remote = AsyncMock()
        remote.transition.side_effect = WriterWitnessClientError(
            "partition",
            code="writer_witness_unreachable",
            retryable=True,
        )
        sessions = FakeSessionFactory()
        local_transition = AsyncMock()
        with (
            patch("core.writer_witness_client.DrControlSessionLocal", new=sessions),
            patch(
                "core.writer_witness_client.load_writer_snapshot",
                new=AsyncMock(return_value=snapshot),
            ),
            patch(
                "core.writer_witness_client.transition_writer_state",
                new=local_transition,
            ),
        ):
            with self.assertRaises(WriterWitnessClientError) as failure:
                await renew_local_writer_lease_once(
                    client=remote,
                    request_id="ambiguous-renewal",
                    identity=IDENTITY,
                    now=NOW,
                )

        self.assertTrue(failure.exception.retryable)
        local_transition.assert_not_awaited()
        eligible, reasons = snapshot_is_local_active(
            IDENTITY,
            snapshot,
            now=NOW + timedelta(seconds=26),
            require_witness_lease=True,
            current_boot_id="12345678-1234-4234-8234-123456789abc",
            current_boottime=126.0,
        )
        self.assertFalse(eligible)
        self.assertIn("writer_witness_monotonic_deadline_expired", reasons)


if __name__ == "__main__":
    unittest.main()
