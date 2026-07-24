import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from core.writer_witness_auth import (
    WITNESS_HUMAN_APPROVAL_RELAY_PATH,
    WITNESS_TRANSITION_PATH,
    WitnessAuthenticationError,
    WitnessClientCredential,
    sign_witness_request,
    verify_witness_request,
)
from core.human_approval import approval_subject, verify_human_approval
from core.human_approval_issuer import (
    authenticate_and_issue_session,
    create_enrollment,
    totp_code,
)
from core.writer_witness_client import WriterWitnessClient, WriterWitnessClientConfig
from models.webapp_writer_state import WebappWriterWitnessReceipt
from writer_witness_app import (
    WitnessServiceConfigurationError,
    WriterWitnessServiceRuntime,
    WriterWitnessServiceSettings,
    _build_runtime_from_settings,
    _service_credentials,
    create_writer_witness_app,
)


NOW = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
FI_CREDENTIAL = WitnessClientCredential(
    key_id="fi-control-v1",
    site="webapp_fi",
    secret="fi-secret-0123456789abcdef-0123456789abcdef",
)
IR_CREDENTIAL = WitnessClientCredential(
    key_id="ir-control-v1",
    site="webapp_ir",
    secret="ir-secret-0123456789abcdef-0123456789abcdef",
)
RELAY_CREDENTIAL = WitnessClientCredential(
    key_id="approval-relay-v1",
    site="orchestrator",
    secret="approval-relay-secret-0123456789abcdef-0123456789abcdef",
)
FI_PREVIOUS_CREDENTIAL = WitnessClientCredential(
    key_id="fi-control-v0",
    site="webapp_fi",
    secret="fi-previous-0123456789abcdef-0123456789abcdef",
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


def command_body(
    *,
    action: str,
    epoch: int,
    lease_id: str | None,
    request_id: str,
    reason: str,
) -> bytes:
    return json.dumps(
        {
            "contract_version": 1,
            "action": action,
            "expected_epoch": epoch,
            "expected_lease_id": lease_id,
            "request_id": request_id,
            "reason": reason,
            "lease_duration_seconds": 180,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    async def __call__(self, _session):
        return self.value


class SequenceClock:
    def __init__(self, *values: datetime):
        self.values = list(values)

    async def __call__(self, _session):
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class SharedFakeSession:
    def __init__(self, receipts: dict[str, WebappWriterWitnessReceipt]):
        self.receipts = receipts
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, key):
        if model is WebappWriterWitnessReceipt:
            return self.receipts.get(key)
        return None

    def add(self, value):
        if isinstance(value, WebappWriterWitnessReceipt):
            self.receipts[value.request_id] = value


class SharedSessionFactory:
    def __init__(self):
        self.receipts: dict[str, WebappWriterWitnessReceipt] = {}

    def __call__(self):
        return SharedFakeSession(self.receipts)


class _RelayMappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class RelaySession:
    def __init__(self, receipts):
        self.receipts = receipts
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement, parameters=None):
        source = str(statement)
        parameters = parameters or {}
        if "SELECT request_sha256, receipt FROM human_approval_relay_receipts" in source:
            receipt = self.receipts.get(parameters["request_id"])
            return _RelayMappingResult(receipt)
        if "INSERT INTO human_approval_relay_receipts" in source:
            self.receipts[parameters["request_id"]] = {
                "request_sha256": parameters["request_sha256"],
                "receipt": json.loads(parameters["receipt"]),
            }
            return _RelayMappingResult(None)
        raise AssertionError(f"unexpected relay SQL: {source}")


class RelaySessionFactory:
    def __init__(self):
        self.receipts = {}

    def __call__(self):
        return RelaySession(self.receipts)


class WriterWitnessAuthenticationTests(unittest.TestCase):
    def test_service_credentials_allow_one_rotation_overlap_per_site(self):
        previous_secret = "previous-fi-secret-0123456789abcdef-0123456789abcdef"
        settings = WriterWitnessServiceSettings(
            writer_witness_service_webapp_fi_key_id=FI_CREDENTIAL.key_id,
            writer_witness_service_webapp_fi_secret=FI_CREDENTIAL.secret,
            writer_witness_service_webapp_fi_previous_key_id="fi-control-v0",
            writer_witness_service_webapp_fi_previous_secret=previous_secret,
            writer_witness_service_webapp_ir_key_id=IR_CREDENTIAL.key_id,
            writer_witness_service_webapp_ir_secret=IR_CREDENTIAL.secret,
        )
        credentials = _service_credentials(settings)
        self.assertEqual(credentials["fi-control-v0"].site, "webapp_fi")
        self.assertEqual(len(credentials), 3)

        unsafe_cases = (
            {"writer_witness_service_webapp_fi_previous_key_id": "fi-control-v0"},
            {
                "writer_witness_service_webapp_fi_previous_key_id": FI_CREDENTIAL.key_id,
                "writer_witness_service_webapp_fi_previous_secret": previous_secret,
            },
            {
                "writer_witness_service_webapp_fi_previous_key_id": "fi-control-v0",
                "writer_witness_service_webapp_fi_previous_secret": IR_CREDENTIAL.secret,
            },
        )
        common = {
            "writer_witness_service_webapp_fi_key_id": FI_CREDENTIAL.key_id,
            "writer_witness_service_webapp_fi_secret": FI_CREDENTIAL.secret,
            "writer_witness_service_webapp_ir_key_id": IR_CREDENTIAL.key_id,
            "writer_witness_service_webapp_ir_secret": IR_CREDENTIAL.secret,
        }
        for unsafe in unsafe_cases:
            with self.subTest(unsafe=unsafe), self.assertRaises(
                WitnessServiceConfigurationError
            ):
                _service_credentials(WriterWitnessServiceSettings(**common, **unsafe))

        campaign_settings = WriterWitnessServiceSettings(
            **{
                **common,
                "writer_witness_service_webapp_fi_key_id": (
                    "matrix-wwm_0123456789ab-fi"
                ),
                "writer_witness_service_webapp_fi_not_after": (
                    "2026-07-15T10:15:00Z"
                ),
            }
        )
        campaign_credentials = _service_credentials(campaign_settings)
        self.assertEqual(
            campaign_credentials["matrix-wwm_0123456789ab-fi"].not_after,
            datetime(2026, 7, 15, 10, 15, tzinfo=timezone.utc),
        )

        bounded_overlap = WriterWitnessServiceSettings(
            **{
                **common,
                "writer_witness_service_webapp_fi_key_id": (
                    "matrix-wwm_0123456789ab-fi"
                ),
                "writer_witness_service_webapp_fi_not_after": (
                    "2026-07-15T10:15:00Z"
                ),
                "writer_witness_service_webapp_fi_previous_key_id": (
                    FI_CREDENTIAL.key_id
                ),
                "writer_witness_service_webapp_fi_previous_secret": (
                    previous_secret
                ),
                "writer_witness_service_webapp_fi_previous_not_after": (
                    "2026-07-15T10:15:00+00:00"
                ),
            }
        )
        overlap_credentials = _service_credentials(bounded_overlap)
        self.assertEqual(
            overlap_credentials[FI_CREDENTIAL.key_id].not_after,
            datetime(2026, 7, 15, 10, 15, tzinfo=timezone.utc),
        )

        for invalid_expiry in (
            "2026-07-15T10:16:00Z",
            "2026-07-15T10:15:00",
        ):
            with self.subTest(invalid_expiry=invalid_expiry), self.assertRaises(
                WitnessServiceConfigurationError
            ):
                _service_credentials(
                    WriterWitnessServiceSettings(
                        **{
                            **bounded_overlap.model_dump(),
                            "writer_witness_service_webapp_fi_previous_not_after": (
                                invalid_expiry
                            ),
                        }
                    )
                )

        with self.assertRaisesRegex(
            WitnessServiceConfigurationError, "cannot be a Matrix key"
        ):
            _service_credentials(
                WriterWitnessServiceSettings(
                    **{
                        **bounded_overlap.model_dump(),
                        "writer_witness_service_webapp_fi_previous_key_id": (
                            "matrix-wwm_abcdefabcdef-fi"
                        ),
                    }
                )
            )

    def test_matrix_credential_lifetime_is_capped_by_database_time(self):
        credential = WitnessClientCredential(
            key_id="matrix-wwm_0123456789ab-fi",
            site="webapp_fi",
            secret=FI_CREDENTIAL.secret,
            not_after=NOW + timedelta(seconds=901),
        )
        body = b"{}"
        headers = sign_witness_request(
            credential=credential,
            method="POST",
            path=WITNESS_TRANSITION_PATH,
            body=body,
            request_id="far-future-campaign",
            timestamp=int(NOW.timestamp()),
        )
        with self.assertRaisesRegex(
            WitnessAuthenticationError, "exceeds its campaign lifetime"
        ) as captured:
            verify_witness_request(
                credentials={credential.key_id: credential},
                method="POST",
                path=WITNESS_TRANSITION_PATH,
                body=body,
                headers=headers,
                now=NOW,
            )
        self.assertEqual(captured.exception.code, "witness_campaign_expiry_invalid")

    def test_minimal_service_settings_enforce_distinct_database_identity(self):
        private_key, public_key = keypair()
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "witness.key"
            key_path.write_text(private_key, encoding="utf-8")
            key_path.chmod(0o600)
            common = dict(
                physical_site="webapp_ir",
                writer_witness_service_enabled=True,
                writer_witness_database_url=(
                    "postgresql+asyncpg://witness_runtime:secret@db/writer_witness"
                ),
                writer_witness_product_database_user="product_runtime",
                writer_witness_private_key_file=str(key_path),
                writer_witness_public_key=public_key,
                writer_witness_service_webapp_fi_key_id=FI_CREDENTIAL.key_id,
                writer_witness_service_webapp_fi_secret=FI_CREDENTIAL.secret,
                writer_witness_service_webapp_ir_key_id=IR_CREDENTIAL.key_id,
                writer_witness_service_webapp_ir_secret=IR_CREDENTIAL.secret,
            )
            fake_engine = SimpleNamespace()
            fake_sessions = object()
            with patch(
                "writer_witness_app.create_async_engine", return_value=fake_engine
            ), patch(
                "writer_witness_app.async_sessionmaker", return_value=fake_sessions
            ):
                runtime, engine = _build_runtime_from_settings(
                    WriterWitnessServiceSettings(**common)
                )

            self.assertIs(engine, fake_engine)
            self.assertIs(runtime.session_factory, fake_sessions)
            self.assertEqual(runtime.credentials[FI_CREDENTIAL.key_id].site, "webapp_fi")

            with self.assertRaises(WitnessServiceConfigurationError):
                _build_runtime_from_settings(
                    WriterWitnessServiceSettings(
                        **{**common, "writer_witness_product_database_user": "witness_runtime"}
                    )
                )

    def test_service_module_does_not_import_product_settings(self):
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, writer_witness_app; print('core.config' in sys.modules)",
            ],
            cwd=repo_root,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(repo_root)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "False")

    def test_valid_signature_is_bound_to_site_path_body_and_database_time(self):
        body = b'{"command":"renew"}'
        headers = sign_witness_request(
            credential=FI_CREDENTIAL,
            method="POST",
            path=WITNESS_TRANSITION_PATH,
            body=body,
            request_id="request-1",
            timestamp=int(NOW.timestamp()),
        )
        caller = verify_witness_request(
            credentials={FI_CREDENTIAL.key_id: FI_CREDENTIAL},
            method="POST",
            path=WITNESS_TRANSITION_PATH,
            body=body,
            headers={key.lower(): value for key, value in headers.items()},
            now=NOW,
        )
        self.assertEqual(caller.site, "webapp_fi")
        self.assertEqual(caller.request_id, "request-1")

        for changed_body, changed_path, changed_now in (
            (body + b" ", WITNESS_TRANSITION_PATH, NOW),
            (body, "/v1/other", NOW),
            (body, WITNESS_TRANSITION_PATH, NOW + timedelta(seconds=16)),
        ):
            with self.subTest(body=changed_body, path=changed_path, now=changed_now):
                with self.assertRaises(WitnessAuthenticationError):
                    verify_witness_request(
                        credentials={FI_CREDENTIAL.key_id: FI_CREDENTIAL},
                        method="POST",
                        path=changed_path,
                        body=changed_body,
                        headers={key.lower(): value for key, value in headers.items()},
                        now=changed_now,
                    )


class WriterWitnessServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.private_key, _ = keypair()
        self.state = vacant_state()
        self.clock = MutableClock(NOW)
        self.sessions = SharedSessionFactory()
        runtime = WriterWitnessServiceRuntime(
            session_factory=self.sessions,
            private_key_base64=self.private_key,
            credentials={
                FI_CREDENTIAL.key_id: FI_CREDENTIAL,
                FI_PREVIOUS_CREDENTIAL.key_id: FI_PREVIOUS_CREDENTIAL,
                IR_CREDENTIAL.key_id: IR_CREDENTIAL,
            },
            clock=self.clock,
        )
        self.app = create_writer_witness_app(runtime)
        self.transport = httpx.ASGITransport(app=self.app)
        self.http = httpx.AsyncClient(transport=self.transport, base_url="http://witness.test")
        self.state_loader = patch(
            "core.writer_witness_control.load_witness_state",
            new=AsyncMock(return_value=self.state),
        )
        self.state_loader.start()

    async def asyncTearDown(self):
        self.state_loader.stop()
        await self.http.aclose()

    async def _post(self, credential, body: bytes, *, timestamp: datetime):
        payload = json.loads(body)
        headers = sign_witness_request(
            credential=credential,
            method="POST",
            path=WITNESS_TRANSITION_PATH,
            body=body,
            request_id=payload["request_id"],
            timestamp=int(timestamp.timestamp()),
        )
        return await self.http.post(WITNESS_TRANSITION_PATH, content=body, headers=headers)

    async def test_private_api_authenticates_and_exact_success_replays(self):
        unauthenticated = await self.http.post(WITNESS_TRANSITION_PATH, content=b"{}")
        self.assertEqual(unauthenticated.status_code, 401)

        client = WriterWitnessClient(
            WriterWitnessClientConfig(
                base_url="http://witness.test",
                credential=FI_CREDENTIAL,
            )
        )
        first = await client.transition(
            action="acquire",
            expected_epoch=0,
            expected_lease_id=None,
            request_id="fi-acquire-1",
            reason="initial Finland writer term",
            lease_duration_seconds=180,
            now=NOW,
            client=self.http,
        )
        replay = await client.transition(
            action="acquire",
            expected_epoch=0,
            expected_lease_id=None,
            request_id="fi-acquire-1",
            reason="initial Finland writer term",
            lease_duration_seconds=180,
            now=NOW,
            client=self.http,
        )

        self.assertTrue(first["accepted"])
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["proof"], first["proof"])
        self.assertEqual(self.state.holder_site, "webapp_fi")

    async def test_rejected_acquisition_cannot_be_replayed_after_old_lease_expires(self):
        fi_body = command_body(
            action="acquire",
            epoch=0,
            lease_id=None,
            request_id="fi-acquire",
            reason="initial term",
        )
        fi_response = await self._post(FI_CREDENTIAL, fi_body, timestamp=NOW)
        self.assertEqual(fi_response.status_code, 200)
        lease_id = fi_response.json()["state"]["lease_id"]

        ir_body = command_body(
            action="acquire",
            epoch=1,
            lease_id=lease_id,
            request_id="delayed-ir-acquire",
            reason="promote after outage",
        )
        self.clock.value = NOW + timedelta(seconds=10)
        first_rejection = await self._post(
            IR_CREDENTIAL,
            ir_body,
            timestamp=self.clock.value,
        )
        self.assertEqual(first_rejection.status_code, 409)
        self.assertFalse(first_rejection.json()["replayed"])

        # A delayed packet is freshly authenticated after the prior lease has
        # expired, but its durable negative receipt keeps it one-shot.
        self.clock.value = NOW + timedelta(seconds=181)
        replay_rejection = await self._post(
            IR_CREDENTIAL,
            ir_body,
            timestamp=self.clock.value,
        )
        self.assertEqual(replay_rejection.status_code, 409)
        self.assertTrue(replay_rejection.json()["replayed"])
        self.assertEqual(self.state.holder_site, "webapp_fi")
        self.assertEqual(self.state.writer_epoch, 1)

    async def test_exact_rejection_replays_across_rotation_key_ids(self):
        body = command_body(
            action="acquire",
            epoch=99,
            lease_id=None,
            request_id="rotation-overlap-replay",
            reason="prove durable request identity ignores key generation",
        )
        first = await self._post(FI_PREVIOUS_CREDENTIAL, body, timestamp=NOW)
        replay = await self._post(FI_CREDENTIAL, body, timestamp=NOW)
        self.assertEqual(first.status_code, 409)
        self.assertFalse(first.json()["replayed"])
        self.assertEqual(replay.status_code, 409)
        self.assertTrue(replay.json()["replayed"])

    async def test_campaign_expiry_crossed_inside_transaction_creates_no_receipt(self):
        expiry = NOW + timedelta(seconds=5)
        campaign_credential = WitnessClientCredential(
            key_id="matrix-wwm_0123456789ab-fi",
            site="webapp_fi",
            secret="campaign-secret-0123456789abcdef-0123456789abcdef",
            not_after=expiry,
        )
        sessions = SharedSessionFactory()
        runtime = WriterWitnessServiceRuntime(
            session_factory=sessions,
            private_key_base64=self.private_key,
            credentials={campaign_credential.key_id: campaign_credential},
            # Authentication succeeds before expiry; the fresh database-time
            # read immediately before transition application crosses it.
            clock=SequenceClock(NOW, expiry),
        )
        app = create_writer_witness_app(runtime)
        transport = httpx.ASGITransport(app=app)
        body = command_body(
            action="acquire",
            epoch=0,
            lease_id=None,
            request_id="campaign-expired-in-transaction",
            reason="prove transaction-bound campaign expiry",
        )
        headers = sign_witness_request(
            credential=campaign_credential,
            method="POST",
            path=WITNESS_TRANSITION_PATH,
            body=body,
            request_id="campaign-expired-in-transaction",
            timestamp=int(NOW.timestamp()),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://witness.test",
        ) as client:
            response = await client.post(
                WITNESS_TRANSITION_PATH,
                content=body,
                headers=headers,
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "witness_campaign_expired")
        self.assertEqual(sessions.receipts, {})
        self.assertEqual(self.state.writer_epoch, 0)
        self.assertIsNone(self.state.holder_site)

    async def test_human_approval_relay_keeps_session_on_witness_and_replays_exact_request(self):
        approval_now = NOW
        enrollment = create_enrollment(
            operator="mohsen",
            password="correct horse battery staple",
            now=approval_now,
            scrypt_n=2**14,
        )
        subject = approval_subject(
            artifact_type="three-site-staging-inventory-v3",
            artifact_sha256="a" * 64,
            release_sha="b" * 40,
            bindings={"campaign_id": "campaign-1", "inventory_stage": "provisioned"},
        )
        session_token, _state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password="correct horse battery staple",
            totp=totp_code(enrollment.totp_secret, at=approval_now)[1],
            recovery_code=None,
            release_sha="b" * 40,
            allowed_actions=["approve_inventory"],
            ttl_seconds=48 * 60 * 60,
            now=approval_now,
        )
        relay_sessions = RelaySessionFactory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            session_path = root / "session.json"
            policy_path = root / "policy.json"
            session_path.write_text(json.dumps(session_token), encoding="utf-8")
            policy_path.write_text(json.dumps(enrollment.policy_payload), encoding="utf-8")
            session_path.chmod(0o600)
            policy_path.chmod(0o600)
            runtime = WriterWitnessServiceRuntime(
                session_factory=relay_sessions,
                private_key_base64=self.private_key,
                credentials={RELAY_CREDENTIAL.key_id: RELAY_CREDENTIAL},
                clock=MutableClock(approval_now + timedelta(minutes=1)),
                human_approval_relay_enabled=True,
                human_approval_relay_session_file=str(session_path),
                human_approval_relay_policy_file=str(policy_path),
            )
            app = create_writer_witness_app(runtime)
            body = json.dumps(
                {
                    "schema": "three-site-human-approval-witness-relay-command-v1",
                    "action": "approve_inventory",
                    "environment": "staging",
                    "subject": subject,
                    "request_id": "relay-inventory-001",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            headers = sign_witness_request(
                credential=RELAY_CREDENTIAL,
                method="POST",
                path=WITNESS_HUMAN_APPROVAL_RELAY_PATH,
                body=body,
                request_id="relay-inventory-001",
                timestamp=int((approval_now + timedelta(minutes=1)).timestamp()),
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://witness.test"
            ) as client:
                first = await client.post(
                    WITNESS_HUMAN_APPROVAL_RELAY_PATH,
                    content=body,
                    headers=headers,
                )
                replay = await client.post(
                    WITNESS_HUMAN_APPROVAL_RELAY_PATH,
                    content=body,
                    headers=headers,
                )
                altered = body.replace(b"approve_inventory", b"approve_migration")
                altered_headers = sign_witness_request(
                    credential=RELAY_CREDENTIAL,
                    method="POST",
                    path=WITNESS_HUMAN_APPROVAL_RELAY_PATH,
                    body=altered,
                    request_id="relay-inventory-001",
                    timestamp=int((approval_now + timedelta(minutes=1)).timestamp()),
                )
                conflicting = await client.post(
                    WITNESS_HUMAN_APPROVAL_RELAY_PATH,
                    content=altered,
                    headers=altered_headers,
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(conflicting.status_code, 409)
        receipt = first.json()
        self.assertNotIn(session_token["signature"], str(receipt))
        verify_human_approval(
            receipt,
            policy_payload=enrollment.policy_payload,
            expected_action="approve_inventory",
            expected_environment="staging",
            expected_subject=subject,
            now=approval_now + timedelta(minutes=2),
            witness_relay_public_key=base64.b64encode(
                Ed25519PrivateKey.from_private_bytes(
                    base64.b64decode(self.private_key)
                ).public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            ).decode("ascii"),
        )


if __name__ == "__main__":
    unittest.main()
