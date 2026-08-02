from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "manage_webapp_fi_emergency_source_delivery.py"
ANCHOR_SOURCE = ROOT / "scripts" / "run_webapp_fi_emergency_source_receive.py"
INSTALLER_SOURCE = ROOT / "scripts" / "install_webapp_fi_emergency_source_anchor.py"
ANCHOR_RELATIVE_NAME = "run_webapp_fi_emergency_source_receive.py"
INSTALLER_RELATIVE_NAME = "install_webapp_fi_emergency_source_anchor.py"
ARVAN_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
ARVAN_REGION = "ir-thr-at1"
SPEC = importlib.util.spec_from_file_location("webapp_fi_emergency_source_delivery_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
DELIVERY = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = DELIVERY
SPEC.loader.exec_module(DELIVERY)

ANCHOR_SPEC = importlib.util.spec_from_file_location("webapp_fi_emergency_source_anchor_test", ANCHOR_SOURCE)
assert ANCHOR_SPEC is not None and ANCHOR_SPEC.loader is not None
ANCHOR = importlib.util.module_from_spec(ANCHOR_SPEC)
sys.modules[ANCHOR_SPEC.name] = ANCHOR
ANCHOR_SPEC.loader.exec_module(ANCHOR)

INSTALLER_SPEC = importlib.util.spec_from_file_location("webapp_fi_emergency_source_installer_test", INSTALLER_SOURCE)
assert INSTALLER_SPEC is not None and INSTALLER_SPEC.loader is not None
INSTALLER = importlib.util.module_from_spec(INSTALLER_SPEC)
sys.modules[INSTALLER_SPEC.name] = INSTALLER
INSTALLER_SPEC.loader.exec_module(INSTALLER)


def run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", *arguments], cwd=repository, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return completed.stdout.strip()


class FakeS3Error(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = io.BytesIO(payload)
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self._payload.read(amount)

    def close(self) -> None:
        self.closed = True


class FakePrivateVersionedS3:
    """Strict enough fake to exercise the controller's S3 privacy boundary."""

    owner_id = "owner-123"

    def __init__(self) -> None:
        self.objects: dict[str, list[dict[str, object]]] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.versioning_status = "Enabled"
        self.public_block = True
        self.public_access_block_error_code: str | None = None
        self.bucket_policy = False
        self.public_bucket_acl = False
        self.public_object_acl = False
        self.history_key: str | None = None

    def _acl(self, *, public: bool = False) -> dict[str, object]:
        grants: list[dict[str, object]] = [{
            "Grantee": {"Type": "CanonicalUser", "ID": self.owner_id},
            "Permission": "FULL_CONTROL",
        }]
        if public:
            grants.append({"Grantee": {"Type": "Group", "URI": "http://acs.amazonaws.com/groups/global/AllUsers"}, "Permission": "READ"})
        return {"Owner": {"ID": self.owner_id}, "Grants": grants}

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_bucket_versioning", kwargs))
        return {"Status": self.versioning_status}

    def get_public_access_block(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_public_access_block", kwargs))
        if self.public_access_block_error_code is not None:
            raise FakeS3Error(self.public_access_block_error_code)
        return {"PublicAccessBlockConfiguration": {
            "BlockPublicAcls": self.public_block,
            "IgnorePublicAcls": self.public_block,
            "BlockPublicPolicy": self.public_block,
            "RestrictPublicBuckets": self.public_block,
        }}

    def get_bucket_policy(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_bucket_policy", kwargs))
        if self.bucket_policy:
            return {"Policy": "{}"}
        raise FakeS3Error("NoSuchBucketPolicy")

    def get_bucket_acl(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_bucket_acl", kwargs))
        return self._acl(public=self.public_bucket_acl)

    def list_object_versions(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list_object_versions", kwargs))
        key = str(kwargs["Prefix"])
        versions = [{"Key": key, "VersionId": "historic"}] if self.history_key == key else []
        return {"IsTruncated": False, "Versions": versions, "DeleteMarkers": []}

    def _lookup(self, key: str, version: object | None) -> dict[str, object]:
        objects = self.objects.get(key, [])
        if not objects:
            raise FakeS3Error("404")
        if version is None:
            return objects[-1]
        for item in objects:
            if item["VersionId"] == version:
                return item
        raise FakeS3Error("NoSuchVersion")

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("head_object", kwargs))
        item = self._lookup(str(kwargs["Key"]), kwargs.get("VersionId"))
        return {
            "ContentLength": len(item["Body"]),
            "VersionId": item["VersionId"],
            "Metadata": dict(item["Metadata"]),
        }

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put_object", kwargs))
        if kwargs.get("ACL") != "private" or kwargs.get("IfNoneMatch") != "*":
            raise AssertionError("publisher did not request a private create-only PUT")
        key = str(kwargs["Key"])
        if self.objects.get(key):
            raise FakeS3Error("PreconditionFailed")
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        version = "v-" + str(sum(len(values) for values in self.objects.values()) + 1)
        self.objects.setdefault(key, []).append({
            "VersionId": version,
            "Body": body,
            "Metadata": dict(kwargs["Metadata"]),
        })
        return {"VersionId": version}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object", kwargs))
        item = self._lookup(str(kwargs["Key"]), kwargs.get("VersionId"))
        return {
            "ContentLength": len(item["Body"]),
            "VersionId": item["VersionId"],
            "Metadata": dict(item["Metadata"]),
            "Body": FakeBody(item["Body"]),
        }

    def get_object_acl(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object_acl", kwargs))
        self._lookup(str(kwargs["Key"]), kwargs.get("VersionId"))
        return self._acl(public=self.public_object_acl)

    def generate_presigned_url(self, _operation: str, *, Params: dict[str, object], ExpiresIn: int, HttpMethod: str) -> str:
        self.calls.append(("generate_presigned_url", {"Params": Params, "ExpiresIn": ExpiresIn, "HttpMethod": HttpMethod}))
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            + str(Params["Bucket"])
            + "/"
            + str(Params["Key"])
            + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x&X-Amz-Date=20260802T000000Z"
            + "&X-Amz-Expires=" + str(ExpiresIn)
            + "&X-Amz-SignedHeaders=host&X-Amz-Signature=" + "a" * 64
            + "&versionId=" + str(Params["VersionId"])
        )


class EmergencySourceDeliveryTests(unittest.TestCase):
    @staticmethod
    def controller_tool() -> DELIVERY.ControllerToolIdentity:
        payload = SOURCE.read_bytes()
        return DELIVERY.ControllerToolIdentity("a" * 40, "b" * 40, hashlib.sha256(payload).hexdigest(), len(payload))

    def make_repository(self, root: Path) -> tuple[Path, DELIVERY.SourceIdentity]:
        repository = root / "source"
        repository.mkdir(mode=0o700)
        run_git(repository, "init", "-b", "emergency")
        run_git(repository, "config", "user.email", "test@example.invalid")
        run_git(repository, "config", "user.name", "Emergency Test")
        (repository / "app.txt").write_text("base\n", encoding="utf-8")
        run_git(repository, "add", "app.txt")
        run_git(repository, "commit", "-m", "base")
        base = run_git(repository, "rev-parse", "HEAD")
        base_tree = run_git(repository, "rev-parse", "HEAD^{tree}")
        (repository / "app.txt").write_text("base\nemergency\n", encoding="utf-8")
        (repository / "script.py").write_text("print('ok')\n", encoding="utf-8")
        run_git(repository, "add", "app.txt", "script.py")
        run_git(repository, "commit", "-m", "emergency")
        patch = run_git(repository, "rev-parse", "HEAD")
        patch_tree = run_git(repository, "rev-parse", "HEAD^{tree}")
        return repository, DELIVERY.SourceIdentity(base, base_tree, patch, patch_tree)

    def write_keypair(self, root: Path) -> tuple[Path, Path, object]:
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        private_path = root / "private.key"
        public_path = root / "public.key"
        private_path.write_bytes(
            base64.b64encode(
                private.private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            + b"\n"
        )
        public_path.write_bytes(
            base64.b64encode(public.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw))
            + b"\n"
        )
        os.chmod(private_path, 0o600)
        os.chmod(public_path, 0o600)
        return private_path, public_path, private

    def make_sealed_artifacts(self, root: Path) -> tuple[DELIVERY.SourceIdentity, Path, Path, Path, str]:
        repository, identity = self.make_repository(root)
        bundle = root / "source.bundle"
        DELIVERY.build_self_contained_git_bundle(repository=repository, identity=identity, output=bundle)
        identity_file = root / "age.key"
        subprocess.run(
            ["/usr/bin/age-keygen", "-o", str(identity_file)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.chmod(identity_file, 0o600)
        recipient = subprocess.run(
            ["/usr/bin/age-keygen", "-y", str(identity_file)], check=True, text=True, stdout=subprocess.PIPE
        ).stdout.strip()
        ciphertext = root / "source.bundle.age"
        DELIVERY.encrypt_git_bundle(bundle=bundle, recipient=recipient, age_binary=Path("/usr/bin/age"), output=ciphertext)
        bootstrap = root / "bootstrap.tar.gz"
        DELIVERY.build_receiver_bootstrap(controller_tool=self.controller_tool(), output=bootstrap)
        return identity, bundle, ciphertext, bootstrap, recipient

    def test_bundle_reconstructs_full_clean_identity_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            repository, identity = self.make_repository(root)
            output = root / "source.bundle"
            digest, size = DELIVERY.build_self_contained_git_bundle(
                repository=repository, identity=identity, output=output
            )
            self.assertRegex(digest, r"^[a-f0-9]{64}$")
            self.assertGreater(size, 0)
            destination = root / "received"
            result = DELIVERY.materialize_checkout_from_bundle(bundle=output, identity=identity, destination=destination)
            self.assertEqual(result["status"], "received-local-only")
            self.assertEqual(run_git(destination, "rev-parse", "HEAD"), identity.emergency_patch_sha)
            self.assertEqual(run_git(destination, "status", "--porcelain=v1", "--untracked-files=all"), "")
            self.assertFalse((destination / ".git" / "config").read_text(encoding="utf-8").find("[remote \"origin\"]") >= 0)

    def test_signed_descriptor_rejects_tampering_and_wrong_version_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            _private_path, _public_path, private = self.write_keypair(root)
            identity = DELIVERY.SourceIdentity("a" * 40, "b" * 40, "c" * 40, "d" * 40)
            prepared = {
                "schema": DELIVERY.PREPARED_SCHEMA,
                "campaign_id": "campaign-1234",
                "source_site": "controller",
                "destination_site": "webapp_fi",
                "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                "region": "ir-thr-at1",
                "bucket": "private-bucket",
                "prefix": "private/source",
                "object_key": DELIVERY._source_object_key(
                    prefix="private/source", campaign_id="campaign-1234", identity=identity, bundle_sha256="e" * 64
                ),
                "recipient_key_id": "age-recipient-sha256:" + "f" * 64,
                "source": identity.as_descriptor(bundle_sha256="e" * 64, bundle_bytes=123),
                "controller_tool": self.controller_tool().as_descriptor(),
                "receiver_bootstrap": {
                    "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
                    "sha256": self.controller_tool().sha256,
                    "bytes": self.controller_tool().bytes,
                },
                "bootstrap": {
                    "object_key": DELIVERY._bootstrap_object_key(
                        prefix="private/source", campaign_id="campaign-1234", controller_tool=self.controller_tool()
                    ),
                    "path": str(root / "bootstrap.tar.gz"),
                    "sha256": "3" * 64,
                    "bytes": 333,
                },
                "ciphertext": {"path": str(root / "cipher.age"), "sha256": "1" * 64, "bytes": 222},
            }
            unsigned = DELIVERY._unsigned_descriptor_from_prepared(
                prepared, source_version_id="version-1", bootstrap_version_id="bootstrap-version-1"
            )
            signed = DELIVERY.sign_descriptor(unsigned, private_key=private)
            verified = DELIVERY.verify_descriptor(signed, public_key=private.public_key())
            self.assertEqual(verified.version_id, "version-1")
            tampered = dict(signed)
            tampered["version_id"] = "version-2"
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY.verify_descriptor(tampered, public_key=private.public_key())
            url = (
                "https://s3.ir-thr-at1.arvanstorage.ir/private-bucket/"
                + verified.object_key
                + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x&X-Amz-Date=20260802T000000Z"
                + "&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature="
                + "a" * 64
                + "&versionId=wrong-version"
            )
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY._validate_presigned_get(url=url, descriptor=verified)

    def test_prepare_plan_binds_recipient_ciphertext_and_source_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            repository, identity = self.make_repository(root)
            bundle = root / "source.bundle"
            DELIVERY.build_self_contained_git_bundle(repository=repository, identity=identity, output=bundle)
            identity_file = root / "age.key"
            subprocess.run(
                ["/usr/bin/age-keygen", "-o", str(identity_file)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.chmod(identity_file, 0o600)
            recipient = subprocess.run(
                ["/usr/bin/age-keygen", "-y", str(identity_file)], check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()
            ciphertext = root / "source.bundle.age"
            DELIVERY.encrypt_git_bundle(bundle=bundle, recipient=recipient, age_binary=Path("/usr/bin/age"), output=ciphertext)
            bootstrap = root / "bootstrap.tar.gz"
            DELIVERY.build_receiver_bootstrap(controller_tool=self.controller_tool(), output=bootstrap)
            plan = DELIVERY.build_prepared_plan(
                campaign_id="campaign-5678",
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                recipient=recipient,
                identity=identity,
                controller_tool=self.controller_tool(),
                bundle=bundle,
                bootstrap=bootstrap,
                ciphertext=ciphertext,
            )
            self.assertEqual(plan["recipient_key_id"], DELIVERY.recipient_key_id(recipient))
            self.assertEqual(plan["source"]["emergency_patch_sha"], identity.emergency_patch_sha)
            self.assertIn(identity.base_sha, plan["object_key"])

    def test_receive_plan_has_no_network_or_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            _private_path, _public_path, private = self.write_keypair(root)
            identity = DELIVERY.SourceIdentity("a" * 40, "b" * 40, "c" * 40, "d" * 40)
            prepared = {
                "schema": DELIVERY.PREPARED_SCHEMA,
                "campaign_id": "campaign-9012",
                "source_site": "controller",
                "destination_site": "webapp_fi",
                "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
                "region": "ir-thr-at1",
                "bucket": "private-bucket",
                "prefix": "private/source",
                "object_key": DELIVERY._source_object_key(
                    prefix="private/source", campaign_id="campaign-9012", identity=identity, bundle_sha256="e" * 64
                ),
                "recipient_key_id": "age-recipient-sha256:" + "f" * 64,
                "source": identity.as_descriptor(bundle_sha256="e" * 64, bundle_bytes=123),
                "controller_tool": self.controller_tool().as_descriptor(),
                "receiver_bootstrap": {
                    "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
                    "sha256": self.controller_tool().sha256,
                    "bytes": self.controller_tool().bytes,
                },
                "bootstrap": {
                    "object_key": DELIVERY._bootstrap_object_key(
                        prefix="private/source", campaign_id="campaign-9012", controller_tool=self.controller_tool()
                    ),
                    "path": str(root / "bootstrap.tar.gz"),
                    "sha256": "3" * 64,
                    "bytes": 333,
                },
                "ciphertext": {"path": str(root / "cipher.age"), "sha256": "1" * 64, "bytes": 222},
            }
            descriptor = DELIVERY.verify_descriptor(
                DELIVERY.sign_descriptor(
                    DELIVERY._unsigned_descriptor_from_prepared(
                        prepared, source_version_id="version-1", bootstrap_version_id="bootstrap-version-1"
                    ),
                    private_key=private,
                ),
                public_key=private.public_key(),
            )
            plan = descriptor.as_receive_plan()
            self.assertFalse(plan["network_action"])
            self.assertEqual(plan["s3_credentials"], "not-accepted-on-webapp-fi")
            self.assertNotIn("url", json.dumps(plan))


class EmergencySourceBootstrapBoundaryTests(unittest.TestCase):
    def make_control_repository(self, root: Path, *, name: str = "controller") -> Path:
        repository = root / name
        (repository / "scripts").mkdir(parents=True, mode=0o700)
        shutil.copyfile(SOURCE, repository / "scripts" / SOURCE.name)
        shutil.copyfile(ANCHOR_SOURCE, repository / "scripts" / ANCHOR_RELATIVE_NAME)
        shutil.copyfile(INSTALLER_SOURCE, repository / "scripts" / INSTALLER_RELATIVE_NAME)
        os.chmod(repository / "scripts" / SOURCE.name, 0o644)
        os.chmod(repository / "scripts" / ANCHOR_RELATIVE_NAME, 0o644)
        os.chmod(repository / "scripts" / INSTALLER_RELATIVE_NAME, 0o644)
        run_git(repository, "init", "-b", "control")
        run_git(repository, "config", "user.email", "test@example.invalid")
        run_git(repository, "config", "user.name", "Emergency Test")
        run_git(repository, "add", "scripts")
        run_git(repository, "commit", "-m", "control artifacts")
        return repository

    def _install_anchor_trust(
        self,
        root: Path,
        *,
        signer: Ed25519PrivateKey,
        anchor_approval: dict[str, object],
    ) -> tuple[Path, Path, Path]:
        trust = root / "trust"
        trust.mkdir(mode=0o700)
        installed = root / "installed-anchor.py"
        shutil.copyfile(ANCHOR_SOURCE, installed)
        os.chmod(installed, 0o600)
        approval_path = trust / "anchor-approval.json"
        approval_path.write_bytes(DELIVERY.canonical_json_bytes(anchor_approval) + b"\n")
        os.chmod(approval_path, 0o600)
        public = signer.public_key()
        raw = public.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        signer_path = trust / "signer.json"
        signer_path.write_bytes(
            ANCHOR._trust_record_payload(
                public_key=public,
                raw_public_key=raw,
                anchor_sha256=str(anchor_approval["anchor_sha256"]),
            )
        )
        os.chmod(signer_path, 0o600)
        return trust, installed, approval_path

    def _fixed_source_prepared(self, *, signer: Ed25519PrivateKey, recipient_key_id: str) -> bytes:
        identity = DELIVERY.SourceIdentity(
            DELIVERY.SOURCE_RELEASE_SHA,
            DELIVERY.SOURCE_RELEASE_TREE,
            DELIVERY.EMERGENCY_PATCH_SHA,
            DELIVERY.EMERGENCY_PATCH_TREE,
        )
        controller = self._fixed_controller_tool()
        campaign = "campaign-anchored-1234"
        prepared = {
            "schema": DELIVERY.PREPARED_SCHEMA,
            "campaign_id": campaign,
            "source_site": "controller",
            "destination_site": "webapp_fi",
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-bucket",
            "prefix": "private/source",
            "object_key": DELIVERY._source_object_key(
                prefix="private/source", campaign_id=campaign, identity=identity, bundle_sha256="d" * 64
            ),
            "recipient_key_id": recipient_key_id,
            "source": identity.as_descriptor(bundle_sha256="d" * 64, bundle_bytes=321),
            "controller_tool": controller.as_descriptor(),
            "receiver_bootstrap": {
                "schema": "gold-trade-webapp-fi-emergency-source-receiver-bootstrap-v1",
                "sha256": controller.sha256,
                "bytes": controller.bytes,
            },
            "bootstrap": {
                "object_key": DELIVERY._bootstrap_object_key(
                    prefix="private/source", campaign_id=campaign, controller_tool=controller
                ),
                "path": "/tmp/ignored-bootstrap.tar.gz",
                "sha256": "e" * 64,
                "bytes": 456,
            },
            "ciphertext": {"path": "/tmp/ignored-source.age", "sha256": "f" * 64, "bytes": 654},
        }
        signed = DELIVERY.sign_descriptor(
            DELIVERY._unsigned_descriptor_from_prepared(
                prepared, source_version_id="source-version", bootstrap_version_id="bootstrap-version"
            ),
            private_key=signer,
        )
        return DELIVERY.canonical_json_bytes(signed) + b"\n"

    @staticmethod
    def _fixed_controller_tool() -> DELIVERY.ControllerToolIdentity:
        return DELIVERY.ControllerToolIdentity("a" * 40, "b" * 40, "c" * 64, 123)

    def test_control_producer_cross_parses_anchor_and_first_installer_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            repository = self.make_control_repository(root)
            client = FakePrivateVersionedS3()
            anchor_output = root / "anchor-approval.json"
            installer_output = root / "installer-approval.json"
            installed_anchor = root / "installed-anchor.py"
            installed_installer = root / "installed-installer.py"
            shutil.copyfile(ANCHOR_SOURCE, installed_anchor)
            shutil.copyfile(INSTALLER_SOURCE, installed_installer)
            os.chmod(installed_anchor, 0o600)
            os.chmod(installed_installer, 0o600)
            with mock.patch.object(DELIVERY, "PINNED_ANCHOR_PATH", str(installed_anchor)), \
                 mock.patch.object(DELIVERY, "PINNED_INSTALLER_PATH", str(installed_installer)):
                anchor = DELIVERY.publish_control_artifact(
                    repository=repository,
                    kind="anchor",
                    endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                    region="ir-thr-at1",
                    bucket="private-bucket",
                    prefix="private/source",
                    credentials={},
                    approval_output=anchor_output,
                    client=client,
                )
                installer = DELIVERY.publish_control_artifact(
                    repository=repository,
                    kind="installer",
                    endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                    region="ir-thr-at1",
                    bucket="private-bucket",
                    prefix="private/source",
                    credentials={},
                    approval_output=installer_output,
                    client=client,
                )
                self.assertEqual(anchor, DELIVERY._validate_control_approval(anchor, kind="anchor"))
                self.assertEqual(installer, DELIVERY._validate_control_approval(installer, kind="installer"))
                controller = DELIVERY.inspect_controller_artifact(repository=repository)
                for approval in (anchor, installer):
                    self.assertEqual(approval["controller_revision"], controller.revision)
                    self.assertEqual(approval["controller_tree"], controller.tree)
                    self.assertEqual(approval["controller_tool_sha256"], controller.sha256)
                    self.assertEqual(approval["controller_tool_bytes"], controller.bytes)
                anchor_contract = DELIVERY.pinned_anchor_installation_contract(approval=anchor)
                self.assertEqual(anchor_contract["ssh_artifact_bytes"], "forbidden")
                self.assertIn("--apply --confirm", "\n".join(anchor_contract["commands"]))
            self.assertEqual(len([item for item in client.calls if item[0] == "put_object"]), 2)

            trust = root / "trust"
            trust.mkdir(mode=0o700)
            anchor_receipt = trust / "anchor-receipt.json"
            installer_receipt = trust / "installer-receipt.json"
            shutil.copyfile(anchor_output, anchor_receipt)
            shutil.copyfile(installer_output, installer_receipt)
            os.chmod(anchor_receipt, 0o600)
            os.chmod(installer_receipt, 0o600)
            signer = Ed25519PrivateKey.generate()
            signer_receipt = trust / "signer-receipt.json"
            with mock.patch.object(DELIVERY, "PINNED_ANCHOR_PATH", str(installed_anchor)):
                signer_approval = DELIVERY.build_pinned_signer_approval(
                    anchor_approval=anchor,
                    signing_public_key=signer.public_key(),
                )
                signer_contract = DELIVERY.pinned_signer_provisioning_contract(
                    anchor_approval=anchor,
                    signer_approval=signer_approval,
                    signing_public_key=signer.public_key(),
                )
                self.assertEqual(signer_contract["signer_private_key_material"], "not-transferred")
                self.assertIn("provision-pinned-signer", "\n".join(signer_contract["commands"]))
            signer_receipt.write_bytes(DELIVERY.canonical_json_bytes(signer_approval) + b"\n")
            os.chmod(signer_receipt, 0o600)
            with mock.patch.object(ANCHOR, "__file__", str(installed_anchor)), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_PATH", installed_anchor), \
                 mock.patch.object(ANCHOR, "TRUST_ROOT", trust), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_APPROVAL", anchor_receipt), \
                 mock.patch.object(ANCHOR, "PINNED_SIGNER_APPROVAL", signer_receipt):
                loaded_anchor = ANCHOR._load_anchor_approval()
                self.assertEqual(
                    ANCHOR._load_signer_approval(anchor_approval=loaded_anchor),
                    signer_approval["signer_key_id"],
                )
            with mock.patch.object(INSTALLER, "__file__", str(installed_installer)), \
                 mock.patch.object(INSTALLER, "INSTALLER_PATH", installed_installer), \
                 mock.patch.object(INSTALLER, "TRUST_ROOT", trust), \
                 mock.patch.object(INSTALLER, "INSTALLER_APPROVAL_PATH", installer_receipt), \
                 mock.patch.object(INSTALLER, "ANCHOR_PATH", installed_anchor), \
                 mock.patch.object(INSTALLER, "APPROVAL_PATH", anchor_receipt):
                loaded_installer = INSTALLER.load_installer_approval()
                loaded_pair_anchor = INSTALLER.load_approval()
            self.assertEqual(loaded_anchor["object_key"], anchor["object_key"])
            self.assertEqual(loaded_installer["object_key"], installer["object_key"])
            self.assertEqual(loaded_pair_anchor["object_key"], anchor["object_key"])

    def test_first_installer_rejects_old_8f_anchor_receipt_before_anchor_download(self) -> None:
        """An eb880 installer may not be paired with the old 8f/67f anchor."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            trust = root / "trust"
            trust.mkdir(mode=0o700)
            installed_installer = root / "installed-installer.py"
            installed_anchor = root / "installed-anchor.py"
            shutil.copyfile(INSTALLER_SOURCE, installed_installer)
            os.chmod(installed_installer, 0o600)

            frozen_installer = DELIVERY.ControllerToolIdentity(
                "eb880ae14b7e9bf025df9cd4a533d8e8d7332b85",
                "0da5e861c82f4e760aecc7c325ea92fd42fef4e4",
                hashlib.sha256(INSTALLER_SOURCE.read_bytes()).hexdigest(),
                len(INSTALLER_SOURCE.read_bytes()),
            )
            old_anchor = DELIVERY.ControllerToolIdentity(
                "8f836c918923ba414daf62503fd4a29955d0d774",
                "1ec9a9362b3effb3213dbbb8cfa84284cb7c16bd",
                "67f3d1b210e6ceadfce4e089d67e22abd87ef37bf58b3c631771a06961412b03",
                76829,
            )
            installer_approval = DELIVERY._build_installer_approval(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                artifact=frozen_installer,
                controller_tool=DELIVERY.ControllerToolIdentity(
                    frozen_installer.revision, frozen_installer.tree, "f" * 64, 42
                ),
                version_id="installer-eb880-v1",
            )
            anchor_approval = DELIVERY._build_anchor_approval(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                artifact=old_anchor,
                controller_tool=DELIVERY.ControllerToolIdentity(
                    old_anchor.revision, old_anchor.tree, "f" * 64, 42
                ),
                version_id="anchor-8f-v1",
            )
            installer_approval["installer_path"] = str(installed_installer)
            anchor_approval["anchor_path"] = str(installed_anchor)
            installer_receipt = trust / "installer-approval.json"
            anchor_receipt = trust / "anchor-approval.json"
            installer_receipt.write_bytes(DELIVERY.canonical_json_bytes(installer_approval) + b"\n")
            anchor_receipt.write_bytes(DELIVERY.canonical_json_bytes(anchor_approval) + b"\n")
            os.chmod(installer_receipt, 0o600)
            os.chmod(anchor_receipt, 0o600)

            with mock.patch.object(INSTALLER, "__file__", str(installed_installer)), \
                 mock.patch.object(INSTALLER, "INSTALLER_PATH", installed_installer), \
                 mock.patch.object(INSTALLER, "ANCHOR_PATH", installed_anchor), \
                 mock.patch.object(INSTALLER, "TRUST_ROOT", trust), \
                 mock.patch.object(INSTALLER, "INSTALLER_APPROVAL_PATH", installer_receipt), \
                 mock.patch.object(INSTALLER, "APPROVAL_PATH", anchor_receipt):
                with self.assertRaisesRegex(INSTALLER.AnchorInstallerError, "control context does not match"):
                    INSTALLER.load_approval()

    def test_first_installer_requires_every_shared_immutable_control_context_field(self) -> None:
        installer_approval = {
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-bucket",
            "prefix": "private/source",
            "controller_revision": "a" * 40,
            "controller_tree": "b" * 40,
            "controller_tool_sha256": "c" * 64,
            "controller_tool_bytes": 123,
        }
        anchor_approval = dict(installer_approval)
        INSTALLER._require_matching_control_context(
            installer_approval=installer_approval,
            anchor_approval=anchor_approval,
        )
        replacements = {
            "endpoint": "https://s3.ir-thr-at2.arvanstorage.ir",
            "region": "ir-thr-at2",
            "bucket": "other-private-bucket",
            "prefix": "other/source",
            "controller_revision": "c" * 40,
            "controller_tree": "d" * 40,
            "controller_tool_sha256": "d" * 64,
            "controller_tool_bytes": 456,
        }
        for field, replacement in replacements.items():
            with self.subTest(field=field):
                mismatched = dict(anchor_approval)
                mismatched[field] = replacement
                with self.assertRaisesRegex(INSTALLER.AnchorInstallerError, "control context does not match"):
                    INSTALLER._require_matching_control_context(
                        installer_approval=installer_approval,
                        anchor_approval=mismatched,
                    )

    def test_first_installer_contract_is_bounded_and_never_self_provisions(self) -> None:
        artifact = DELIVERY.ControllerToolIdentity("a" * 40, "b" * 40, "c" * 64, 42)
        approval = DELIVERY._build_installer_approval(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-bucket",
            prefix="private/source",
            artifact=artifact,
            controller_tool=artifact,
            version_id="immutable-v1",
        )
        contract = DELIVERY.first_installer_placement_contract(approval=approval)
        self.assertEqual(contract["ssh_artifact_bytes"], "forbidden")
        self.assertEqual(contract["execution_before_hash_verification"], "forbidden")
        self.assertEqual(contract["installer"]["sha256"], artifact.sha256)
        commands = "\n".join(contract["commands"])
        self.assertIn("/usr/bin/ln ", commands)
        self.assertIn("/usr/bin/env -i PATH=/usr/bin:/bin", commands)
        self.assertIn("HOME=/nonexistent", commands)
        self.assertIn("XDG_CONFIG_HOME=/nonexistent", commands)
        self.assertIn("CURL_HOME=/nonexistent", commands)
        self.assertIn("/usr/bin/curl -q --noproxy '*'", commands)
        self.assertIn("--location --max-redirs 0", commands)
        self.assertIn("/usr/bin/python3 -I -B", commands)
        self.assertNotIn("\ncurl ", commands)
        self.assertNotIn("\npython3 ", commands)
        self.assertNotIn("provision-installer", INSTALLER_SOURCE.read_text(encoding="utf-8"))

    def test_persistent_campaign_lock_survives_volatile_reboot_and_rejects_descriptor_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            campaigns = root / "campaigns"
            campaigns.mkdir(mode=0o700)
            campaign = "campaign-anchored-1234"
            webapp_fi = campaigns / campaign / "webapp-fi"
            webapp_fi.mkdir(parents=True, mode=0o700)
            os.chmod(campaigns / campaign, 0o700)
            os.chmod(webapp_fi, 0o700)
            signer = Ed25519PrivateKey.generate()
            payload = self._fixed_source_prepared(
                signer=signer,
                recipient_key_id="age-recipient-sha256:" + "a" * 64,
            )
            descriptor = ANCHOR.verify_descriptor(payload, public_key=signer.public_key())
            lock_path = webapp_fi / "emergency-source-receive-lock.json"
            volatile = root / "run"
            volatile.mkdir(mode=0o700)
            with mock.patch.object(ANCHOR, "FI_CAMPAIGN_IDENTITY_ROOT", campaigns):
                first = ANCHOR._bind_persistent_campaign_descriptor(descriptor)
                self.assertEqual(first["status"], "created")
                self.assertTrue(lock_path.is_file())
                # `/run` loss is the relevant reboot boundary; the lock remains
                # durable and permits only the exact signed descriptor again.
                shutil.rmtree(volatile)
                second = ANCHOR._bind_persistent_campaign_descriptor(descriptor)
                self.assertEqual(second["status"], "reused-exact-descriptor")

                alternate = json.loads(payload)
                alternate["ciphertext"]["sha256"] = "0" * 64
                unsigned = {
                    key: alternate[key]
                    for key in alternate
                    if key not in {"signature_algorithm", "signer_key_id", "signature_base64"}
                }
                swapped = ANCHOR.verify_descriptor(
                    DELIVERY.canonical_json_bytes(DELIVERY.sign_descriptor(unsigned, private_key=signer)) + b"\n",
                    public_key=signer.public_key(),
                )
                with self.assertRaisesRegex(ANCHOR.EmergencySourceBootstrapError, "different signed descriptor"):
                    ANCHOR._bind_persistent_campaign_descriptor(swapped)

    def test_receive_binds_persistent_campaign_before_creating_volatile_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            campaigns = root / "campaigns"
            campaigns.mkdir(mode=0o700)
            campaign = "campaign-anchored-1234"
            webapp_fi = campaigns / campaign / "webapp-fi"
            webapp_fi.mkdir(parents=True, mode=0o700)
            os.chmod(campaigns / campaign, 0o700)
            os.chmod(webapp_fi, 0o700)
            destination_parent = root / "destination-parent"
            destination_parent.mkdir(mode=0o700)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            signer = Ed25519PrivateKey.generate()
            recipient = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqs6d3kl"
            payload = self._fixed_source_prepared(
                signer=signer,
                recipient_key_id="age-recipient-sha256:" + hashlib.sha256(recipient.encode("ascii")).hexdigest(),
            )
            descriptor = ANCHOR.verify_descriptor(payload, public_key=signer.public_key())
            lock_path = webapp_fi / "emergency-source-receive-lock.json"

            def presigned(key: str, version: str) -> str:
                return (
                    "https://s3.ir-thr-at1.arvanstorage.ir/private-bucket/"
                    + key
                    + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x&X-Amz-Date=20260802T000000Z"
                    + "&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature=" + "a" * 64
                    + "&versionId=" + version
                )

            url_map = json.dumps(
                {
                    "schema": ANCHOR.URL_MAP_SCHEMA,
                    "descriptor_sha256": descriptor["descriptor_sha256"],
                    "bootstrap_url": presigned(
                        str(descriptor["bootstrap"]["object_key"]), str(descriptor["bootstrap"]["version_id"])
                    ),
                    "source_url": presigned(
                        str(descriptor["source"]["object_key"]), str(descriptor["source"]["version_id"])
                    ),
                }
            ).encode("utf-8")
            original_fresh = ANCHOR._fresh_private_directory

            def fresh_after_lock(parent: Path, name: str) -> Path:
                self.assertTrue(lock_path.is_file())
                return original_fresh(parent, name)

            controller = self._fixed_controller_tool()
            with mock.patch.object(ANCHOR, "FI_CAMPAIGN_IDENTITY_ROOT", campaigns), \
                 mock.patch.object(
                     ANCHOR,
                     "_load_anchor_approval",
                     return_value={
                         "anchor_sha256": "a" * 64,
                         "controller_revision": controller.revision,
                         "controller_tree": controller.tree,
                         "controller_tool_sha256": controller.sha256,
                         "controller_tool_bytes": controller.bytes,
                     },
                 ), \
                 mock.patch.object(
                     ANCHOR,
                     "_load_pinned_signer",
                     return_value=(signer.public_key(), "ed25519-sha256:" + "b" * 64, b"cHVibGlj\n"),
                 ), \
                 mock.patch.object(ANCHOR, "_derive_age_recipient", return_value=recipient), \
                 mock.patch.object(ANCHOR, "_ensure_fixed_receiver_root", return_value=runtime), \
                 mock.patch.object(ANCHOR, "_fresh_private_directory", side_effect=fresh_after_lock), \
                 mock.patch.object(ANCHOR, "_download_bootstrap"), \
                 mock.patch.object(ANCHOR, "_extract_verified_receiver", return_value=root / "receiver.py"), \
                 mock.patch.object(ANCHOR, "_run_receiver", return_value=0):
                result = ANCHOR.receive_from_transient_url_map(
                    descriptor_payload=payload,
                    destination=destination_parent / "checkout",
                    url_map_payload=url_map,
                )
            self.assertEqual(result["campaign_lock"], "created")
            self.assertTrue(lock_path.is_file())

    def test_first_installer_rejects_malformed_receipt_and_foreign_or_wrong_version_url(self) -> None:
        approval = {
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-bucket",
            "prefix": "private/source",
            "object_key": "private/source/webapp-fi-emergency-source-anchor/v1/" + "a" * 40 + "/" + "b" * 64 + ".py",
            "artifact_version_id": "version-one",
            "anchor_sha256": "b" * 64,
            "anchor_bytes": 123,
            "controller_revision": "a" * 40,
            "controller_tree": "c" * 40,
        }
        foreign = (
            "https://evil.invalid/private-bucket/" + approval["object_key"]
            + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x&X-Amz-Date=20260802T000000Z"
            + "&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature=" + "d" * 64 + "&versionId=version-one"
        )
        wrong_version = (
            "https://s3.ir-thr-at1.arvanstorage.ir/private-bucket/" + approval["object_key"]
            + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=x&X-Amz-Date=20260802T000000Z"
            + "&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature=" + "d" * 64 + "&versionId=other"
        )
        with self.assertRaises(INSTALLER.AnchorInstallerError):
            INSTALLER._validate_url(url=foreign, approval=approval)
        with self.assertRaises(INSTALLER.AnchorInstallerError):
            INSTALLER._validate_url(url=wrong_version, approval=approval)

    def test_anchor_rejects_bad_descriptor_before_any_download_or_receiver_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            anchor_bytes = ANCHOR_SOURCE.read_bytes()
            artifact = DELIVERY.ControllerToolIdentity("a" * 40, "b" * 40, hashlib.sha256(anchor_bytes).hexdigest(), len(anchor_bytes))
            approval = DELIVERY._build_anchor_approval(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                artifact=artifact,
                controller_tool=self._fixed_controller_tool(),
                version_id="anchor-v1",
            )
            approval["anchor_path"] = str(root / "installed-anchor.py")
            signer = Ed25519PrivateKey.generate()
            trust, installed, approval_path = self._install_anchor_trust(root, signer=signer, anchor_approval=approval)
            called: list[str] = []
            with mock.patch.object(ANCHOR, "__file__", str(installed)), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_PATH", installed), \
                 mock.patch.object(ANCHOR, "TRUST_ROOT", trust), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_APPROVAL", approval_path), \
                 mock.patch.object(ANCHOR, "PINNED_SIGNER_RECORD", trust / "signer.json"), \
                 mock.patch.object(ANCHOR, "DEFAULT_RECEIVER_ROOT", root / "run"), \
                 mock.patch.object(ANCHOR, "_download_bootstrap", side_effect=lambda **_kwargs: called.append("download")), \
                 mock.patch.object(ANCHOR, "_run_receiver", side_effect=lambda **_kwargs: called.append("execute")):
                with self.assertRaises(ANCHOR.EmergencySourceBootstrapError):
                    ANCHOR.receive_from_transient_url_map(
                        descriptor_payload=b"{not-json}\n",
                        destination=root / "destination",
                        url_map_payload=b"{}",
                    )
            self.assertEqual(called, [])

    def test_anchor_fixed_source_tree_and_descriptor_bytes_are_pinned_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            anchor_bytes = ANCHOR_SOURCE.read_bytes()
            artifact = DELIVERY.ControllerToolIdentity("a" * 40, "b" * 40, hashlib.sha256(anchor_bytes).hexdigest(), len(anchor_bytes))
            approval = DELIVERY._build_anchor_approval(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                artifact=artifact,
                controller_tool=self._fixed_controller_tool(),
                version_id="anchor-v1",
            )
            approval["anchor_path"] = str(root / "installed-anchor.py")
            signer = Ed25519PrivateKey.generate()
            trust, installed, approval_path = self._install_anchor_trust(root, signer=signer, anchor_approval=approval)
            payload = self._fixed_source_prepared(
                signer=signer,
                recipient_key_id="age-recipient-sha256:" + "a" * 64,
            )
            value = json.loads(payload)
            value["source"]["emergency_patch_tree"] = "0" * 40
            unsigned = {key: value[key] for key in value if key not in {"signature_algorithm", "signer_key_id", "signature_base64"}}
            tampered = DELIVERY.sign_descriptor(unsigned, private_key=signer)
            called: list[str] = []
            with mock.patch.object(ANCHOR, "__file__", str(installed)), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_PATH", installed), \
                 mock.patch.object(ANCHOR, "TRUST_ROOT", trust), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_APPROVAL", approval_path), \
                 mock.patch.object(ANCHOR, "PINNED_SIGNER_RECORD", trust / "signer.json"), \
                 mock.patch.object(ANCHOR, "_download_bootstrap", side_effect=lambda **_kwargs: called.append("download")):
                with self.assertRaises(ANCHOR.EmergencySourceBootstrapError):
                    ANCHOR.receive_from_transient_url_map(
                        descriptor_payload=DELIVERY.canonical_json_bytes(tampered) + b"\n",
                        destination=root / "destination",
                        url_map_payload=b"{}",
                    )
            self.assertEqual(called, [])

    def test_anchor_rejects_validly_signed_foreign_controller_bootstrap_before_network_or_exec(self) -> None:
        """A pinned signer cannot expand the immutable anchor's receiver authority."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            anchor_bytes = ANCHOR_SOURCE.read_bytes()
            artifact = DELIVERY.ControllerToolIdentity(
                "a" * 40,
                "b" * 40,
                hashlib.sha256(anchor_bytes).hexdigest(),
                len(anchor_bytes),
            )
            approval = DELIVERY._build_anchor_approval(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                artifact=artifact,
                controller_tool=self._fixed_controller_tool(),
                version_id="anchor-v1",
            )
            approval["anchor_path"] = str(root / "installed-anchor.py")
            signer = Ed25519PrivateKey.generate()
            trust, installed, approval_path = self._install_anchor_trust(
                root,
                signer=signer,
                anchor_approval=approval,
            )
            foreign = DELIVERY.ControllerToolIdentity("f" * 40, "e" * 40, "d" * 64, 456)
            descriptor_value = json.loads(
                self._fixed_source_prepared(
                    signer=signer,
                    recipient_key_id="age-recipient-sha256:" + "a" * 64,
                )
            )
            descriptor_value["controller_tool"] = foreign.as_descriptor()
            descriptor_value["receiver_bootstrap"] = {
                "schema": ANCHOR.RECEIVER_BOOTSTRAP_SCHEMA,
                "sha256": foreign.sha256,
                "bytes": foreign.bytes,
            }
            descriptor_value["bootstrap"]["object_key"] = DELIVERY._bootstrap_object_key(
                prefix="private/source",
                campaign_id="campaign-anchored-1234",
                controller_tool=foreign,
            )
            unsigned = {
                key: descriptor_value[key]
                for key in descriptor_value
                if key not in {"signature_algorithm", "signer_key_id", "signature_base64"}
            }
            foreign_payload = DELIVERY.canonical_json_bytes(
                DELIVERY.sign_descriptor(unsigned, private_key=signer)
            ) + b"\n"
            verified = ANCHOR.verify_descriptor(foreign_payload, public_key=signer.public_key())
            self.assertEqual(verified["controller"]["revision"], foreign.revision)
            self.assertEqual(verified["receiver"]["sha256"], foreign.sha256)
            called: list[str] = []
            with mock.patch.object(ANCHOR, "__file__", str(installed)), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_PATH", installed), \
                 mock.patch.object(ANCHOR, "TRUST_ROOT", trust), \
                 mock.patch.object(ANCHOR, "PINNED_ANCHOR_APPROVAL", approval_path), \
                 mock.patch.object(ANCHOR, "PINNED_SIGNER_RECORD", trust / "signer.json"), \
                 mock.patch.object(ANCHOR, "DEFAULT_RECEIVER_ROOT", root / "run"), \
                 mock.patch.object(ANCHOR, "_bind_persistent_campaign_descriptor", side_effect=lambda *_args, **_kwargs: called.append("lock")), \
                 mock.patch.object(ANCHOR, "_download_bootstrap", side_effect=lambda **_kwargs: called.append("download")), \
                 mock.patch.object(ANCHOR, "_run_receiver", side_effect=lambda **_kwargs: called.append("execute")):
                with self.assertRaisesRegex(
                    ANCHOR.EmergencySourceBootstrapError,
                    "controller/receiver provenance does not match the approved anchor control context",
                ):
                    ANCHOR.receive_from_transient_url_map(
                        descriptor_payload=foreign_payload,
                        destination=root / "destination",
                        url_map_payload=b"{}",
                    )
            self.assertEqual(called, [])

    def test_filesystem_links_private_leaf_and_retry_candidates_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            checkout = root / "checkout"
            checkout.mkdir(mode=0o700)
            (checkout / ".git").mkdir(mode=0o700)
            source = checkout / "source"
            source.write_text("x", encoding="utf-8")
            os.link(source, checkout / ".git" / "hardlink")
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY._verify_checkout_filesystem(checkout=checkout)
            private_leaf = root / "leaf"
            private_leaf.mkdir(mode=0o710)
            with self.assertRaises(ANCHOR.EmergencySourceBootstrapError):
                ANCHOR._safe_directory(private_leaf, label="private leaf", private=True)
            candidate_root = root / "candidates"
            candidate_root.mkdir(mode=0o700)
            ANCHOR._fresh_private_directory(candidate_root, "campaign-1234")
            with self.assertRaisesRegex(ANCHOR.EmergencySourceBootstrapError, "new campaign ID"):
                ANCHOR._fresh_private_directory(candidate_root, "campaign-1234")

    def test_private_bucket_history_acl_and_ambient_proxy_or_ca_override_fail_closed(self) -> None:
        client = FakePrivateVersionedS3()
        self.assertEqual(
            DELIVERY._assert_private_versioned_bucket(
                client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            ),
            client.owner_id,
        )
        client.public_block = False
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_private_versioned_bucket(
                client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            )
        client.public_block = True
        client.bucket_policy = True
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_private_versioned_bucket(
                client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            )
        client.bucket_policy = False
        client.history_key = "private/source/existing"
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_key_unused(client, bucket="private-bucket", key="private/source/existing")
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "https://proxy.invalid"}, clear=False):
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY._require_direct_object_storage_environment()
        for key in ("SSL_CERT_FILE", "ssl_cert_dir"):
            with self.subTest(environment=key), mock.patch.dict(os.environ, {key: "/tmp/untrusted-ca"}, clear=False):
                with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                    DELIVERY._require_direct_object_storage_environment()
                with self.assertRaises(ANCHOR.EmergencySourceBootstrapError):
                    ANCHOR._require_direct_object_storage_environment()
                with self.assertRaises(INSTALLER.AnchorInstallerError):
                    INSTALLER._require_direct_object_storage_environment()

    def test_arvan_missing_public_access_block_capability_keeps_all_other_bucket_checks(self) -> None:
        """Only Arvan's exact missing-capability response may skip AWS PAB flags."""

        client = FakePrivateVersionedS3()
        client.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
        self.assertEqual(
            DELIVERY._assert_private_versioned_bucket(
                client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            ),
            client.owner_id,
        )
        self.assertEqual(
            [name for name, _kwargs in client.calls],
            ["get_bucket_versioning", "get_public_access_block", "get_bucket_policy", "get_bucket_acl"],
        )

        bad_versioning = FakePrivateVersionedS3()
        bad_versioning.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
        bad_versioning.versioning_status = "Suspended"
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_private_versioned_bucket(
                bad_versioning, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            )
        self.assertEqual([name for name, _kwargs in bad_versioning.calls], ["get_bucket_versioning"])

        policy_present = FakePrivateVersionedS3()
        policy_present.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
        policy_present.bucket_policy = True
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_private_versioned_bucket(
                policy_present, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            )

        public_bucket_acl = FakePrivateVersionedS3()
        public_bucket_acl.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
        public_bucket_acl.public_bucket_acl = True
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_private_versioned_bucket(
                public_bucket_acl, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
            )

    def test_public_access_block_rejects_other_errors_and_disabled_statuses(self) -> None:
        for code in (
            "404",
            "NotFound",
            "AccessDenied",
            "NoSuchBucketPolicy",
            "NoSuchPublicAccessBlockConfiguration ",
            "NoSuchPublicAccessBlockConfigurationx",
        ):
            with self.subTest(error_code=code):
                client = FakePrivateVersionedS3()
                client.public_access_block_error_code = code
                with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                    DELIVERY._assert_private_versioned_bucket(
                        client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
                    )
                self.assertEqual([name for name, _kwargs in client.calls], ["get_bucket_versioning", "get_public_access_block"])

        non_audited_arvan = FakePrivateVersionedS3()
        non_audited_arvan.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
        with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
            DELIVERY._assert_private_versioned_bucket(
                non_audited_arvan,
                endpoint="https://s3.ir-thr-at2.arvanstorage.ir",
                region="ir-thr-at2",
                bucket="private-bucket",
            )
        self.assertEqual(
            [name for name, _kwargs in non_audited_arvan.calls],
            ["get_bucket_versioning", "get_public_access_block"],
        )

        client = FakePrivateVersionedS3()
        with mock.patch.object(
            client,
            "get_public_access_block",
            return_value={
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": False,
                }
            },
        ):
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY._assert_private_versioned_bucket(
                    client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
                )

        client = FakePrivateVersionedS3()
        with mock.patch.object(client, "get_public_access_block", return_value={}):
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY._assert_private_versioned_bucket(
                    client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
                )

        client = FakePrivateVersionedS3()
        with mock.patch.object(client, "get_public_access_block", side_effect=RuntimeError("transport")):
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY._assert_private_versioned_bucket(
                    client, endpoint=ARVAN_ENDPOINT, region=ARVAN_REGION, bucket="private-bucket"
                )

    def test_tls_override_fails_before_credentials_or_anchor_transport(self) -> None:
        """An OpenSSL override must stop all three trust boundaries first."""

        for key in ("SSL_CERT_FILE", "ssl_cert_dir"):
            with self.subTest(environment=key), mock.patch.dict(os.environ, {key: "/tmp/untrusted-ca"}, clear=False):
                with mock.patch.object(DELIVERY, "_read_stable_regular", side_effect=AssertionError("credentials read")):
                    with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                        DELIVERY._load_credentials(Path("/unreachable/credentials.json"))
                with mock.patch.object(INSTALLER, "_require_root"), \
                     mock.patch.object(INSTALLER, "_validate_url", side_effect=AssertionError("URL validated")), \
                     mock.patch.object(INSTALLER, "_ensure_target_parent", side_effect=AssertionError("target touched")):
                    with self.assertRaises(INSTALLER.AnchorInstallerError):
                        INSTALLER.install_from_url(approval={}, url="https://s3.ir-thr-at1.arvanstorage.ir/ignored")
                with mock.patch.object(ANCHOR, "_load_anchor_approval", side_effect=AssertionError("trust read")), \
                     mock.patch.object(ANCHOR, "_download_bootstrap", side_effect=AssertionError("network opened")):
                    with self.assertRaises(ANCHOR.EmergencySourceBootstrapError):
                        ANCHOR.receive_from_transient_url_map(
                            descriptor_payload=b"{}",
                            destination=Path("/unreachable/destination"),
                            url_map_payload=b"{}",
                        )

    def test_source_publisher_requires_private_create_only_objects_and_rechecks_before_presign(self) -> None:
        helper = EmergencySourceDeliveryTests()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            identity, bundle, ciphertext, bootstrap, recipient = helper.make_sealed_artifacts(root)
            plan = DELIVERY.build_prepared_plan(
                campaign_id="campaign-publish-1234",
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                recipient=recipient,
                identity=identity,
                controller_tool=helper.controller_tool(),
                bundle=bundle,
                bootstrap=bootstrap,
                ciphertext=ciphertext,
            )
            private_path, _public_path, private = helper.write_keypair(root)
            descriptor_path = root / "descriptor.json"
            client = FakePrivateVersionedS3()
            client.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
            descriptor = DELIVERY.publish_prepared_plan(
                plan=plan,
                private_key=DELIVERY.load_private_key(private_path),
                credentials={},
                descriptor_output=descriptor_path,
                client=client,
            )
            self.assertEqual(len([item for item in client.calls if item[0] == "put_object"]), 2)
            self.assertEqual(len([item for item in client.calls if item[0] == "get_object_acl"]), 2)
            urls = DELIVERY.issue_version_bound_gets(descriptor=descriptor, client=client, ttl_seconds=300)
            self.assertEqual(set(urls), {"bootstrap_url", "source_url"})
            client.public_object_acl = True
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY.issue_version_bound_gets(descriptor=descriptor, client=client, ttl_seconds=300)

            public_readback = FakePrivateVersionedS3()
            public_readback.public_access_block_error_code = "NoSuchPublicAccessBlockConfiguration"
            public_readback.public_object_acl = True
            with self.assertRaises(DELIVERY.EmergencySourceDeliveryError):
                DELIVERY.publish_prepared_plan(
                    plan=plan,
                    private_key=DELIVERY.load_private_key(private_path),
                    credentials={},
                    descriptor_output=root / "public-readback-descriptor.json",
                    client=public_readback,
                )
            self.assertGreaterEqual(len([item for item in public_readback.calls if item[0] == "get_object_acl"]), 1)

    def test_fixed_delivered_source_is_exact_e1a30972_and_matches_the_anchor_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            repository, generic = EmergencySourceDeliveryTests().make_repository(root)
            self.assertEqual(
                DELIVERY.inspect_clean_checkout(
                    repository=repository,
                    base_sha=generic.base_sha,
                    emergency_patch_sha=generic.emergency_patch_sha,
                ),
                generic,
            )
        expected = DELIVERY.SourceIdentity(
            "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
            "b4da321f72b84c075bd267bda1211f0ff68b91d6",
            "e1a309725154ab6b67655ebdfe22c73d831aa72e",
            "d158aa5f520fd625537f927fb079196aa24fa302",
        )
        source = Path("/source-only-wa-ir-e1a30972")
        with mock.patch.object(DELIVERY, "inspect_clean_checkout", return_value=expected) as inspect:
            identity = DELIVERY.inspect_fixed_emergency_checkout(repository=source)
        inspect.assert_called_once_with(
            repository=source,
            base_sha=expected.base_sha,
            emergency_patch_sha=expected.emergency_patch_sha,
        )
        self.assertEqual(identity, expected)
        self.assertEqual(DELIVERY.EMERGENCY_PATCH_SHA, expected.emergency_patch_sha)
        self.assertEqual(DELIVERY.EMERGENCY_PATCH_TREE, expected.emergency_patch_tree)
        self.assertEqual(ANCHOR.EMERGENCY_PATCH_SHA, expected.emergency_patch_sha)
        self.assertEqual(ANCHOR.EMERGENCY_PATCH_TREE, expected.emergency_patch_tree)
        wrong_tree = DELIVERY.SourceIdentity(
            expected.base_sha,
            expected.base_tree,
            expected.emergency_patch_sha,
            "0" * 40,
        )
        with mock.patch.object(DELIVERY, "inspect_clean_checkout", return_value=wrong_tree), self.assertRaisesRegex(
            DELIVERY.EmergencySourceDeliveryError, "tree identities"
        ):
            DELIVERY.inspect_fixed_emergency_checkout(repository=source)

    def test_prepare_plan_binds_e1a30972_source_to_a_distinct_later_control_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            source = self.make_control_repository(root, name="source-fixture")
            control = self.make_control_repository(root, name="later-controller")
            expected = DELIVERY.SourceIdentity(
                DELIVERY.SOURCE_RELEASE_SHA,
                DELIVERY.SOURCE_RELEASE_TREE,
                DELIVERY.EMERGENCY_PATCH_SHA,
                DELIVERY.EMERGENCY_PATCH_TREE,
            )

            def fixed_source(*, repository: Path) -> DELIVERY.SourceIdentity:
                if repository == source:
                    return expected
                raise DELIVERY.EmergencySourceDeliveryError("HEAD does not match the approved patch identity")

            arguments = argparse.Namespace(
                repository=source,
                controller_repository=control,
                campaign_id="campaign-e1a30972-1234",
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-bucket",
                prefix="private/source",
                age_recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqs6d3kl",
                output_directory=root / "prepared",
            )
            control_manager = control / "scripts" / SOURCE.name
            with mock.patch.object(DELIVERY, "__file__", str(control_manager)), mock.patch.object(
                DELIVERY, "inspect_fixed_emergency_checkout", side_effect=fixed_source
            ):
                plan = DELIVERY._plan_prepare(arguments)
                self.assertEqual(plan["source"]["emergency_patch_sha"], DELIVERY.EMERGENCY_PATCH_SHA)
                self.assertEqual(plan["source"]["emergency_patch_tree"], DELIVERY.EMERGENCY_PATCH_TREE)
                self.assertEqual(plan["controller_tool"]["revision"], run_git(control, "rev-parse", "HEAD"))
                self.assertNotEqual(plan["controller_tool"]["revision"], plan["source"]["emergency_patch_sha"])
                with self.assertRaisesRegex(DELIVERY.EmergencySourceDeliveryError, "HEAD does not match"):
                    DELIVERY._plan_prepare(
                        argparse.Namespace(**{**vars(arguments), "repository": control})
                    )


if __name__ == "__main__":
    unittest.main()
