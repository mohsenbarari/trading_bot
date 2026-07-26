from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.human_approval import (
    approval_subject,
    issue_human_approval_relay_receipt,
    parse_human_approval_relay_command,
)
from core.human_approval_issuer import (
    authenticate_and_issue_session,
    create_enrollment,
    totp_code,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/request_three_site_human_approval_relay.py"
SPEC = importlib.util.spec_from_file_location("human_approval_relay_request", SCRIPT_PATH)
assert SPEC and SPEC.loader
relay_request = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay_request)


class _Response:
    status_code = 200

    def __init__(self, payload):  # noqa: ANN001
        self.payload = payload

    def json(self):
        return self.payload


class RelayRequestTests(unittest.TestCase):
    def test_controller_receives_only_a_verified_action_receipt(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment = create_enrollment(
            operator="mohsen",
            password="correct horse battery staple",
            now=now,
            scrypt_n=2**14,
        )
        subject = approval_subject(
            artifact_type="three-site-staging-inventory-v3",
            artifact_sha256="a" * 64,
            release_sha="b" * 40,
            bindings={"campaign_id": "campaign-1", "inventory_stage": "provisioned"},
        )
        session, _state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password="correct horse battery staple",
            totp=totp_code(enrollment.totp_secret, at=now)[1],
            recovery_code=None,
            release_sha="b" * 40,
            allowed_actions=["approve_inventory"],
            ttl_seconds=48 * 60 * 60,
            now=now,
        )
        witness = Ed25519PrivateKey.generate()
        witness_public = base64.b64encode(
            witness.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii")

        class FakeClient:
            def __init__(self, **kwargs):  # noqa: ANN003
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def post(self, path, *, content, headers):  # noqa: ANN001
                self.assertEqual(path, "/v1/human-approval/relay")
                self.assertEqual(headers["X-Writer-Witness-Site"], "orchestrator")
                command = parse_human_approval_relay_command(json.loads(content))
                receipt = issue_human_approval_relay_receipt(
                    session,
                    policy_payload=enrollment.policy_payload,
                    command=command,
                    witness_private_key=witness,
                    now=now + timedelta(seconds=1),
                    receipt_id="11111111-1111-4111-8111-111111111111",
                )
                return _Response(receipt)

        # Bind unittest assertions into the fake client without exposing the
        # session anywhere outside this test's temporary owner-controlled root.
        FakeClient.assertEqual = self.assertEqual
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            subject_path = root / "subject.json"
            policy_path = root / "policy.json"
            credentials_path = root / "relay.env"
            ca_bundle = root / "ca.crt"
            output = root / "receipt.json"
            subject_path.write_text(json.dumps(subject), encoding="utf-8")
            policy_path.write_text(json.dumps(enrollment.policy_payload), encoding="utf-8")
            credentials_path.write_text(
                "\n".join(
                    (
                        "HUMAN_APPROVAL_RELAY_WITNESS_URL=https://witness.test",
                        "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID=relay-key-1",
                        "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET=" + "a" * 64,
                        f"HUMAN_APPROVAL_RELAY_CA_BUNDLE={ca_bundle}",
                        f"WRITER_WITNESS_PUBLIC_KEY={witness_public}",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            ca_bundle.write_text("test-ca\n", encoding="utf-8")
            for path in (subject_path, policy_path, credentials_path):
                path.chmod(0o600)
            ca_bundle.chmod(0o644)
            args = relay_request.argparse.Namespace(
                action="approve_inventory",
                subject=subject_path,
                policy=policy_path,
                credentials=credentials_path,
                output=output,
                timeout_seconds=5.0,
            )
            with patch.object(relay_request.httpx, "Client", FakeClient):
                result = relay_request.request_receipt(args)

            self.assertEqual(result["status"], "approved")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertNotIn(session["signature"], str(receipt))
            self.assertNotIn("allowed_actions", receipt)


if __name__ == "__main__":
    unittest.main()
