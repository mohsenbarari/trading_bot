from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest

from botocore.exceptions import ClientError

from scripts.harden_three_site_stage3_object_storage import (
    STAGING_BUCKET,
    Stage3ObjectStorageError,
    confirmation_phrase,
    execute,
)


CAMPAIGN = "fd34231d-f52e-498a-aab4-438c99d88fc5"
PREFIX = f"staging/{CAMPAIGN}/"


def _missing(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "missing"}},
        "GetConfiguration",
    )


class _FakeS3:
    def __init__(
        self,
        *,
        public: bool = False,
        policy_public: bool = False,
        provider_policy_public: bool = False,
    ):
        self.public = public
        self.policy_public = policy_public
        self.provider_policy_public = provider_policy_public
        self.encryption: dict = {
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        }
        self.public_access_block: dict = {
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }
        self.lifecycle_rules: list[dict] = []
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str, str]] = {}
        self.mutations: list[str] = []

    def head_bucket(self, *, Bucket):  # noqa: N803, ARG002
        return {}

    def get_bucket_versioning(self, *, Bucket):  # noqa: N803, ARG002
        return {"Status": "Enabled"}

    def get_bucket_acl(self, *, Bucket):  # noqa: N803, ARG002
        uri = "http://acs.amazonaws.com/groups/global/AllUsers" if self.public else None
        grantee = {"Type": "Group", "URI": uri} if uri else {"Type": "CanonicalUser"}
        return {"Grants": [{"Grantee": grantee, "Permission": "FULL_CONTROL"}]}

    def get_bucket_encryption(self, *, Bucket):  # noqa: N803, ARG002
        if not self.encryption:
            raise _missing("ServerSideEncryptionConfigurationNotFoundError")
        return {"ServerSideEncryptionConfiguration": self.encryption}

    def get_public_access_block(self, *, Bucket):  # noqa: N803, ARG002
        if not self.public_access_block:
            raise _missing("NoSuchPublicAccessBlockConfiguration")
        return {"PublicAccessBlockConfiguration": self.public_access_block}

    def get_bucket_lifecycle_configuration(self, *, Bucket):  # noqa: N803, ARG002
        if not self.lifecycle_rules:
            raise _missing("NoSuchLifecycleConfiguration")
        return {"Rules": self.lifecycle_rules}

    def get_bucket_policy_status(self, *, Bucket):  # noqa: N803, ARG002
        return {"PolicyStatus": {"IsPublic": self.provider_policy_public}}

    def get_bucket_policy(self, *, Bucket):  # noqa: N803, ARG002
        principal = "*" if self.policy_public else "arn:aws:iam:::user/p1:specific-key"
        return {
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": principal},
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{STAGING_BUCKET}/*",
                        }
                    ],
                }
            )
        }

    def delete_bucket_encryption(self, *, Bucket):  # noqa: N803, ARG002
        self.mutations.append("delete-incompatible-encryption-config")
        self.encryption = {}

    def delete_public_access_block(self, *, Bucket):  # noqa: N803, ARG002
        self.mutations.append("delete-incompatible-public-access-block")
        self.public_access_block = {}

    def put_bucket_lifecycle_configuration(self, *, Bucket, LifecycleConfiguration):  # noqa: N803, ARG002
        self.mutations.append("lifecycle")
        self.lifecycle_rules = LifecycleConfiguration["Rules"]

    def put_object(
        self,
        *,
        Bucket,
        Key,
        Body,
        ContentLength,
        ContentType,
        Metadata,
    ):  # noqa: N803, ARG002
        self.mutations.append("probe")
        payload = bytes(Body)
        assert len(payload) == ContentLength
        version = "version-1"
        self.objects[(Bucket, Key)] = (payload, dict(Metadata), version)
        return {"VersionId": version}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        payload, metadata, version = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(payload),
            "Metadata": metadata,
            "VersionId": version,
        }

    def get_object(self, *, Bucket, Key, VersionId):  # noqa: N803
        payload, _metadata, version = self.objects[(Bucket, Key)]
        assert VersionId == version
        return {"Body": io.BytesIO(payload)}


def _args(root: Path, **overrides) -> argparse.Namespace:
    values = {
        "credentials": root / "unused.env",
        "bucket": STAGING_BUCKET,
        "prefix": PREFIX,
        "output_dir": root / "evidence",
        "apply": False,
        "confirm": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class HardenThreeSiteStage3ObjectStorageTests(unittest.TestCase):
    def test_dry_run_audits_without_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3()
            result = execute(_args(Path(raw)), client=fake)
            self.assertEqual(result["status"], "planned")
            self.assertEqual(fake.mutations, [])
            self.assertFalse(result["bucket_or_object_delete"])

    def test_apply_hardens_and_verifies_encrypted_versioned_roundtrip(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3()
            args = _args(
                Path(raw),
                apply=True,
                confirm=confirmation_phrase(STAGING_BUCKET, PREFIX),
            )
            result = execute(args, client=fake)
            self.assertEqual(
                result["status"],
                "private-versioned-lifecycle-client-encrypted-readback-verified",
            )
            self.assertEqual(
                fake.mutations,
                [
                    "delete-incompatible-encryption-config",
                    "delete-incompatible-public-access-block",
                    "lifecycle",
                    "probe",
                ],
            )
            evidence_path = Path(result["evidence"])
            self.assertEqual(evidence_path.stat().st_mode & 0o777, 0o600)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertTrue(evidence["after"]["campaign_lifecycle_exact"])
            self.assertFalse(evidence["after"]["server_default_encryption_configured"])
            self.assertFalse(evidence["after"]["public_access_block_configured"])
            self.assertTrue(evidence["probe"]["readback_verified"])
            self.assertFalse(evidence["probe"]["ephemeral_key_persisted"])
            self.assertFalse(evidence["bucket_or_object_deleted"])

    def test_unpinned_bucket_fails_before_any_request(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3()
            with self.assertRaisesRegex(Stage3ObjectStorageError, "not the pinned"):
                execute(_args(Path(raw), bucket="production-bucket"), client=fake)
            self.assertEqual(fake.mutations, [])

    def test_public_acl_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3(public=True)
            with self.assertRaisesRegex(Stage3ObjectStorageError, "ACL is public"):
                execute(
                    _args(
                        Path(raw),
                        apply=True,
                        confirm=confirmation_phrase(STAGING_BUCKET, PREFIX),
                    ),
                    client=fake,
                )
            self.assertEqual(fake.mutations, [])

    def test_provider_public_flag_with_specific_principal_is_recorded_not_blocked(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3(provider_policy_public=True)
            result = execute(_args(Path(raw)), client=fake)
            self.assertFalse(result["before"]["bucket_policy_public"])
            self.assertTrue(result["before"]["provider_policy_status_public"])
            self.assertEqual(fake.mutations, [])

    def test_wildcard_allow_policy_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3(policy_public=True)
            with self.assertRaisesRegex(Stage3ObjectStorageError, "policy is public"):
                execute(
                    _args(
                        Path(raw),
                        apply=True,
                        confirm=confirmation_phrase(STAGING_BUCKET, PREFIX),
                    ),
                    client=fake,
                )
            self.assertEqual(fake.mutations, [])

    def test_confirmation_mismatch_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3()
            with self.assertRaisesRegex(Stage3ObjectStorageError, "confirmation mismatch"):
                execute(_args(Path(raw), apply=True, confirm="wrong"), client=fake)
            self.assertEqual(fake.mutations, [])

    def test_conflicting_campaign_lifecycle_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = _FakeS3()
            fake.lifecycle_rules = [
                {
                    "ID": f"three-site-stage3-{CAMPAIGN}-retention",
                    "Filter": {"Prefix": PREFIX},
                    "Status": "Enabled",
                    "Expiration": {"Days": 1},
                }
            ]
            with self.assertRaisesRegex(Stage3ObjectStorageError, "conflicting content"):
                execute(
                    _args(
                        Path(raw),
                        apply=True,
                        confirm=confirmation_phrase(STAGING_BUCKET, PREFIX),
                    ),
                    client=fake,
                )
            self.assertEqual(fake.mutations, [])


if __name__ == "__main__":
    unittest.main()
