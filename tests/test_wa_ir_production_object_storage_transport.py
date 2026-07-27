from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.wa_ir_production_object_storage_transport import (
    ARVAN_ENDPOINT,
    PRODUCTION_BUCKET,
    ProductionTransportError,
    load_secure_credentials,
    presign_exact_get,
    publish_age_encrypted,
)


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


class FakeS3Error(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(
        self,
        *,
        versioning: str = "Enabled",
        public: bool = False,
        put_version: str | None = "version-1",
        corrupt_get: bool = False,
        omit_presigned_version: bool = False,
        public_policy: bool = False,
        put_raises_after_store: bool = False,
    ) -> None:
        self.versioning = versioning
        self.public = public
        self.put_version = put_version
        self.corrupt_get = corrupt_get
        self.omit_presigned_version = omit_presigned_version
        self.public_policy = public_policy
        self.put_raises_after_store = put_raises_after_store
        self.objects: dict[tuple[str, str, str], tuple[bytes, dict[str, str]]] = {}
        self.put_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, str]] = []
        self.get_calls: list[dict[str, str]] = []
        self.presign_calls: list[tuple[str, dict[str, object], int]] = []

    def get_bucket_versioning(self, *, Bucket):  # noqa: N803, ARG002
        return {"Status": self.versioning}

    def get_bucket_acl(self, *, Bucket):  # noqa: N803, ARG002
        grants = [
            {
                "Grantee": {"Type": "CanonicalUser"},
                "Permission": "FULL_CONTROL",
            }
        ]
        if self.public:
            grants.append(
                {
                    "Grantee": {
                        "Type": "Group",
                        "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                    },
                    "Permission": "READ",
                }
            )
        return {"Grants": grants}

    def get_bucket_policy(self, *, Bucket):  # noqa: N803, ARG002
        if not self.public_policy:
            raise FakeS3Error("NoSuchBucketPolicy")
        return {
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{Bucket}/*",
                        }
                    ],
                }
            )
        }

    def put_object(self, **kwargs):  # noqa: ANN003, ANN201
        self.put_calls.append(dict(kwargs))
        body = kwargs["Body"].read()
        if kwargs.get("IfNoneMatch") == "*" and any(
            bucket == kwargs["Bucket"] and key == kwargs["Key"]
            for bucket, key, _version in self.objects
        ):
            raise RuntimeError("create-only precondition failed")
        version = self.put_version
        if version:
            self.objects[(kwargs["Bucket"], kwargs["Key"], version)] = (
                body,
                dict(kwargs["Metadata"]),
            )
        if self.put_raises_after_store:
            self.put_raises_after_store = False
            raise RuntimeError("connection lost after accept")
        return {"VersionId": version}

    def head_object(self, **kwargs):  # noqa: ANN003, ANN201
        self.head_calls.append(dict(kwargs))
        version = kwargs.get("VersionId")
        if version is None:
            matches = [
                (candidate_version, payload, metadata)
                for (bucket, key, candidate_version), (payload, metadata)
                in self.objects.items()
                if bucket == kwargs["Bucket"] and key == kwargs["Key"]
            ]
            if not matches:
                raise FakeS3Error("NoSuchKey")
            version, payload, metadata = matches[-1]
        else:
            try:
                payload, metadata = self.objects[
                    (kwargs["Bucket"], kwargs["Key"], version)
                ]
            except KeyError as exc:
                raise FakeS3Error("NoSuchKey") from exc
        return {
            "VersionId": version,
            "ContentLength": len(payload),
            "Metadata": metadata,
        }

    def get_object(self, **kwargs):  # noqa: ANN003, ANN201
        self.get_calls.append(dict(kwargs))
        payload, metadata = self.objects[
            (kwargs["Bucket"], kwargs["Key"], kwargs["VersionId"])
        ]
        if self.corrupt_get:
            payload += b"corrupt"
        return {
            "VersionId": kwargs["VersionId"],
            "ContentLength": len(payload),
            "Metadata": metadata,
            "Body": io.BytesIO(payload),
        }

    def generate_presigned_url(
        self, operation, *, Params, ExpiresIn  # noqa: N803
    ):  # noqa: ANN001, ANN201
        self.presign_calls.append((operation, dict(Params), ExpiresIn))
        suffix = "" if self.omit_presigned_version else f"&versionId={Params['VersionId']}"
        return (
            f"{ARVAN_ENDPOINT}/{Params['Bucket']}/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}{suffix}"
        )


def fake_encrypt(
    source: Path,
    output: Path,
    recipient: str,
    *,
    max_bytes: int,  # noqa: ARG001
) -> tuple[str, int]:
    assert recipient == RECIPIENT
    output.write_bytes(b"age-encrypted:" + source.read_bytes())
    output.chmod(0o600)
    payload = output.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


class ProductionObjectStorageTransportTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        source = root / "payload.tar"
        source.write_bytes(b"private production payload")
        source.chmod(0o600)
        recipient = root / "recipient.txt"
        recipient.write_text(RECIPIENT + "\n", encoding="utf-8")
        recipient.chmod(0o600)
        return source, recipient

    def credentials(self, root: Path) -> Path:
        path = root / "arvan.env"
        path.write_text(
            "ARVAN_S3_ACCESS_KEY=access-key\n"
            f"ARVAN_S3_SECRET_KEY={'s' * 40}\n"
            f"ARVAN_S3_ENDPOINT={ARVAN_ENDPOINT}\n"
            "ARVAN_S3_REGION=ir-thr-at1\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def publish(
        self,
        root: Path,
        client: FakeS3,
        *,
        nonce: str = "1" * 32,
    ):  # noqa: ANN201
        source, recipient = self.fixture(root)
        with (
            patch(
                "scripts.wa_ir_production_object_storage_transport.encrypt_age_file",
                side_effect=fake_encrypt,
            ),
            patch(
                "scripts.wa_ir_production_object_storage_transport.uuid.uuid4"
            ) as uuid4,
        ):
            uuid4.return_value.hex = nonce
            return publish_age_encrypted(
                source,
                recipient_file=recipient,
                bucket=PRODUCTION_BUCKET,
                prefix="dark-standby/production-transfer",
                operation_id=OPERATION_ID,
                artifact_kind="release-bundle",
                client=client,
                journal_path=root / "publication-journal.json",
                metadata={"release-sha": "a" * 40},
                max_bytes=1024,
            )

    def test_secure_credentials_are_pinned_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self.credentials(Path(raw))
            credentials = load_secure_credentials(path)
            self.assertEqual(credentials.endpoint, ARVAN_ENDPOINT)
            self.assertNotIn("access-key", repr(credentials))
            self.assertNotIn("s" * 40, repr(credentials))

            path.chmod(0o644)
            with self.assertRaisesRegex(ProductionTransportError, "unsafe"):
                load_secure_credentials(path)

    def test_config_endpoint_drift_and_extra_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = self.credentials(root)
            payload = path.read_text(encoding="utf-8")
            path.write_text(
                payload.replace(ARVAN_ENDPOINT, "https://attacker.invalid"),
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(ProductionTransportError, "drifted"):
                load_secure_credentials(path)
            path.write_text(payload + "UNEXPECTED=value\n", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(ProductionTransportError, "fields"):
                load_secure_credentials(path)

    def test_publish_is_private_create_only_unique_and_exact_version_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeS3()
            published = self.publish(Path(raw), fake)

        self.assertEqual(len(fake.put_calls), 1)
        put = fake.put_calls[0]
        self.assertEqual(put["ACL"], "private")
        self.assertEqual(put["IfNoneMatch"], "*")
        self.assertNotIn("ServerSideEncryption", put)
        self.assertIn(OPERATION_ID, published.object_key)
        self.assertIn("/release-bundle/" + "1" * 32 + "-", published.object_key)
        self.assertEqual(fake.head_calls[0]["VersionId"], "version-1")
        self.assertEqual(fake.get_calls[0]["VersionId"], "version-1")
        self.assertEqual(published.metadata["operation-id"], OPERATION_ID)
        self.assertEqual(
            published.metadata["plaintext-sha256"],
            published.plaintext_sha256,
        )

    def test_unique_keys_and_exact_collision_recovery_do_not_overwrite(self) -> None:
        fake = FakeS3()
        with (
            tempfile.TemporaryDirectory() as first_raw,
            tempfile.TemporaryDirectory() as second_raw,
            tempfile.TemporaryDirectory() as collision_raw,
        ):
            first = self.publish(Path(first_raw), fake, nonce="1" * 32)
            second = self.publish(Path(second_raw), fake, nonce="2" * 32)
            recovered = self.publish(Path(collision_raw), fake, nonce="1" * 32)
        self.assertNotEqual(first.object_key, second.object_key)
        self.assertEqual(recovered.object_key, first.object_key)
        self.assertEqual(recovered.version_id, first.version_id)
        self.assertEqual(len(fake.objects), 2)

    def test_presigned_url_is_exact_version_and_redacted_from_durable_forms(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeS3()
            published = self.publish(Path(raw), fake)
            ephemeral = presign_exact_get(fake, published, ttl_seconds=300)

        url = ephemeral.reveal_for_control_channel()
        self.assertIn("versionId=version-1", url)
        self.assertNotIn(url, repr(ephemeral))
        self.assertNotIn(url, str(ephemeral))
        evidence = published.evidence()
        self.assertNotIn('"url":', json.dumps(evidence))
        self.assertNotIn("X-Amz-", json.dumps(evidence))
        self.assertFalse(evidence["presigned_url_persisted"])
        self.assertEqual(
            fake.presign_calls[0][1]["VersionId"],
            published.version_id,
        )

    def test_public_or_unversioned_bucket_fails_before_put(self) -> None:
        for fake, message in (
            (FakeS3(versioning="Suspended"), "not versioned"),
            (FakeS3(public=True), "ACL is public"),
            (FakeS3(public_policy=True), "policy permits public"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as raw:
                with self.assertRaisesRegex(ProductionTransportError, message):
                    self.publish(Path(raw), fake)
                self.assertEqual(fake.put_calls, [])

    def test_put_without_version_id_uses_only_bounded_recovery_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeS3(put_version=None)
            with self.assertRaisesRegex(ProductionTransportError, "not proven"):
                self.publish(Path(raw), fake)
            self.assertEqual(len(fake.head_calls), 1)
            self.assertNotIn("VersionId", fake.head_calls[0])
            self.assertEqual(fake.get_calls, [])

    def test_corrupt_exact_version_readback_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeS3(corrupt_get=True)
            with self.assertRaisesRegex(
                ProductionTransportError,
                "GET metadata differs|exceeded its bound|hash or size differs",
            ):
                self.publish(Path(raw), fake)
            self.assertEqual(fake.head_calls[0]["VersionId"], "version-1")
            self.assertEqual(fake.get_calls[0]["VersionId"], "version-1")

    def test_presign_without_exact_version_query_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            publisher = FakeS3()
            published = self.publish(Path(raw), publisher)
            signer = FakeS3(omit_presigned_version=True)
            with self.assertRaisesRegex(ProductionTransportError, "exact Arvan"):
                presign_exact_get(signer, published, ttl_seconds=300)

    def test_presign_rejects_a_manually_forged_object_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeS3()
            published = self.publish(Path(raw), fake)
            forged = replace(published, object_key="unrelated/private-object")
            with self.assertRaisesRegex(ProductionTransportError, "published object"):
                presign_exact_get(fake, forged, ttl_seconds=300)
            self.assertEqual(fake.presign_calls, [])

    def test_unsafe_recipient_and_invalid_scope_fail_before_put(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source, recipient = self.fixture(root)
            recipient.chmod(0o644)
            fake = FakeS3()
            with self.assertRaisesRegex(ProductionTransportError, "unsafe"):
                publish_age_encrypted(
                    source,
                    recipient_file=recipient,
                    bucket=PRODUCTION_BUCKET,
                    prefix="dark-standby/production-transfer",
                    operation_id=OPERATION_ID,
                    artifact_kind="release-bundle",
                    client=fake,
                    journal_path=root / "unsafe-recipient-journal.json",
                )
            self.assertEqual(fake.put_calls, [])

            recipient.chmod(0o600)
            with self.assertRaisesRegex(ProductionTransportError, "isolated"):
                publish_age_encrypted(
                    source,
                    recipient_file=recipient,
                    bucket=PRODUCTION_BUCKET,
                    prefix="staging/transfer",
                    operation_id=OPERATION_ID,
                    artifact_kind="release-bundle",
                    client=fake,
                    journal_path=root / "invalid-prefix-journal.json",
                )
            self.assertEqual(fake.put_calls, [])

    def test_ambiguous_put_recovers_same_key_and_exact_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeS3(put_raises_after_store=True)
            published = self.publish(root, fake)
            self.assertEqual(published.version_id, "version-1")
            self.assertEqual(len(fake.objects), 1)
            journal = json.loads((root / "publication-journal.json").read_text())
            self.assertEqual(journal["phase"], "verified")
            for name in (
                "publication-journal.json",
                "publication-journal.json.lock",
                "publication-journal.json.payload.age",
            ):
                self.assertEqual(
                    stat.S_IMODE((root / name).stat().st_mode),
                    0o600,
                )

    def test_failed_readback_resumes_without_a_second_put(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeS3(corrupt_get=True)
            with self.assertRaises(ProductionTransportError):
                self.publish(root, fake)
            self.assertEqual(len(fake.put_calls), 1)
            fake.corrupt_get = False
            published = self.publish(root, fake)
            self.assertEqual(published.version_id, "version-1")
            self.assertEqual(len(fake.put_calls), 1)

    def test_journal_rejects_changed_source_or_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeS3()
            self.publish(root, fake)
            (root / "payload.tar").write_bytes(b"changed")
            (root / "payload.tar").chmod(0o600)
            with self.assertRaisesRegex(
                ProductionTransportError,
                "different inputs",
            ):
                publish_age_encrypted(
                    root / "payload.tar",
                    recipient_file=root / "recipient.txt",
                    bucket=PRODUCTION_BUCKET,
                    prefix="dark-standby/production-transfer",
                    operation_id=OPERATION_ID,
                    artifact_kind="release-bundle",
                    client=fake,
                    journal_path=root / "publication-journal.json",
                    metadata={"release-sha": "a" * 40},
                    max_bytes=1024,
                )
            self.assertEqual(len(fake.put_calls), 1)

    def test_verified_journal_replay_performs_no_second_put(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fake = FakeS3()
            first = self.publish(root, fake)
            second = self.publish(root, fake)
            self.assertEqual(first, second)
            self.assertEqual(len(fake.put_calls), 1)

    def test_journal_parent_and_existing_path_must_be_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source, recipient = self.fixture(root)
            journal_parent = root / "journal-parent"
            journal_parent.mkdir(mode=0o755)
            fake = FakeS3()
            with self.assertRaisesRegex(ProductionTransportError, "directory is unsafe"):
                publish_age_encrypted(
                    source,
                    recipient_file=recipient,
                    bucket=PRODUCTION_BUCKET,
                    prefix="dark-standby/production-transfer",
                    operation_id=OPERATION_ID,
                    artifact_kind="release-bundle",
                    client=fake,
                    journal_path=journal_parent / "publication.json",
                    max_bytes=1024,
                )
            self.assertEqual(fake.put_calls, [])

            journal_parent.chmod(0o700)
            target = journal_parent / "target"
            target.write_text("{}\n")
            target.chmod(0o600)
            (journal_parent / "publication.json").symlink_to(target)
            with self.assertRaisesRegex(
                ProductionTransportError,
                "journal is unavailable or invalid",
            ):
                publish_age_encrypted(
                    source,
                    recipient_file=recipient,
                    bucket=PRODUCTION_BUCKET,
                    prefix="dark-standby/production-transfer",
                    operation_id=OPERATION_ID,
                    artifact_kind="release-bundle",
                    client=fake,
                    journal_path=journal_parent / "publication.json",
                    max_bytes=1024,
                )
            self.assertEqual(os.lstat(journal_parent / "publication.json").st_nlink, 1)
            self.assertEqual(fake.put_calls, [])


if __name__ == "__main__":
    unittest.main()
