from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from core.canonical_json import canonical_json_bytes
from core.human_approval import POLICY_SCHEMA, TOKEN_SCHEMA
from core.human_approval_issuer import DEFAULT_ACTIONS
from core.production_shadow_authorization import (
    APPROVAL_HASH_FIELD,
    AUTHORIZATION_ACTION,
    AUTHORIZATION_BASIS_SCHEMA,
    AUTHORIZATION_ENVIRONMENT,
    POLICY_HASH_FIELD,
    ZERO_SHA256,
    ProductionShadowAuthorizationError,
    authorization_basis_sha256,
    authorization_subject_from_manifest,
    parse_canonical_json_object,
    verify_authorization_documents,
)
from scripts.finalize_production_shadow_cutover_manifest import (
    CutoverManifestFinalizationError,
    build_subject,
    finalize_manifest,
)
from scripts.production_shadow_cutover_controller import validate_manifest
from tests.test_production_shadow_cutover_controller import manifest_payload


def secure_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


class ProductionShadowAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.required_uid = os.geteuid()
        self.private_key = Ed25519PrivateKey.from_private_bytes(b"\x19" * 32)
        public_key = self.private_key.public_key().public_bytes_raw()
        self.policy = {
            "schema": POLICY_SCHEMA,
            "policy_id": str(uuid4()),
            "issuer": {
                "issuer_id": "production-shadow-test-issuer",
                "key_id": "production-shadow-test-key",
                "operator": "owner77",
                "authenticator_id": "production-shadow-test-totp",
                "public_key": base64.b64encode(public_key).decode("ascii"),
            },
            "actions": [
                {
                    "action": AUTHORIZATION_ACTION,
                    "environments": [AUTHORIZATION_ENVIRONMENT],
                    "max_ttl_seconds": 86400,
                }
            ],
        }
        self.policy_bytes = canonical_json_bytes(self.policy)
        self.template = manifest_payload()
        self.template["artifacts"][APPROVAL_HASH_FIELD] = ZERO_SHA256
        self.template["artifacts"][POLICY_HASH_FIELD] = hashlib.sha256(
            self.policy_bytes
        ).hexdigest()
        self.template_path = self.root / "template.json"
        secure_file(
            self.template_path,
            canonical_json_bytes(self.template),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def token(
        self,
        *,
        manifest: dict | None = None,
        action: str = AUTHORIZATION_ACTION,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        selected = self.template if manifest is None else manifest
        current = datetime.now(timezone.utc).replace(microsecond=0)
        issued = issued_at or current - timedelta(seconds=5)
        expires = expires_at or current + timedelta(hours=12)
        unsigned = {
            "schema": TOKEN_SCHEMA,
            "approval_id": str(uuid4()),
            "policy_id": self.policy["policy_id"],
            "policy_hash": hashlib.sha256(self.policy_bytes).hexdigest(),
            "issuer_id": self.policy["issuer"]["issuer_id"],
            "key_id": self.policy["issuer"]["key_id"],
            "operator": self.policy["issuer"]["operator"],
            "authenticator_id": self.policy["issuer"]["authenticator_id"],
            "action": action,
            "environment": AUTHORIZATION_ENVIRONMENT,
            "subject": authorization_subject_from_manifest(selected),
            "issued_at": issued.isoformat().replace("+00:00", "Z"),
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
            "authentication": {"methods": ["password", "totp"]},
        }
        return {
            **unsigned,
            "signature": base64.b64encode(
                self.private_key.sign(canonical_json_bytes(unsigned))
            ).decode("ascii"),
        }

    def test_basis_removes_only_circular_approval_hash(self) -> None:
        template_subject = authorization_subject_from_manifest(self.template)
        final = json.loads(canonical_json_bytes(self.template))
        final["artifacts"][APPROVAL_HASH_FIELD] = "a" * 64

        self.assertEqual(
            authorization_basis_sha256(self.template),
            authorization_basis_sha256(final),
        )
        self.assertEqual(
            authorization_subject_from_manifest(final),
            template_subject,
        )
        self.assertEqual(
            template_subject["artifact_type"],
            AUTHORIZATION_BASIS_SCHEMA,
        )
        changed = json.loads(canonical_json_bytes(final))
        changed["artifacts"]["release_bundle_sha256"] = "f" * 64
        self.assertNotEqual(
            authorization_basis_sha256(final),
            authorization_basis_sha256(changed),
        )
        for field, replacement in (
            ("nginx_shadow_readonly_generation_sha256", "a" * 64),
            ("nginx_shadow_writable_generation_sha256", "b" * 64),
        ):
            with self.subTest(field=field):
                generation_changed = json.loads(canonical_json_bytes(final))
                generation_changed["artifacts"][field] = replacement
                self.assertNotEqual(
                    authorization_basis_sha256(final),
                    authorization_basis_sha256(generation_changed),
                )
                self.assertNotEqual(
                    authorization_subject_from_manifest(final),
                    authorization_subject_from_manifest(
                        generation_changed
                    ),
                )

    def test_subject_and_finalize_publish_exact_canonical_documents(self) -> None:
        subject_output = self.root / "subject.json"
        subject_result = build_subject(
            template_path=self.template_path,
            output_path=subject_output,
            required_uid=self.required_uid,
        )
        self.assertEqual(subject_result["status"], "subject-ready")
        self.assertEqual(subject_result["publication"], "created")
        self.assertEqual(
            parse_canonical_json_object(
                subject_output.read_bytes(),
                label="subject",
            ),
            authorization_subject_from_manifest(self.template),
        )

        token = self.token()
        policy_source = self.root / "policy-source.json"
        approval_source = self.root / "approval-source.json"
        secure_file(
            policy_source,
            (json.dumps(self.policy, sort_keys=True, indent=2) + "\n").encode(),
        )
        secure_file(
            approval_source,
            (json.dumps(token, sort_keys=True, indent=2) + "\n").encode(),
        )
        manifest_output = self.root / "manifest.json"
        policy_output = self.root / "policy.json"
        approval_output = self.root / "approval.json"
        result = finalize_manifest(
            template_path=self.template_path,
            policy_path=policy_source,
            approval_path=approval_source,
            manifest_output=manifest_output,
            policy_output=policy_output,
            approval_output=approval_output,
            required_uid=self.required_uid,
        )

        self.assertEqual(result["status"], "finalized")
        manifest = parse_canonical_json_object(
            manifest_output.read_bytes(),
            label="manifest",
        )
        validate_manifest(manifest)
        verified = verify_authorization_documents(
            manifest,
            approval_bytes=approval_output.read_bytes(),
            policy_bytes=policy_output.read_bytes(),
        )
        self.assertEqual(verified.action, AUTHORIZATION_ACTION)
        self.assertEqual(
            manifest["artifacts"][APPROVAL_HASH_FIELD],
            hashlib.sha256(approval_output.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            manifest["artifacts"][POLICY_HASH_FIELD],
            hashlib.sha256(policy_output.read_bytes()).hexdigest(),
        )
        for path in (manifest_output, policy_output, approval_output):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        repeated = finalize_manifest(
            template_path=self.template_path,
            policy_path=policy_source,
            approval_path=approval_source,
            manifest_output=manifest_output,
            policy_output=policy_output,
            approval_output=approval_output,
            required_uid=self.required_uid,
        )
        self.assertEqual(
            set(repeated["publications"].values()),
            {"existing-exact"},
        )

    def test_finalize_rejects_token_after_any_basis_change(self) -> None:
        token = self.token()
        changed = json.loads(canonical_json_bytes(self.template))
        changed["artifacts"][
            "nginx_shadow_writable_generation_sha256"
        ] = "b" * 64
        changed_path = self.root / "changed-template.json"
        secure_file(changed_path, canonical_json_bytes(changed))
        policy_path = self.root / "policy-source.json"
        token_path = self.root / "approval-source.json"
        secure_file(policy_path, self.policy_bytes)
        secure_file(token_path, canonical_json_bytes(token))

        with self.assertRaisesRegex(
            CutoverManifestFinalizationError,
            "invalid, expired, or bound elsewhere",
        ):
            finalize_manifest(
                template_path=changed_path,
                policy_path=policy_path,
                approval_path=token_path,
                manifest_output=self.root / "manifest.json",
                policy_output=self.root / "policy.json",
                approval_output=self.root / "approval.json",
                required_uid=self.required_uid,
            )

    def test_destination_conflict_is_detected_before_any_output_is_created(self) -> None:
        token = self.token()
        policy_source = self.root / "policy-source.json"
        approval_source = self.root / "approval-source.json"
        secure_file(policy_source, self.policy_bytes)
        secure_file(approval_source, canonical_json_bytes(token))
        manifest_output = self.root / "manifest.json"
        policy_output = self.root / "policy.json"
        approval_output = self.root / "approval.json"
        secure_file(approval_output, b"foreign")

        with self.assertRaisesRegex(
            CutoverManifestFinalizationError,
            "already exists with different bytes",
        ):
            finalize_manifest(
                template_path=self.template_path,
                policy_path=policy_source,
                approval_path=approval_source,
                manifest_output=manifest_output,
                policy_output=policy_output,
                approval_output=approval_output,
                required_uid=self.required_uid,
            )
        self.assertFalse(policy_output.exists())
        self.assertFalse(manifest_output.exists())

    def test_rejects_noncanonical_runtime_approval_and_expired_token(self) -> None:
        final = json.loads(canonical_json_bytes(self.template))
        token = self.token(
            issued_at=datetime.now(timezone.utc) - timedelta(hours=2),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        token_bytes = canonical_json_bytes(token)
        final["artifacts"][APPROVAL_HASH_FIELD] = hashlib.sha256(
            token_bytes
        ).hexdigest()
        with self.assertRaises(ProductionShadowAuthorizationError):
            verify_authorization_documents(
                final,
                approval_bytes=(
                    json.dumps(token, sort_keys=True, indent=2) + "\n"
                ).encode(),
                policy_bytes=self.policy_bytes,
            )
        with self.assertRaisesRegex(
            ProductionShadowAuthorizationError,
            "verification failed",
        ):
            verify_authorization_documents(
                final,
                approval_bytes=token_bytes,
                policy_bytes=self.policy_bytes,
            )

    def test_policy_exposes_one_24_hour_production_deploy_action(self) -> None:
        rows = [
            row
            for row in DEFAULT_ACTIONS
            if row["action"] == AUTHORIZATION_ACTION
        ]
        self.assertEqual(
            rows,
            [
                {
                    "action": AUTHORIZATION_ACTION,
                    "environments": [AUTHORIZATION_ENVIRONMENT],
                    "max_ttl_seconds": 86400,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
