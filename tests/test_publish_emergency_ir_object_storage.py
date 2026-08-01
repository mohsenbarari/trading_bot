from __future__ import annotations

import argparse
import base64
import dataclasses
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import emergency_ir_object_storage_manifest as manifest
from scripts import emergency_ir_object_storage_receiver as receiver
from scripts import publish_emergency_ir_object_storage as publisher
from scripts import run_emergency_ir_object_storage_receive as receiver_bootstrap


CAMPAIGN_ID = "20260801T213000Z-emergency-ir-publish"
RECIPIENT_KEY_ID = "age-recipient-sha256:" + "b" * 64


class FakeClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """A strict in-memory versioned S3 surface; it never opens a socket."""

    def __init__(self, *, versioning: bool = True, private: bool = True, corrupt_get: bool = False) -> None:
        self.versioning = versioning
        self.private = private
        self.corrupt_get = corrupt_get
        self.objects: dict[tuple[str, str], list[tuple[str, bytes]]] = {}
        self.put_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str, str]] = []
        self.head_calls: list[tuple[str, str, str | None]] = []
        self._sequence = 0

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def get_public_access_block(self, *, Bucket: str) -> dict[str, object]:
        enabled = self.private
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": enabled,
                "IgnorePublicAcls": enabled,
                "BlockPublicPolicy": enabled,
                "RestrictPublicBuckets": enabled,
            }
        }

    def get_bucket_policy_status(self, *, Bucket: str) -> dict[str, object]:
        return {"PolicyStatus": {"IsPublic": not self.private}}

    def get_bucket_acl(self, *, Bucket: str) -> dict[str, object]:
        grants: list[dict[str, object]] = []
        if not self.private:
            grants.append(
                {
                    "Grantee": {
                        "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                    }
                }
            )
        return {"Grants": grants}

    def _select(self, bucket: str, key: str, version_id: str | None) -> tuple[str, bytes]:
        versions = self.objects.get((bucket, key))
        if not versions:
            raise FakeClientError("NoSuchKey")
        if version_id is None:
            return versions[-1]
        for item in versions:
            if item[0] == version_id:
                return item
        raise FakeClientError("NoSuchVersion")

    def head_object(self, *, Bucket: str, Key: str, VersionId: str | None = None) -> dict[str, object]:
        self.head_calls.append((Bucket, Key, VersionId))
        version, payload = self._select(Bucket, Key, VersionId)
        return {"VersionId": version, "ContentLength": len(payload)}

    def put_object(self, *, Bucket: str, Key: str, Body: io.BufferedReader, **_kwargs: object) -> dict[str, str]:
        payload = Body.read()
        self._sequence += 1
        version = f"version-{self._sequence:02d}"
        self.objects.setdefault((Bucket, Key), []).append((version, payload))
        self.put_calls.append((Bucket, Key))
        return {"VersionId": version}

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        self.get_calls.append((Bucket, Key, VersionId))
        version, payload = self._select(Bucket, Key, VersionId)
        observed = payload + b"x" if self.corrupt_get else payload
        return {"VersionId": version, "ContentLength": len(payload), "Body": io.BytesIO(observed)}

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, str],
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str:
        if operation != "get_object" or HttpMethod != "GET":
            raise AssertionError("publisher may generate only GET URLs")
        query = urlencode(
            {
                "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
                "X-Amz-Credential": "test/20260801/ir-thr-at1/s3/aws4_request",
                "X-Amz-Date": "20260801T213000Z",
                "X-Amz-Expires": str(ExpiresIn),
                "X-Amz-SignedHeaders": "host",
                "X-Amz-Signature": "a" * 64,
                "versionId": Params["VersionId"],
            }
        )
        return f"{manifest.APPROVED_ARVAN_ENDPOINT}/{Params['Bucket']}/{Params['Key']}?{query}"


