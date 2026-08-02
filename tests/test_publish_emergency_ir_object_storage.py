from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
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
RECIPIENT_KEY_ID = "age-recipient-sha256:8ab221e2abb62642e85960a38ba07f2de379d1744c222a44efcc922cf435418d"


class FakeClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """A strict in-memory versioned S3 surface; it never opens a socket."""

    OWNER_ID = "emergency-owner-canonical-id"

    def __init__(
        self,
        *,
        versioning: bool = True,
        private: bool = True,
        corrupt_get: bool = False,
        foreign_bucket_grant: bool = False,
        foreign_object_grant: bool = False,
        has_bucket_policy: bool = False,
        public_access_block_error_code: str | None = None,
    ) -> None:
        self.versioning = versioning
        self.private = private
        self.corrupt_get = corrupt_get
        self.foreign_bucket_grant = foreign_bucket_grant
        self.foreign_object_grant = foreign_object_grant
        self.has_bucket_policy = has_bucket_policy
        self.public_access_block_error_code = public_access_block_error_code
        self.objects: dict[tuple[str, str], list[tuple[str, bytes]]] = {}
        self.put_calls: list[tuple[str, str]] = []
        self.put_kwargs: list[dict[str, object]] = []
        self.get_calls: list[tuple[str, str, str]] = []
        self.head_calls: list[tuple[str, str, str | None]] = []
        self.object_acl_calls: list[tuple[str, str, str]] = []
        self.public_access_block_calls = 0
        self.bucket_policy_calls = 0
        self.bucket_acl_calls = 0
        self._sequence = 0

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:
        return {"Status": "Enabled" if self.versioning else "Suspended"}

    def get_public_access_block(self, *, Bucket: str) -> dict[str, object]:
        self.public_access_block_calls += 1
        if self.public_access_block_error_code is not None:
            raise FakeClientError(self.public_access_block_error_code)
        enabled = self.private
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": enabled,
                "IgnorePublicAcls": enabled,
                "BlockPublicPolicy": enabled,
                "RestrictPublicBuckets": enabled,
            }
        }

    def get_bucket_policy(self, *, Bucket: str) -> dict[str, object]:
        self.bucket_policy_calls += 1
        if not self.has_bucket_policy:
            raise FakeClientError("NoSuchBucketPolicy")
        return {"Policy": "{\\\"Statement\\\":[]}"}

    def _acl(self, *, foreign_grant: bool) -> dict[str, object]:
        grants: list[dict[str, object]] = [
            {
                "Grantee": {"Type": "CanonicalUser", "ID": self.OWNER_ID},
                "Permission": "FULL_CONTROL",
            }
        ]
        if foreign_grant:
            grants.append(
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "unapproved-foreign-principal"},
                    "Permission": "READ",
                }
            )
        if not self.private:
            grants.append(
                {
                    "Grantee": {
                        "Type": "Group",
                        "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                    },
                    "Permission": "READ",
                }
            )
        return {"Owner": {"ID": self.OWNER_ID}, "Grants": grants}

    def get_bucket_acl(self, *, Bucket: str) -> dict[str, object]:
        self.bucket_acl_calls += 1
        return self._acl(foreign_grant=self.foreign_bucket_grant)

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

    def list_object_versions(self, *, Bucket: str, Prefix: str) -> dict[str, object]:
        versions = [
            {"Key": key, "VersionId": version}
            for (bucket, key), entries in self.objects.items()
            if bucket == Bucket and key.startswith(Prefix)
            for version, _payload in entries
        ]
        return {"IsTruncated": False, "Versions": versions, "DeleteMarkers": []}

    def put_object(self, *, Bucket: str, Key: str, Body: io.BufferedReader, **kwargs: object) -> dict[str, str]:
        if kwargs.get("ACL") != "private" or kwargs.get("IfNoneMatch") != "*":
            raise AssertionError("publisher must use private conditional object creation")
        if (Bucket, Key) in self.objects:
            raise FakeClientError("PreconditionFailed")
        payload = Body.read()
        self._sequence += 1
        version = f"version-{self._sequence:02d}"
        self.objects.setdefault((Bucket, Key), []).append((version, payload))
        self.put_calls.append((Bucket, Key))
        self.put_kwargs.append(dict(kwargs))
        return {"VersionId": version}

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        self.get_calls.append((Bucket, Key, VersionId))
        version, payload = self._select(Bucket, Key, VersionId)
        observed = payload + b"x" if self.corrupt_get else payload
        return {"VersionId": version, "ContentLength": len(payload), "Body": io.BytesIO(observed)}

    def get_object_acl(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        self._select(Bucket, Key, VersionId)
        self.object_acl_calls.append((Bucket, Key, VersionId))
        return self._acl(foreign_grant=self.foreign_object_grant)

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
    def bootstrap_provenance(
        self, public_key: Path, *, revision: str | None = None
    ) -> publisher.BootstrapProvenance:
        revision = revision or subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        bundle_sha256, bundle_bytes = publisher.receiver_bundle.bundle_digest(
            signing_public_key=public_key,
            source_revision=revision,
        )
        return publisher.BootstrapProvenance(
            publisher_source_revision=revision,
            receiver_bundle_sha256=bundle_sha256,
            receiver_bundle_bytes=bundle_bytes,
            signer_key_id=publisher._load_public_key_id(public_key),
        )

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
            provenance = self.bootstrap_provenance(public)
            with patch.object(publisher, "_bootstrap_provenance", return_value=provenance):
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
                    bootstrap_provenance=provenance,
                    ttl_seconds=300,
                ),
            )
            self.assertEqual(result["bootstrap_provenance"], provenance.as_manifest())
            scope = result["confirmation_scope"]
            self.assertEqual(scope["campaign_id"], CAMPAIGN_ID)
            self.assertEqual(scope["destination_age_recipient"], publisher.WA_IR_AGE_RECIPIENT)
            self.assertEqual(scope["destination_age_recipient_key_id"], RECIPIENT_KEY_ID)
            self.assertEqual(scope["bootstrap"], provenance.as_manifest())
            self.assertEqual(
                [item["ciphertext_sha256"] for item in scope["artifacts"]],
                [item.ciphertext_sha256 for item in plan.artifacts],
            )
            self.assertNotIn("ciphertext_path", json.dumps(scope))

    def test_preflight_pins_checkout_revision_and_exact_bundle_digest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _private, public = self.write_keypair(root)
            provenance = publisher._bootstrap_provenance(signing_public_key_path=public)
            revision = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
            digest, size = publisher.receiver_bundle.bundle_digest(
                signing_public_key=public,
                source_revision=revision,
            )
            self.assertEqual(provenance.publisher_source_revision, revision)
            self.assertEqual(provenance.receiver_bundle_sha256, digest)
            self.assertEqual(provenance.receiver_bundle_bytes, size)
            self.assertIn("scripts/emergency_ir_standalone_activate.py", publisher.PUBLISHER_SOURCE_PATHS)
            self.assertNotIn("scripts/__init__.py", publisher.PUBLISHER_SOURCE_PATHS)

    def test_publish_plan_rejects_any_non_wa_ir_age_recipient(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-recipient-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, _plan = self.write_plan(root)
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            payload["destination_age_recipient_key_id"] = "age-recipient-sha256:" + "f" * 64
            plan_path.write_text(json.dumps(payload), encoding="utf-8")
            plan_path.chmod(0o600)
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "fixed WA-IR age recipient"):
                publisher.load_publish_plan(plan_path)

    def test_preflight_rejects_a_skip_worktree_bootstrap_source_substitution(self) -> None:
        """A clean-looking Git status must not falsify executable provenance."""

        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-source-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            repository = root / "repository"
            source = repository / "scripts" / "trusted.py"
            source.parent.mkdir(parents=True)
            source.write_text("committed source\n", encoding="utf-8")
            source.chmod(0o600)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repository), "config", "user.name", "Emergency Test"], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "scripts/trusted.py"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "update-index", "--skip-worktree", "scripts/trusted.py"],
                check=True,
            )
            source.write_text("substituted source\n", encoding="utf-8")
            source.chmod(0o600)
            status = subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain=v1", "--", "scripts/trusted.py"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(status.stdout, "")
            with (
                patch.object(publisher, "REPO_ROOT", repository),
                patch.object(publisher, "PUBLISHER_SOURCE_PATHS", ("scripts/trusted.py",)),
            ):
                with self.assertRaisesRegex(publisher.EmergencyPublisherError, "differs from its fixed revision"):
                    publisher._fixed_publisher_source_revision()

    def test_preflight_uses_one_captured_revision_for_every_head_blob(self) -> None:
        revision = "a" * 40
        head_blob = Mock(return_value=b"trusted source\n")
        with (
            patch.object(publisher, "PUBLISHER_SOURCE_PATHS", ("scripts/trusted.py",)),
            patch.object(
                publisher,
                "_run_publisher_git",
                side_effect=(
                    f"{REPO_ROOT}\n",
                    "scripts/trusted.py\n",
                    "",
                    revision + "\n",
                ),
            ),
            patch.object(publisher, "_publisher_head_blob", head_blob),
            patch.object(publisher, "_read_publisher_worktree_blob", return_value=b"trusted source\n"),
        ):
            self.assertEqual(publisher._fixed_publisher_source_revision(), revision)
        head_blob.assert_called_once_with(revision=revision, relative="scripts/trusted.py")

    def test_preimport_guard_rejects_an_untracked_scripts_initializer(self) -> None:
        """A package initializer would execute before a later ``from scripts`` import."""

        with tempfile.TemporaryDirectory(prefix="emergency-ir-import-surface-") as raw:
            root = Path(raw)
            scripts = root / "scripts"
            scripts.mkdir(mode=0o700)
            initializer = scripts / "__init__.py"
            initializer.write_text("raise RuntimeError('must never import')\n", encoding="utf-8")
            initializer.chmod(0o600)
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "package initializer"):
                publisher._assert_preimport_scripts_surface(repo_root=root)

    def test_source_revision_rejects_any_checkout_change_before_returning_identity(self) -> None:
        """The full status call must not be narrowed to bootstrap file pathspecs."""

        calls: list[tuple[str, ...]] = []

        def fake_git(*arguments: str) -> str:
            calls.append(arguments)
            if arguments == ("rev-parse", "--show-toplevel"):
                return f"{REPO_ROOT}\n"
            if arguments[:2] == ("ls-files", "--error-unmatch"):
                return "\n".join(publisher.PUBLISHER_SOURCE_PATHS) + "\n"
            if arguments == ("status", "--porcelain=v1", "--untracked-files=all", "--"):
                return "?? scripts/__init__.py\n"
            self.fail(f"unexpected Git invocation: {arguments!r}")

        with patch.object(publisher, "_run_publisher_git", side_effect=fake_git):
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "checkout is not clean"):
                publisher._fixed_publisher_source_revision()
        self.assertIn(("status", "--porcelain=v1", "--untracked-files=all", "--"), calls)

    def test_git_boundary_scrubs_ambient_state_and_pins_the_worktree(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch.dict(os.environ, {"GIT_DIR": "/tmp/attacker", "GIT_WORK_TREE": "/tmp/attacker"}, clear=False),
            patch.object(publisher.subprocess, "run", return_value=completed) as run,
        ):
            publisher._run_publisher_git("status", "--porcelain=v1", "--")
        arguments = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("GIT_WORK_TREE", environment)
        self.assertIn("core.fsmonitor=false", arguments)
        self.assertIn(f"core.worktree={publisher.REPO_ROOT}", arguments)

    def test_publish_rejects_a_different_provenance_signer_before_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            provenance = self.bootstrap_provenance(public)
            mismatched = dataclasses.replace(
                provenance, signer_key_id="ed25519-sha256:" + "f" * 64
            )
            client = FakeS3()
            with patch.object(publisher, "_bootstrap_provenance", return_value=provenance):
                with self.assertRaisesRegex(publisher.EmergencyPublisherError, "signer"):
                    publisher.publish(
                        client=client,
                        plan=plan,
                        signing_private_key_path=private,
                        signing_public_key_path=public,
                        bootstrap_provenance=mismatched,
                        outputs=self.outputs(root),
                        ttl_seconds=300,
                    )
            self.assertEqual(client.put_calls, [])

    def test_apply_rejects_wrong_confirmation_before_client_or_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, _plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            factory = Mock(side_effect=AssertionError("client must remain unused"))
            with patch.object(
                publisher,
                "_bootstrap_provenance",
                return_value=self.bootstrap_provenance(public),
            ):
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

    def test_same_confirmation_rejects_any_substituted_bootstrap_provenance_fact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            original = self.bootstrap_provenance(public)
            confirmation = publisher.confirmation_phrase(
                plan,
                bootstrap_provenance=original,
                ttl_seconds=300,
            )
            for label, replacement in (
                ("publisher revision", {"publisher_source_revision": "c" * 40}),
                ("receiver bundle", {"receiver_bundle_sha256": "f" * 64}),
                ("signer key", {"signer_key_id": "ed25519-sha256:" + "f" * 64}),
            ):
                with self.subTest(field=label):
                    substituted = dataclasses.replace(original, **replacement)
                    self.assertNotEqual(
                        publisher.confirmation_phrase(
                            plan,
                            bootstrap_provenance=substituted,
                            ttl_seconds=300,
                        ),
                        confirmation,
                    )
                    factory = Mock(side_effect=AssertionError("provenance mismatch must precede client construction"))
                    with patch.object(publisher, "_bootstrap_provenance", return_value=substituted):
                        with self.assertRaisesRegex(publisher.EmergencyPublisherError, "confirmation"):
                            publisher.execute(
                                self.arguments(
                                    plan=plan_path,
                                    private=private,
                                    public=public,
                                    outputs=outputs,
                                    apply=True,
                                    confirm=confirmation,
                                    credentials=root / "credentials.json",
                                ),
                                client_factory=factory,
                            )
                    factory.assert_not_called()
                    self.assertFalse(any(path.exists() for path in dataclasses.astuple(outputs)))

    def test_publisher_cli_rejects_a_caller_selected_repository(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                publisher.parse_args(
                    [
                        "--plan", "/tmp/plan.json",
                        "--signing-private-key", "/tmp/private.key",
                        "--signing-public-key", "/tmp/public.key",
                        "--receiver-bundle-output", "/tmp/receiver.tar.gz",
                        "--sealed-manifest-output", "/tmp/manifest.json",
                        "--url-map-output", "/tmp/urls.json",
                        "--descriptor-output", "/tmp/descriptor.json",
                        "--repo", "/tmp/substituted-checkout",
                    ]
                )
        self.assertIn("--repo", stderr.getvalue())

    def test_mocked_publish_binds_versions_and_creates_only_private_bootstrap_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            outputs = self.outputs(root)
            client = FakeS3()
            provenance = self.bootstrap_provenance(public)
            confirmation = publisher.confirmation_phrase(
                plan,
                bootstrap_provenance=provenance,
                ttl_seconds=300,
            )
            with (
                patch.object(publisher, "_bootstrap_provenance", return_value=provenance),
                patch.object(
                    publisher,
                    "_fixed_publisher_source_revision",
                    return_value=provenance.publisher_source_revision,
                ),
            ):
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
            self.assertEqual(len(client.object_acl_calls), 7)
            self.assertEqual(len(client.objects), 7)
            self.assertTrue(
                all(
                    values.get("ACL") == "private" and values.get("IfNoneMatch") == "*"
                    for values in client.put_kwargs
                )
            )
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
                {
                    "schema",
                    "campaign_id",
                    "expires_in_seconds",
                    "bootstrap_provenance",
                    "receiver_bundle",
                    "manifest",
                    "url_map",
                },
            )
            self.assertEqual(
                descriptor["bootstrap_provenance"],
                provenance.as_manifest(),
            )
            self.assertEqual(
                receive_plan["bootstrap_provenance"], descriptor["bootstrap_provenance"]
            )

    def test_bucket_must_be_private_and_versioned_before_any_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            provenance = self.bootstrap_provenance(public)
            for client in (FakeS3(versioning=False), FakeS3(private=False)):
                with self.subTest(versioning=client.versioning, private=client.private):
                    output_root = root / f"output-{client.versioning}-{client.private}"
                    output_root.mkdir(mode=0o700)
                    with patch.object(
                        publisher, "_bootstrap_provenance", return_value=provenance
                    ):
                        with self.assertRaisesRegex(publisher.EmergencyPublisherError, "bucket"):
                            publisher.publish(
                                client=client,
                                plan=plan,
                                signing_private_key_path=private,
                                signing_public_key_path=public,
                                bootstrap_provenance=provenance,
                                outputs=self.outputs(output_root),
                                ttl_seconds=300,
                            )
                    self.assertEqual(client.put_calls, [])

    def test_arvan_missing_public_access_block_uses_remaining_strict_controls(self) -> None:
        """Arvan's exact capability-absence response is safe only with all fallbacks."""

        client = FakeS3(
            public_access_block_error_code="NoSuchPublicAccessBlockConfiguration"
        )
        owner_id = publisher._require_private_versioned_bucket(
            client, bucket="dedicated-emergency-bucket"
        )
        self.assertEqual(owner_id, FakeS3.OWNER_ID)
        self.assertEqual(client.public_access_block_calls, 1)
        self.assertEqual(client.bucket_policy_calls, 1)
        self.assertEqual(client.bucket_acl_calls, 1)

    def test_other_public_access_block_errors_fail_closed(self) -> None:
        for error_code in (
            "AccessDenied",
            "NoSuchPublicAccessBlockConfiguration ",
        ):
            with self.subTest(error_code=error_code):
                client = FakeS3(public_access_block_error_code=error_code)
                with self.assertRaisesRegex(
                    publisher.EmergencyPublisherError, "privacy/versioning"
                ):
                    publisher._require_private_versioned_bucket(
                        client, bucket="dedicated-emergency-bucket"
                    )
                self.assertEqual(client.bucket_policy_calls, 0)
                self.assertEqual(client.bucket_acl_calls, 0)

    def test_missing_public_access_block_does_not_bypass_policy_or_acl_checks(self) -> None:
        cases = (
            (
                "bucket-policy",
                FakeS3(
                    public_access_block_error_code="NoSuchPublicAccessBlockConfiguration",
                    has_bucket_policy=True,
                ),
                "bucket policy",
                0,
            ),
            (
                "foreign-owner-acl",
                FakeS3(
                    public_access_block_error_code="NoSuchPublicAccessBlockConfiguration",
                    foreign_bucket_grant=True,
                ),
                "ACL",
                1,
            ),
            (
                "public-acl",
                FakeS3(
                    public_access_block_error_code="NoSuchPublicAccessBlockConfiguration",
                    private=False,
                ),
                "ACL",
                1,
            ),
        )
        for label, client, error, expected_acl_calls in cases:
            with self.subTest(control=label):
                with self.assertRaisesRegex(publisher.EmergencyPublisherError, error):
                    publisher._require_private_versioned_bucket(
                        client, bucket="dedicated-emergency-bucket"
                    )
                self.assertEqual(client.bucket_policy_calls, 1)
                self.assertEqual(client.bucket_acl_calls, expected_acl_calls)

    def test_bucket_rejects_nonpublic_foreign_acl_grant_before_any_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            client = FakeS3(foreign_bucket_grant=True)
            provenance = publisher._bootstrap_provenance(signing_public_key_path=public)
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "ACL"):
                publisher.publish(
                    client=client,
                    plan=plan,
                    signing_private_key_path=private,
                    signing_public_key_path=public,
                    bootstrap_provenance=provenance,
                    outputs=self.outputs(root),
                    ttl_seconds=300,
                )
            self.assertEqual(client.put_calls, [])

    def test_object_acl_must_remain_owner_only_after_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            client = FakeS3(foreign_object_grant=True)
            provenance = publisher._bootstrap_provenance(signing_public_key_path=public)
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "ACL"):
                publisher.publish(
                    client=client,
                    plan=plan,
                    signing_private_key_path=private,
                    signing_public_key_path=public,
                    bootstrap_provenance=provenance,
                    outputs=self.outputs(root),
                    ttl_seconds=300,
                )
            self.assertEqual(len(client.put_calls), 1)

    def test_existing_object_version_blocks_campaign_before_any_upload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            _plan_path, plan = self.write_plan(root)
            private, public = self.write_keypair(root)
            client = FakeS3()
            existing_key = publisher._control_key(plan, "receiver_bundle")
            client.objects[(plan.bucket, existing_key)] = [("old-version", b"prior")]
            provenance = publisher._bootstrap_provenance(signing_public_key_path=public)
            with self.assertRaisesRegex(publisher.EmergencyPublisherError, "existing Emergency campaign object version"):
                publisher.publish(
                    client=client,
                    plan=plan,
                    signing_private_key_path=private,
                    signing_public_key_path=public,
                    bootstrap_provenance=provenance,
                    outputs=self.outputs(root),
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
            provenance = self.bootstrap_provenance(public)
            with (
                patch.object(publisher, "_bootstrap_provenance", return_value=provenance),
                patch.object(
                    publisher,
                    "_fixed_publisher_source_revision",
                    return_value=provenance.publisher_source_revision,
                ),
            ):
                with self.assertRaisesRegex(publisher.EmergencyPublisherError, "immutable GET|readback"):
                    publisher.publish(
                        client=FakeS3(corrupt_get=True),
                        plan=plan,
                        signing_private_key_path=private,
                        signing_public_key_path=public,
                        bootstrap_provenance=provenance,
                        outputs=outputs,
                        ttl_seconds=300,
                    )
            self.assertTrue(outputs.receiver_bundle.exists())
            self.assertFalse(outputs.sealed_manifest.exists())

    def test_publisher_cli_requires_the_isolated_direct_script_contract(self) -> None:
        plain = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "publish_emergency_ir_object_storage.py"),
                "--help",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(plain.returncode, 2)
        self.assertIn("-I -B", plain.stderr)

        isolated = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(REPO_ROOT / "scripts" / "publish_emergency_ir_object_storage.py"),
                "--help",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        self.assertIn("--apply", isolated.stdout)
        self.assertNotIn("--repo", isolated.stdout)

    def test_publisher_ignores_an_ambient_scripts_regular_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-ambient-scripts-") as raw:
            root = Path(raw)
            ambient = root / "ambient" / "scripts"
            ambient.mkdir(parents=True)
            (ambient / "__init__.py").write_text(
                "raise RuntimeError('ambient scripts initializer executed')\n", encoding="utf-8"
            )
            code = (
                "import runpy, sys; "
                f"sys.path.append({str(ambient.parent)!r}); "
                f"runpy.run_path({str(REPO_ROOT / 'scripts/publish_emergency_ir_object_storage.py')!r}, run_name='__main__')"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", code, "--help"],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--apply", result.stdout)

    def test_client_rejects_proxy_environment_before_reading_credentials(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:8080"}, clear=True):
                with self.assertRaisesRegex(publisher.EmergencyPublisherError, "proxy environment"):
                    publisher.make_s3_client(root / "credentials.json")

    def test_client_rejects_ca_bundle_override_before_reading_credentials(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": "/tmp/untrusted-ca.pem"}, clear=True):
                with self.assertRaisesRegex(publisher.EmergencyPublisherError, "CA override"):
                    publisher.make_s3_client(root / "credentials.json")

    def test_client_explicitly_disables_botocore_proxy_inheritance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-publisher-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            credentials = root / "credentials.json"
            credentials.write_text(
                json.dumps({"access_key_id": "test-access", "secret_access_key": "test-secret"}),
                encoding="utf-8",
            )
            credentials.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True), patch("boto3.session.Session") as session_class:
                publisher.make_s3_client(credentials)

            kwargs = session_class.return_value.client.call_args.kwargs
            self.assertEqual(kwargs["endpoint_url"], manifest.APPROVED_ARVAN_ENDPOINT)
            self.assertIs(kwargs["verify"], True)
            self.assertEqual(kwargs["config"].proxies, {})


if __name__ == "__main__":
    unittest.main()
