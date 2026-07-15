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
    WITNESS_TRANSITION_PATH,
    WitnessAuthenticationError,
    WitnessClientCredential,
    sign_witness_request,
    verify_witness_request,
)
from core.writer_witness_client import WriterWitnessClient, WriterWitnessClientConfig
from models.webapp_writer_state import WebappWriterWitnessReceipt
from writer_witness_app import (
    WitnessServiceConfigurationError,
    WriterWitnessServiceRuntime,
    WriterWitnessServiceSettings,
    _build_runtime_from_settings,
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


class WriterWitnessAuthenticationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