class PublishEmergencyIrObjectStorageTests(unittest.TestCase):
    def write_keypair(self, root: Path) -> tuple[Path, Path]:
        private = Ed25519PrivateKey.generate()
        private_path = root / "signing-private.key"
        public_path = root / "signing-public.key"
        private_path.write_text(
            base64.b64encode(
                private.private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )
            ).decode("ascii")
            + "\n",
            encoding="ascii",
        )
        public_path.write_text(
            base64.b64encode(
                private.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            ).decode("ascii")
            + "\n",
            encoding="ascii",
        )
        private_path.chmod(0o600)
        public_path.chmod(0o600)
        return private_path, public_path

    def write_age_ciphertext(self, root: Path, kind: str) -> tuple[Path, dict[str, object]]:
        payload = (
            b"age-encryption.org/v1\n"
            b"-> X25519 ZHVtbXktcmVjaXBpZW50\n"
            b"--- fake-authentication-tag\n"
            + (kind.encode("ascii") + b"-") * 80
        )
        path = root / f"{kind}.age"
        path.write_bytes(payload)
        path.chmod(0o600)
        plaintext = b"sealed-plaintext-metadata:" + kind.encode("ascii")
        return path, {
            "kind": kind,
            "ciphertext_path": str(path),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "plaintext_bytes": len(plaintext),
            "ciphertext_sha256": hashlib.sha256(payload).hexdigest(),
            "ciphertext_bytes": len(payload),
        }

    def write_plan(self, root: Path) -> tuple[Path, publisher.PublishPlan]:
        descriptors = [self.write_age_ciphertext(root, kind)[1] for kind in manifest.ARTIFACT_ORDER]
        payload = {
            "schema": publisher.PUBLISH_PLAN_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "bucket": "emergency-ir-artifacts",
            "prefix": "emergency-ir",
            "created_at": datetime(2026, 8, 1, 21, 30, tzinfo=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "destination_age_recipient_key_id": RECIPIENT_KEY_ID,
            "artifacts": descriptors,
        }
        path = root / "publish-plan.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)
        return path, publisher.load_publish_plan(path)

    def outputs(self, root: Path) -> publisher.PublishOutputs:
        return publisher.PublishOutputs(
            receiver_bundle=root / "receiver.tar.gz",
            sealed_manifest=root / "sealed-manifest.json",
            url_map=root / "presigned-urls.json",
            descriptor=root / "bootstrap-descriptor.json",
        )

    def arguments(
        self,
        *,
        plan: Path,
        private: Path,
        public: Path,
        outputs: publisher.PublishOutputs,
        apply: bool,
        confirm: str | None = None,
        credentials: Path | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            plan=plan,
            signing_private_key=private,
            signing_public_key=public,
            credentials=credentials,
            repo=REPO_ROOT,
            receiver_bundle_output=outputs.receiver_bundle,
            sealed_manifest_output=outputs.sealed_manifest,
            url_map_output=outputs.url_map,
            descriptor_output=outputs.descriptor,
            ttl_seconds=300,
            apply=apply,
            confirm=confirm,
        )

    def test_dry_run_never_constructs_a_client_or_writes_control_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            factory = Mock(side_effect=AssertionError("dry run must not construct an S3 client"))
            result = publisher.execute(
                self.arguments(
                    plan=plan_path, private=private, public=public, outputs=outputs, apply=False
                ),
                client_factory=factory,
            )
            self.assertEqual(result["status"], "planned-no-network")
            self.assertEqual(result["campaign_id"], CAMPAIGN_ID)
            self.assertTrue(result["required_confirmation"].startswith("publish-emergency-ir:"))
            factory.assert_not_called()
            self.assertFalse(any(path.exists() for path in dataclasses.astuple(outputs)))
            self.assertEqual(
                result["required_confirmation"],
                publisher.confirmation_phrase(
                    plan,
                    signer_key_id=publisher._load_public_key_id(public),
                    ttl_seconds=300,
                ),
            )

    def test_apply_rejects_wrong_confirmation_before_client_or_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, _plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            factory = Mock(side_effect=AssertionError("client must remain unused"))
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "confirmation"):
                publisher.execute(
                    self.arguments(
                        plan=plan_path,
                        private=private,
                        public=public,
                        outputs=outputs,
                        apply=True,
                        confirm="publish-emergency-ir:wrong",
                        credentials=root / "credentials.json",
                    ),
                    client_factory=factory,
                )
            factory.assert_not_called()
            self.assertFalse(any(path.exists() for path in dataclasses.astuple(outputs)))

    def test_mocked_publish_binds_versions_and_creates_only_private_bootstrap_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            client = FakeS3()
            confirmation = publisher.confirmation_phrase(
                plan,
                signer_key_id=publisher._load_public_key_id(public),
                ttl_seconds=300,
            )
            result = publisher.execute(
                self.arguments(
                    plan=plan_path,
                    private=private,
                    public=public,
                    outputs=outputs,
                    apply=True,
                    confirm=confirmation,
                    credentials=root / "credentials.json",
                ),
                client_factory=lambda _path: client,
            )
            self.assertEqual(result["status"], "published-sealed")
            self.assertEqual(result["artifact_count"], 4)
            self.assertEqual(len(client.put_calls), 7)
            self.assertEqual(len(client.get_calls), 7)
            self.assertEqual(len(client.objects), 7)
            self.assertNotIn("https://", json.dumps(result))
            for output in dataclasses.astuple(outputs):
                self.assertTrue(output.is_file())
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

            public_key = manifest.load_public_key(public)
            signed_bytes = outputs.sealed_manifest.read_bytes()
            verified = manifest.verify_manifest_bytes(signed_bytes, public_key=public_key)
            receive_plan = verified.as_receive_plan()
            self.assertEqual(verified.manifest_sha256, result["manifest_sha256"])
            self.assertEqual(
                [item["version_id"] for item in receive_plan["artifacts"]],
                [f"version-{index:02d}" for index in range(2, 6)],
            )
            url_map = receiver._parse_url_map(
                outputs.url_map.read_bytes(), manifest_sha256=verified.manifest_sha256
            )
            self.assertEqual(list(url_map), list(manifest.ARTIFACT_ORDER))
            descriptor = receiver_bootstrap.load_descriptor(outputs.descriptor)
            self.assertEqual(descriptor["campaign_id"], CAMPAIGN_ID)
            self.assertEqual(descriptor["expires_in_seconds"], 300)
            self.assertEqual(
                set(descriptor),
                {"schema", "campaign_id", "expires_in_seconds", "receiver_bundle", "manifest", "url_map"},
            )

    def test_bucket_must_be_private_and_versioned_before_any_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            for client in (FakeS3(versioning=False), FakeS3(private=False)):
                with self.subTest(versioning=client.versioning, private=client.private):
                    output_root = root / f"output-{client.versioning}-{client.private}"
                    output_root.mkdir(mode=0o700)
                    with self.assertRaisesRegex(publisher.EmergencyPublisherError, "bucket"):
                        publisher.publish(
                            client=client,
                            plan=plan,
                            signing_private_key_path=private,
                            signing_public_key_path=public,
                            repo=REPO_ROOT,
                            outputs=self.outputs(output_root),
                            ttl_seconds=300,
                        )
                    self.assertEqual(client.put_calls, [])

    def test_corrupt_immutable_readback_blocks_before_manifest_is_written(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "immutable GET|readback"):
                publisher.publish(
                    client=FakeS3(corrupt_get=True),
                    plan=plan,
                    signing_private_key_path=private,
                    signing_public_key_path=public,
                    repo=REPO_ROOT,
                    outputs=outputs,
                    ttl_seconds=300,
                )
            self.assertTrue(outputs.receiver_bundle.exists())
            self.assertFalse(outputs.sealed_manifest.exists())


if __name__ == "__main__":
    unittest.main()
