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
    CONTROL_FENCED,
    REQUIRED_READY_EVIDENCE_FLAGS,
    WriterStateSnapshot,
    snapshot_is_local_active,
)
from core.writer_witness_client import (
    acquire_and_activate_local_writer_once,
    drain_local_writer_lease_once,
    WriterWitnessClientError,
    initialize_local_writer_lease_once,
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
IR_IDENTITY = RuntimeIdentity(
    logical_authority="webapp",
    physical_site="webapp_ir",
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


def target_snapshot(*, active: bool, proof=None) -> WriterStateSnapshot:  # noqa: ANN001
    base = local_snapshot(expires_at=NOW)
    return base.__class__(
        **{
            **base.__dict__,
            "active_site": "webapp_ir" if active else None,
            "writer_epoch": 2 if active else 1,
            "control_state": CONTROL_ACTIVE if active else CONTROL_FENCED,
            "witness_lease_id": proof.lease_id if active else None,
            "witness_lease_issued_at": proof.issued_at if active else None,
            "witness_lease_expires_at": proof.expires_at if active else None,
            "witness_proof_hash": proof.proof_hash if active else None,
            "witness_transition_id": proof.witness_transition_id if active else None,
        }
    )


def target_readiness() -> dict:
    return {
        "evidence_id": "promotion-ready-ir-2",
        "target_site": "webapp_ir",
        "writer_epoch": 2,
        "generated_at": (NOW - timedelta(seconds=5)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        **{name: True for name in REQUIRED_READY_EVIDENCE_FLAGS},
    }


class FakeSession:
    def __init__(self, store):
        self.store = store
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def get(self, model, key, **_kwargs):
        return self.store.get((model, key))

    def add(self, value):
        self.store[(type(value), value.operation_id)] = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeSessionFactory:
    def __init__(self):
        self.sessions = []
        self.store = {}

    def __call__(self):
        session = FakeSession(self.store)
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

    async def test_initial_fi_epoch_one_lease_is_acquired_and_atomically_imported(self):
        private_key, public_key = keypair()
        snapshot = local_snapshot(expires_at=NOW)
        snapshot = snapshot.__class__(
            **{
                **snapshot.__dict__,
                "writer_epoch": 1,
                "transition_id": "bootstrap-fi-1",
                "witness_lease_id": None,
                "witness_lease_issued_at": None,
                "witness_lease_expires_at": None,
                "witness_proof_hash": None,
                "witness_transition_id": None,
                "witness_local_boot_id": None,
                "witness_local_boottime_deadline": None,
                "witness_observed_wall_at": None,
                "witness_observed_boottime": None,
                "witness_clock_offset_ms": None,
            }
        )
        proof = sign_witness_lease_proof(
            holder_site="webapp_fi",
            writer_epoch=1,
            lease_id="initial-lease",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=180),
            witness_transition_id="witness-initial",
            private_key_base64=private_key,
        )
        remote = AsyncMock()
        remote.transition.return_value = {"proof": proof}
        sessions = FakeSessionFactory()
        local_transition = AsyncMock()
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
            validated = await initialize_local_writer_lease_once(
                client=remote,
                request_id="11111111-1111-4111-8111-111111111111",
                campaign_id="22222222-2222-4222-8222-222222222222",
                identity=IDENTITY,
                now=NOW,
                session_factory=sessions,
                public_key_base64=public_key,
                lease_duration_seconds=180,
                safety_margin_seconds=15,
                max_clock_skew_seconds=5,
            )
        self.assertEqual(validated.writer_epoch, 1)
        self.assertEqual(remote.transition.await_args.kwargs["action"], "acquire")
        self.assertEqual(remote.transition.await_args.kwargs["expected_epoch"], 0)
        kwargs = local_transition.await_args.kwargs
        self.assertEqual(kwargs["action"], "lease_refresh")
        self.assertEqual(kwargs["expected_epoch"], 1)
        sessions.sessions[-1].commit.assert_awaited_once()

    async def test_fenced_target_acquires_exact_next_term_and_activates_atomically(self):
        private_key, public_key = keypair()
        snapshot = local_snapshot(expires_at=NOW)
        snapshot = snapshot.__class__(
            **{
                **snapshot.__dict__,
                "active_site": None,
                "writer_epoch": 1,
                "control_state": CONTROL_FENCED,
                "witness_lease_id": None,
                "witness_lease_issued_at": None,
                "witness_lease_expires_at": None,
                "witness_proof_hash": None,
                "witness_transition_id": None,
            }
        )
        proof = sign_witness_lease_proof(
            holder_site="webapp_ir",
            writer_epoch=2,
            lease_id="target-lease",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=180),
            witness_transition_id="witness-target",
            private_key_base64=private_key,
        )
        remote = AsyncMock()
        remote.status.return_value = {
            "state": {
                "writer_epoch": 1,
                "lease_id": "source-lease",
                "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
            }
        }
        remote.transition.return_value = {"proof": proof}
        readiness = {
            "evidence_id": "promotion-ready-ir-2",
            "target_site": "webapp_ir",
            "writer_epoch": 2,
            "generated_at": (NOW - timedelta(seconds=5)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            **{name: True for name in REQUIRED_READY_EVIDENCE_FLAGS},
        }
        sessions = FakeSessionFactory()
        local_transition = AsyncMock()
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
            validated = await acquire_and_activate_local_writer_once(
                client=remote,
                status_request_id="11111111-1111-4111-8111-111111111111",
                acquire_request_id="22222222-2222-4222-8222-222222222222",
                operation_id="33333333-3333-4333-8333-333333333333",
                target_epoch=2,
                readiness_payload=readiness,
                identity=IR_IDENTITY,
                now=NOW,
                session_factory=sessions,
                public_key_base64=public_key,
                lease_duration_seconds=180,
                safety_margin_seconds=15,
                max_clock_skew_seconds=5,
            )
        self.assertEqual(validated.holder_site, "webapp_ir")
        self.assertEqual(remote.transition.await_args.kwargs["expected_epoch"], 1)
        self.assertEqual(
            remote.transition.await_args.kwargs["expected_lease_id"], "source-lease"
        )
        kwargs = local_transition.await_args.kwargs
        self.assertEqual(kwargs["action"], "activate")
        self.assertEqual(kwargs["expected_epoch"], 1)
        self.assertEqual(kwargs["witness_proof"].writer_epoch, 2)
        sessions.sessions[-1].commit.assert_awaited_once()

    async def test_target_acquire_response_loss_replays_same_persisted_request(self):
        private_key, public_key = keypair()
        proof = sign_witness_lease_proof(
            holder_site="webapp_ir",
            writer_epoch=2,
            lease_id="target-lease",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=180),
            witness_transition_id="witness-target",
            private_key_base64=private_key,
        )
        remote = AsyncMock()
        remote.status.return_value = {
            "state": {
                "writer_epoch": 1,
                "lease_id": "source-lease",
                "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
            }
        }
        remote.transition.side_effect = [
            WriterWitnessClientError(
                "response lost after possible Witness commit",
                code="writer_witness_unreachable",
                retryable=True,
            ),
            {"proof": proof},
        ]
        sessions = FakeSessionFactory()
        call = dict(
            client=remote,
            status_request_id="11111111-1111-4111-8111-111111111111",
            acquire_request_id="22222222-2222-4222-8222-222222222222",
            operation_id="33333333-3333-4333-8333-333333333333",
            target_epoch=2,
            readiness_payload=target_readiness(),
            identity=IR_IDENTITY,
            now=NOW,
            session_factory=sessions,
            public_key_base64=public_key,
            lease_duration_seconds=180,
            safety_margin_seconds=15,
            max_clock_skew_seconds=5,
        )
        with (
            patch(
                "core.writer_witness_client.load_writer_snapshot",
                new=AsyncMock(return_value=target_snapshot(active=False)),
            ),
            patch(
                "core.writer_witness_client.transition_writer_state",
                new=AsyncMock(),
            ),
        ):
            with self.assertRaises(WriterWitnessClientError):
                await acquire_and_activate_local_writer_once(**call)
            validated = await acquire_and_activate_local_writer_once(**call)

        self.assertEqual(validated.writer_epoch, 2)
        self.assertEqual(remote.status.await_count, 1)
        self.assertEqual(remote.transition.await_count, 2)
        self.assertEqual(
            remote.transition.await_args_list[0].kwargs["request_id"],
            remote.transition.await_args_list[1].kwargs["request_id"],
        )
        self.assertEqual(
            remote.transition.await_args_list[1].kwargs["expected_lease_id"],
            "source-lease",
        )

    async def test_retry_after_local_activation_commit_reconciles_without_new_term(self):
        private_key, public_key = keypair()
        proof_payload = sign_witness_lease_proof(
            holder_site="webapp_ir",
            writer_epoch=2,
            lease_id="target-lease",
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=180),
            witness_transition_id="witness-target",
            private_key_base64=private_key,
        )
        remote = AsyncMock()
        remote.status.return_value = {
            "state": {
                "writer_epoch": 1,
                "lease_id": "source-lease",
                "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
            }
        }
        remote.transition.return_value = {"proof": proof_payload}
        sessions = FakeSessionFactory()
        committed_proof = None

        async def transition_then_lose_response(*_args, **kwargs):
            nonlocal committed_proof
            committed_proof = kwargs["witness_proof"]
            raise KeyboardInterrupt("controller lost response after local commit")

        async def snapshots(_session):
            return target_snapshot(
                active=committed_proof is not None,
                proof=committed_proof,
            )

        call = dict(
            client=remote,
            status_request_id="11111111-1111-4111-8111-111111111111",
            acquire_request_id="22222222-2222-4222-8222-222222222222",
            operation_id="33333333-3333-4333-8333-333333333333",
            target_epoch=2,
            readiness_payload=target_readiness(),
            identity=IR_IDENTITY,
            now=NOW,
            session_factory=sessions,
            public_key_base64=public_key,
            lease_duration_seconds=180,
            safety_margin_seconds=15,
            max_clock_skew_seconds=5,
        )
        with (
            patch(
                "core.writer_witness_client.load_writer_snapshot",
                new=AsyncMock(side_effect=snapshots),
            ),
            patch(
                "core.writer_witness_client.transition_writer_state",
                new=AsyncMock(side_effect=transition_then_lose_response),
            ) as local_transition,
        ):
            with self.assertRaises(KeyboardInterrupt):
                await acquire_and_activate_local_writer_once(**call)
            validated = await acquire_and_activate_local_writer_once(**call)

        self.assertEqual(validated.writer_epoch, 2)
        self.assertEqual(remote.status.await_count, 1)
        self.assertEqual(remote.transition.await_count, 1)
        self.assertEqual(local_transition.await_count, 1)

    async def test_source_lease_drain_is_bound_to_local_term_and_request(self):
        snapshot = local_snapshot(expires_at=NOW + timedelta(seconds=90))
        remote = AsyncMock()
        remote.transition.return_value = {
            "state": {
                "holder_site": "webapp_fi",
                "writer_epoch": 4,
                "lease_id": "lease-4",
                "lease_status": "draining",
                "expires_at": (NOW + timedelta(seconds=90)).isoformat(),
            }
        }
        sessions = FakeSessionFactory()
        with patch(
            "core.writer_witness_client.load_writer_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            result = await drain_local_writer_lease_once(
                client=remote,
                request_id="11111111-1111-4111-8111-111111111111",
                operation_id="22222222-2222-4222-8222-222222222222",
                expected_epoch=4,
                identity=IDENTITY,
                now=NOW,
                session_factory=sessions,
            )
        self.assertEqual(result["state"]["lease_status"], "draining")
        kwargs = remote.transition.await_args.kwargs
        self.assertEqual(kwargs["action"], "drain")
        self.assertEqual(kwargs["expected_lease_id"], "lease-4")


if __name__ == "__main__":
    unittest.main()
