from __future__ import annotations

from io import BytesIO
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.canonical_json import canonical_json_bytes
from scripts import publish_three_site_staging_seed_campaign as campaign
from core import human_approval_issuer
from core.human_approval import approval_policy_hash
from core.human_approval_issuer import (
    authenticate_and_issue,
    create_enrollment,
    totp_code,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_IMMUTABLE_RELEASE_BINDING = campaign._immutable_release_binding


class FakeS3Error(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, list[dict[str, object]]] = {}
        self.delete_markers: dict[str, list[str]] = {}
        self.put_attempts = 0
        self.put_count = 0
        self.response_loss_at: int | None = None
        self.fail_without_commit_at: int | None = None
        self.object_acl = self.owner_acl()
        self.bucket_acl = self.owner_acl()
        self.default_sse = False
        self.object_sse = False

    @staticmethod
    def owner_acl():
        return {
            "Owner": {"ID": "owner-1", "DisplayName": "owner"},
            "Grants": [
                {
                    "Grantee": {
                        "Type": "CanonicalUser",
                        "ID": "owner-1",
                        "DisplayName": "owner",
                    },
                    "Permission": "FULL_CONTROL",
                }
            ],
        }

    def get_bucket_versioning(self, **_kwargs):
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **_kwargs):
        return self.bucket_acl

    def get_bucket_policy_status(self, **_kwargs):
        return {"PolicyStatus": {"IsPublic": False}}

    def get_bucket_encryption(self, **_kwargs):
        if self.default_sse:
            return {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                }
            }
        raise FakeS3Error("ServerSideEncryptionConfigurationNotFoundError")

    def list_object_versions(self, *, Prefix, **_kwargs):  # noqa: N803
        return {
            "Versions": [
                {"Key": Prefix, "VersionId": row["version_id"]}
                for row in self.objects.get(Prefix, [])
            ],
            "DeleteMarkers": [
                {"Key": Prefix, "VersionId": version}
                for version in self.delete_markers.get(Prefix, [])
            ],
            "IsTruncated": False,
        }

    def put_object(self, **kwargs):
        self.put_attempts += 1
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") != "*" or kwargs.get("ACL") != "private":
            raise AssertionError("PUT must be conditional and private")
        if self.objects.get(key) or self.delete_markers.get(key):
            raise FakeS3Error("PreconditionFailed")
        if self.fail_without_commit_at == self.put_attempts:
            raise FakeS3Error("ServiceUnavailable")
        body = kwargs["Body"].read()
        version_id = f"version-{self.put_count + 1}"
        self.objects.setdefault(key, []).append(
            {
                "version_id": version_id,
                "body": body,
                "metadata": dict(kwargs["Metadata"]),
                "content_type": kwargs["ContentType"],
            }
        )
        self.put_count += 1
        if self.response_loss_at == self.put_attempts:
            raise FakeS3Error("ConnectionClosed")
        return {"VersionId": version_id}

    def _one(self, key: str, version_id: str):
        rows = [
            row
            for row in self.objects.get(key, [])
            if row["version_id"] == version_id
        ]
        if len(rows) != 1:
            raise FakeS3Error("NoSuchVersion")
        return rows[0]

    def head_object(self, *, Key, VersionId, **_kwargs):  # noqa: N803
        row = self._one(Key, VersionId)
        result = {
            "VersionId": VersionId,
            "ContentLength": len(row["body"]),
            "ContentType": row["content_type"],
            "Metadata": row["metadata"],
        }
        if self.object_sse:
            result["ServerSideEncryption"] = "AES256"
        return result

    def get_object_acl(self, **_kwargs):
        return self.object_acl

    def get_object(self, *, Key, VersionId, **_kwargs):  # noqa: N803
        row = self._one(Key, VersionId)
        result = {
            "VersionId": VersionId,
            "ContentLength": len(row["body"]),
            "Metadata": row["metadata"],
            "Body": BytesIO(row["body"]),
        }
        if self.object_sse:
            result["ServerSideEncryption"] = "AES256"
        return result


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


def _sha(path: Path) -> tuple[str, int]:
    payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _tar(path: Path, name: str, payload: bytes) -> None:
    source = path.parent / f".{name}.source"
    source.write_bytes(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source, arcname=name)
    source.unlink()
    path.chmod(0o600)


class CampaignFixture:
    def __init__(self, root: Path):
        self.root = root
        root.chmod(0o700)
        self.release_sha = "1" * 40
        self.campaign_id = "11111111-2222-4333-8444-555555555555"
        self.controller_id = "a" * 32
        self.web_id = "b" * 32
        self.controller_machine = root / "controller-machine-id"
        self.web_machine = root / "web-machine-id"
        self.controller_machine.write_text(self.controller_id + "\n", encoding="ascii")
        self.web_machine.write_text(self.web_id + "\n", encoding="ascii")
        self.identity_paths = {
            "bot_fi": root / "identities/bot-fi.agekey",
            "webapp_fi": root / "identities/webapp-fi.agekey",
            "webapp_ir": root / "identities/webapp-ir.agekey",
        }
        self.source_roots = {
            "bot_fi": root / "source-bot",
            "webapp_fi": root / "source-web",
        }
        self.controller_root = root / "controller"
        self.backups: dict[str, dict[str, object]] = {}
        self.backup_paths: dict[str, Path] = {}
        for index, role in enumerate(campaign.SOURCE_ROLES, start=1):
            self._make_backup(role, index)
        self.recipients = {
            "bot_fi": "age1" + "q" * 58,
            "webapp_fi": "age1" + "w" * 58,
            "webapp_ir": "age1" + "e" * 58,
        }
        self.approval_private = Ed25519PrivateKey.generate()
        approval_public = self.approval_private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.approval_policy = {
            "schema": "three-site-human-approval-policy-v1",
            "policy_id": str(uuid4()),
            "issuer": {
                "issuer_id": "test-seed-issuer",
                "key_id": "test-key",
                "operator": "test-operator",
                "authenticator_id": str(uuid4()),
                "public_key": base64.b64encode(approval_public).decode("ascii"),
            },
            "actions": [
                {
                    "action": "approve_inventory",
                    "environments": ["staging"],
                    "max_ttl_seconds": 86400,
                },
                {
                    "action": "approve_seed_preparation",
                    "environments": ["staging"],
                    "max_ttl_seconds": 3600,
                },
                {
                    "action": "approve_seed_publication",
                    "environments": ["staging"],
                    "max_ttl_seconds": 3600,
                },
            ],
        }
        self.witness_key = base64.b64encode(b"w" * 32).decode("ascii")
        inventory = {
            "campaign_id": self.campaign_id,
            "release_sha": self.release_sha,
            "deployment_id": "three-site-campaign-generic",
            "object_storage": {
                "bucket": "staging-bucket",
                "prefix": "staging/full-matrix/campaign-generic/",
                "credential_id": "staging-seed-publisher",
                "private": True,
                "versioning": True,
            },
            "roles": [
                {"role": "bot_fi", "machine_id": self.controller_id},
                {"role": "webapp_fi", "machine_id": self.web_id},
                {"role": "webapp_ir", "machine_id": "c" * 32},
                {"role": "witness", "machine_id": "d" * 32},
            ],
        }
        inventory_approval = {"receipt": "inventory"}
        inventory_result = {
            "inventory_stage": "provisioned",
            "campaign_id": self.campaign_id,
            "release_sha": self.release_sha,
            "deployment_id": inventory["deployment_id"],
            "inventory_sha256": campaign._canonical_hash(inventory),
            "approval_policy_sha256": approval_policy_hash(self.approval_policy),
            "approval_sha256": campaign._canonical_hash(inventory_approval),
            "approval_id": "inventory-approval",
            "approval_expires_at": "2099-01-01T00:00:00+00:00",
            "witness_relay_public_key_sha256": hashlib.sha256(
                (self.witness_key + "\n").encode()
            ).hexdigest(),
        }
        release_files = {}
        for relative in campaign.SECURITY_RELEASE_FILES:
            release_files[relative] = hashlib.sha256(
                (ROOT / relative).read_bytes()
            ).hexdigest()
        immutable_release = {
            "root": str(ROOT),
            "git_head": self.release_sha,
            "git_tree": "2" * 40,
            "tracked_index_sha256": "3" * 64,
            "security_files": release_files,
        }
        self.immutable_release = immutable_release
        core = campaign.build_preparation_core(
            inventory=inventory,
            inventory_result=inventory_result,
            inventory_approval=inventory_approval,
            backups=self.backups,
            backup_paths=self.backup_paths,
            recipient_values=self.recipients,
            immutable_release=immutable_release,
            source_state_roots=self.source_roots,
            controller_state_root=self.controller_root,
            target_identity_paths=self.identity_paths,
        )
        self.preparation_core = core
        preparation_approval = self.issue_token(
            action="approve_seed_preparation",
            subject=campaign.source_preparation_subject(core),
        )
        self.source_plans = {
            role: campaign.source_preparation_plan(
                core,
                role=role,
                preparation_approval=preparation_approval,
                approval_policy=self.approval_policy,
                witness_relay_public_key=self.witness_key,
            )
            for role in campaign.SOURCE_ROLES
        }
        self.contract = None
        self.contract_sha = None
        self.publication_core = None
        self.age_calls: list[list[str]] = []

    def issue_token(
        self,
        *,
        action: str,
        subject: dict[str, object],
        now: datetime | None = None,
    ) -> dict[str, object]:
        now = now or datetime.now(timezone.utc).replace(microsecond=0)
        unsigned = {
            "schema": "three-site-human-approval-token-v1",
            "approval_id": str(uuid4()),
            "policy_id": self.approval_policy["policy_id"],
            "policy_hash": approval_policy_hash(self.approval_policy),
            "issuer_id": self.approval_policy["issuer"]["issuer_id"],
            "key_id": self.approval_policy["issuer"]["key_id"],
            "operator": self.approval_policy["issuer"]["operator"],
            "authenticator_id": self.approval_policy["issuer"]["authenticator_id"],
            "action": action,
            "environment": "staging",
            "subject": subject,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=3600)).isoformat(),
            "authentication": {"methods": ["password", "totp"]},
        }
        return {
            **unsigned,
            "signature": base64.b64encode(
                self.approval_private.sign(canonical_json_bytes(unsigned))
            ).decode("ascii"),
        }

    def seal_publication(
        self,
        core: dict[str, object],
        publication_core: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now = now or datetime.now(timezone.utc).replace(microsecond=0)
        subject = campaign.publication_contract_subject(
            publication_core,
            preparation_core=core,
        )
        token = self.issue_token(
            action="approve_seed_publication",
            subject=subject,
            now=now,
        )
        contract = {
            "schema": campaign.CONTRACT_SCHEMA,
            "core": core,
            "core_sha256": campaign._canonical_hash(core),
            "publication_core": publication_core,
            "publication_core_sha256": campaign._canonical_hash(publication_core),
            "approval_policy": self.approval_policy,
            "witness_relay_public_key": self.witness_key,
            "publication_approval_token": token,
            "publication_approval": {
                "sha256": campaign._canonical_hash(token),
                "approval_id": token["approval_id"],
                "expires_at": token["expires_at"],
                "action": "approve_seed_publication",
            },
        }
        campaign._validate_contract(contract, now=now)
        return contract

    def _make_backup(self, role: str, index: int) -> None:
        backup_root = self.root / f"backup-{role}"
        backup_root.mkdir(mode=0o700)
        postgres = backup_root / "postgres.custom"
        uploads = backup_root / "uploads.tar.gz"
        audit = backup_root / "audit.tar.gz"
        _write_private(postgres, (f"postgres-{role}-" * 8).encode())
        _tar(uploads, "upload.txt", (f"upload-{role}-" * 8).encode())
        _tar(audit, "audit.jsonl", (f"audit-{role}-" * 8).encode())
        postgres_hash, postgres_bytes = _sha(postgres)
        uploads_hash, uploads_bytes = _sha(uploads)
        audit_hash, audit_bytes = _sha(audit)
        manifest = {
            "schema": "three-site-staging-source-backup-v2",
            "campaign_id": self.campaign_id,
            "source_role": role,
            "source_release_sha": "5" * 40,
            "target_release_sha": self.release_sha,
            "created_at": "2026-07-27T08:00:00+00:00",
            "source_postgres_system_id": f"{index}" * 19,
            "source_alembic_revision": "abcdef123456",
            "artifacts": {
                "postgres": {
                    "path": str(postgres),
                    "bytes": postgres_bytes,
                    "sha256": postgres_hash,
                },
                "uploads": {
                    "path": str(uploads),
                    "bytes": uploads_bytes,
                    "sha256": uploads_hash,
                    "safe_member_count": 1,
                },
                "audit": {
                    "path": str(audit),
                    "bytes": audit_bytes,
                    "sha256": audit_hash,
                    "safe_member_count": 1,
                },
            },
            "restore_drill": {
                "status": "passed",
                "restored_alembic_revision": "abcdef123456",
                "scratch_postgres_system_id": f"{index + 2}" * 19,
                "database_fingerprint_sha256": f"{index}" * 64,
                "database_row_count": 12,
                "public_table_count": 3,
            },
            "source_freeze_evidence_sha256": "6" * 64,
            "redis_observation": {
                "dbsize": 2,
                "appendonly": True,
                "lastsave_unix": 1700000000,
                "restore": False,
            },
            "redis_restore": False,
            "application_mutation": False,
        }
        manifest_path = backup_root / "manifest.json"
        _write_private(
            manifest_path,
            (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(),
        )
        self.backups[role] = manifest
        self.backup_paths[role] = manifest_path

    def fake_age(self, arguments: list[str]) -> None:
        self.age_calls.append(list(arguments))
        output = Path(arguments[arguments.index("--output") + 1])
        source = Path(arguments[-1])
        _write_private(output, b"age-v1-encrypted-header:" + source.read_bytes())

    def install_identity(self, role: str) -> None:
        _write_private(
            self.identity_paths[role],
            b"# fixture identity\nAGE-SECRET-KEY-TEST123\n",
        )

    def fake_derive_recipient(self, identity_path: Path) -> str:
        matching = [
            role
            for role, expected_path in self.identity_paths.items()
            if identity_path == expected_path
        ]
        if len(matching) != 1:
            raise AssertionError("unexpected identity path")
        return self.recipients[matching[0]]

    def prepare_and_ingest_core(
        self,
        core: dict[str, object],
    ) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
        preparations = {}
        preparation_approval = self.issue_token(
            action="approve_seed_preparation",
            subject=campaign.source_preparation_subject(core),
        )
        plans = {
            role: campaign.source_preparation_plan(
                core,
                role=role,
                preparation_approval=preparation_approval,
                approval_policy=self.approval_policy,
                witness_relay_public_key=self.witness_key,
            )
            for role in campaign.SOURCE_ROLES
        }
        for role, machine in (
            ("bot_fi", self.controller_machine),
            ("webapp_fi", self.web_machine),
        ):
            self.install_identity(role)
            try:
                preparation = campaign.prepare_role(
                    source_plan=plans[role],
                    role=role,
                    confirmation=campaign.source_confirmation_phrase(plans[role]),
                    machine_id_path=machine,
                    encrypt=self.fake_age,
                    derive_recipient=self.fake_derive_recipient,
                )
            finally:
                self.identity_paths[role].unlink(missing_ok=True)
            preparations[role] = preparation
            campaign.ingest_role(
                preparation_core=core,
                role=role,
                preparation_path=Path(core["source_state_roots"][role])
                / "role-preparation.json",
                ciphertext_root=Path(core["source_state_roots"][role]),
                machine_id_path=self.controller_machine,
            )
        publication_core = campaign.build_publication_core(
            preparation_core=core,
            preparations=preparations,
        )
        contract = self.seal_publication(core, publication_core)
        return preparations, contract

    def prepare_and_ingest(self) -> dict[str, dict[str, object]]:
        preparations, contract = self.prepare_and_ingest_core(
            self.preparation_core
        )
        self.publication_core = contract["publication_core"]
        self.contract = contract
        self.contract_sha = campaign._canonical_hash(contract)
        return preparations


class CampaignSeedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CampaignFixture(Path(self.temporary.name))
        self.release_patch = patch.object(
            campaign,
            "_immutable_release_binding",
            return_value=self.fixture.immutable_release,
        )
        self.release_patch.start()

    def tearDown(self):
        self.release_patch.stop()
        self.temporary.cleanup()

    def test_end_to_end_exact_six_with_distinct_target_recipient_map(self):
        preparations = self.fixture.prepare_and_ingest()
        self.assertEqual(
            preparations["bot_fi"]["recipient_fingerprints"],
            {
                "bot_fi": campaign._recipient_fingerprint(
                    self.fixture.recipients["bot_fi"]
                )
            },
        )
        self.assertEqual(
            set(preparations["webapp_fi"]["recipient_fingerprints"]),
            {"webapp_fi", "webapp_ir"},
        )
        self.assertEqual(self.fixture.age_calls[0].count("--recipient"), 1)
        self.assertEqual(self.fixture.age_calls[3].count("--recipient"), 2)

        client = FakeS3()
        published = campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        self.assertEqual(client.put_count, 6)
        self.assertEqual(len(client.objects), 6)
        self.assertFalse(
            published["controller_publication_identity_input_accepted"]
        )
        self.assertFalse(published["controller_decryption_performed"])
        result = campaign.finalize_manifests(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            publication=published,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        self.assertFalse(result["controller_decryption"])
        web_manifest = json.loads(
            Path(result["manifests"]["webapp_fi"]["manifest"]).read_text()
        )
        self.assertEqual(
            set(web_manifest["recipient_fingerprints"]),
            {"webapp_fi", "webapp_ir"},
        )
        self.assertEqual(web_manifest["schema"], "three-site-staging-seed-manifest-v2")
        self.assertTrue(
            all("publication_intent" in row for row in web_manifest["objects"])
        )
        started = json.loads(
            (self.fixture.controller_root / "readback-started.json").read_text()
        )
        readback = json.loads(
            Path(result["manifests"]["webapp_fi"]["readback"]).read_text()
        )
        self.assertEqual(readback["verified_at"], started["started_at"])
        self.assertEqual(
            readback["plaintext_end_to_end_verification"],
            "deferred-to-target-fetch",
        )

    def test_preexisting_sixth_key_blocks_before_any_put(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        key = self.fixture.contract["core"]["objects"][-1]["object_key"]
        client.objects[key] = [
            {
                "version_id": "foreign",
                "body": b"foreign",
                "metadata": {},
                "content_type": "application/octet-stream",
            }
        ]
        with self.assertRaisesRegex(campaign.CampaignSeedError, "existed"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        self.assertEqual(client.put_attempts, 0)
        self.assertFalse(
            (self.fixture.controller_root / "global-remote-absence.json").exists()
        )

    def test_committed_response_loss_is_reconciled_without_duplicate_put(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        client.response_loss_at = 1
        result = campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        self.assertEqual(result["object_count"], 6)
        self.assertEqual(client.put_attempts, 6)
        self.assertTrue(all(len(rows) == 1 for rows in client.objects.values()))

    def test_crash_after_put_before_receipt_resumes_from_durable_intent(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        calls = 0
        issued = datetime.fromisoformat(
            self.fixture.contract["publication_approval_token"]["issued_at"]
        )

        def crash(_row, _version):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise SystemExit("simulated SIGKILL boundary")

        with self.assertRaises(SystemExit):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
                after_put=crash,
                now=issued + timedelta(seconds=1),
            )
        self.assertEqual(client.put_count, 1)
        result = campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
            now=issued + timedelta(hours=2),
        )
        self.assertEqual(result["object_count"], 6)
        self.assertEqual(client.put_count, 6)
        self.assertEqual(client.put_attempts, 6)

    def test_expired_approval_before_first_put_creates_no_remote_object(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        expires = datetime.fromisoformat(
            self.fixture.contract["publication_approval_token"]["expires_at"]
        )
        with self.assertRaises(campaign.CampaignSeedError):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
                now=expires + timedelta(seconds=1),
            )
        self.assertEqual(client.put_attempts, 0)
        self.assertEqual(client.objects, {})
        self.assertFalse(
            (self.fixture.controller_root / "global-remote-absence.json").exists()
        )

    def test_fresh_reapproval_can_resume_a_pre_put_baseline(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        issued = datetime.fromisoformat(
            self.fixture.contract["publication_approval_token"]["issued_at"]
        )
        campaign.establish_global_readiness(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
            now=issued + timedelta(seconds=1),
        )
        replacement_issued = issued + timedelta(hours=2)
        replacement = self.fixture.seal_publication(
            self.fixture.preparation_core,
            self.fixture.publication_core,
            now=replacement_issued,
        )
        replacement_hash = campaign._canonical_hash(replacement)
        result = campaign.publish_six(
            contract=replacement,
            contract_sha256=replacement_hash,
            client=client,
            machine_id_path=self.fixture.controller_machine,
            now=replacement_issued + timedelta(seconds=1),
        )
        self.assertEqual(result["object_count"], 6)
        self.assertEqual(client.put_attempts, 6)

    def test_signed_publication_core_rejects_ciphertext_commitment_tampering(self):
        self.fixture.prepare_and_ingest()
        tampered = copy.deepcopy(self.fixture.contract)
        original = tampered["publication_core"]["objects"][0]["ciphertext_sha256"]
        tampered["publication_core"]["objects"][0]["ciphertext_sha256"] = (
            "f" * 64 if original != "f" * 64 else "e" * 64
        )
        tampered["publication_core_sha256"] = campaign._canonical_hash(
            tampered["publication_core"]
        )
        with self.assertRaisesRegex(
            campaign.CampaignSeedError,
            "approval signature",
        ):
            campaign._validate_contract(tampered)

    def test_partial_failure_resumes_and_second_run_never_republishes(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        client.fail_without_commit_at = 3
        with self.assertRaisesRegex(campaign.CampaignSeedError, "exactly one"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        self.assertEqual(client.put_count, 2)
        client.fail_without_commit_at = None
        campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        self.assertEqual(client.put_count, 6)
        attempts = client.put_attempts
        campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        self.assertEqual(client.put_attempts, attempts)

    def test_path_swap_between_hash_and_upload_creates_zero_versions(self):
        self.fixture.prepare_and_ingest()
        first = self.fixture.contract["publication_core"]["objects"][0]
        ciphertext = (
            self.fixture.controller_root
            / "incoming"
            / first["source_role"]
            / f"{first['kind']}.age"
        )

        class SwapClient(FakeS3):
            def __init__(self):
                super().__init__()
                self.list_calls = 0

            def list_object_versions(self, **kwargs):
                self.list_calls += 1
                if self.list_calls == 13:
                    replacement = ciphertext.parent / ".replacement"
                    _write_private(replacement, b"foreign-ciphertext")
                    replacement.replace(ciphertext)
                return super().list_object_versions(**kwargs)

        client = SwapClient()
        with self.assertRaisesRegex(campaign.CampaignSeedError, "exactly one"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        self.assertEqual(client.put_count, 0)
        self.assertEqual(client.objects, {})

    def test_second_distinct_contract_with_same_campaign_keys_is_blocked(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        self.assertEqual(client.put_count, 6)

        second_core = copy.deepcopy(self.fixture.preparation_core)
        second_core["source_state_roots"] = {
            "bot_fi": str(self.fixture.root / "second-source-bot"),
            "webapp_fi": str(self.fixture.root / "second-source-web"),
        }
        second_core["controller_state_root"] = str(
            self.fixture.root / "second-controller"
        )
        _preparations, second = self.fixture.prepare_and_ingest_core(second_core)
        second_hash = campaign._canonical_hash(second)
        attempts = client.put_attempts
        with self.assertRaisesRegex(campaign.CampaignSeedError, "existed"):
            campaign.publish_six(
                contract=second,
                contract_sha256=second_hash,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        self.assertEqual(client.put_attempts, attempts)

    def test_foreign_version_after_baseline_and_multiple_or_delete_marker_block(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        campaign.establish_global_readiness(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        first = self.fixture.contract["core"]["objects"][0]
        client.objects[first["object_key"]] = [
            {
                "version_id": "foreign",
                "body": b"foreign",
                "metadata": {},
                "content_type": "application/octet-stream",
            }
        ]
        with self.assertRaisesRegex(campaign.CampaignSeedError, "PUT intent"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        client.objects[first["object_key"]].append(
            {
                "version_id": "foreign-2",
                "body": b"foreign",
                "metadata": {},
                "content_type": "application/octet-stream",
            }
        )
        with self.assertRaisesRegex(campaign.CampaignSeedError, "multiple versions"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        client.objects[first["object_key"]] = []
        client.delete_markers[first["object_key"]] = ["marker-1"]
        with self.assertRaisesRegex(campaign.CampaignSeedError, "delete marker"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )

    def test_strict_acl_and_provider_sse_fail_closed(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        client.bucket_acl["Grants"].append(
            {
                "Grantee": {
                    "Type": "Group",
                    "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                },
                "Permission": "READ",
            }
        )
        with self.assertRaisesRegex(campaign.CampaignSeedError, "owner-only"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        self.assertEqual(client.put_attempts, 0)

        client = FakeS3()
        client.default_sse = True
        with self.assertRaisesRegex(campaign.CampaignSeedError, "default encryption"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )
        self.assertEqual(client.put_attempts, 0)

        client = FakeS3()
        client.object_acl = {
            "Owner": {"ID": "owner-1"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "other-owner"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }
        with self.assertRaisesRegex(campaign.CampaignSeedError, "owner-only"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )

        client = FakeS3()
        client.object_sse = True
        with self.assertRaisesRegex(campaign.CampaignSeedError, "HEAD"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )

        client = FakeS3()
        client.object_acl = {
            "Owner": {"ID": "owner-2"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "owner-2"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }
        with self.assertRaisesRegex(campaign.CampaignSeedError, "owner-only"):
            campaign.publish_six(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )

    def test_finalize_rechecks_exact_receipts_and_provider_bytes(self):
        self.fixture.prepare_and_ingest()
        client = FakeS3()
        publication = campaign.publish_six(
            contract=self.fixture.contract,
            contract_sha256=self.fixture.contract_sha,
            client=client,
            machine_id_path=self.fixture.controller_machine,
        )
        first_key = publication["objects"][0]["object_key"]
        client.objects[first_key][0]["body"] = b"tampered"
        with self.assertRaises(campaign.CampaignSeedError):
            campaign.finalize_manifests(
                contract=self.fixture.contract,
                contract_sha256=self.fixture.contract_sha,
                publication=publication,
                client=client,
                machine_id_path=self.fixture.controller_machine,
            )

    def test_host_mismatch_and_identity_path_presence_block_source(self):
        web_plan = self.fixture.source_plans["webapp_fi"]
        with self.assertRaisesRegex(campaign.CampaignSeedError, "machine id"):
            campaign.prepare_role(
                source_plan=web_plan,
                role="webapp_fi",
                confirmation=campaign.source_confirmation_phrase(web_plan),
                machine_id_path=self.fixture.controller_machine,
                encrypt=self.fixture.fake_age,
            )
        _write_private(
            self.fixture.identity_paths["bot_fi"],
            b"AGE-SECRET-KEY-TEST123\n",
        )
        bot_plan = self.fixture.source_plans["bot_fi"]
        bot_preparation = campaign.prepare_role(
            source_plan=bot_plan,
            role="bot_fi",
            confirmation=campaign.source_confirmation_phrase(bot_plan),
            machine_id_path=self.fixture.controller_machine,
            encrypt=self.fixture.fake_age,
            derive_recipient=self.fixture.fake_derive_recipient,
        )
        self.assertTrue(
            bot_preparation["identity_path_policy"][
                "allowed_local_identity_present"
            ]
        )
        with self.assertRaisesRegex(campaign.CampaignSeedError, "forbidden target bot_fi"):
            campaign.prepare_role(
                source_plan=web_plan,
                role="webapp_fi",
                confirmation=campaign.source_confirmation_phrase(web_plan),
                machine_id_path=self.fixture.web_machine,
                encrypt=self.fixture.fake_age,
            )

    def test_interrupted_encryption_has_zero_plaintext_residue(self):
        root = self.fixture.source_roots["bot_fi"]
        root.mkdir(mode=0o700)
        interrupted = root / ".postgres.age.encrypting"
        _write_private(interrupted, b"partial-ciphertext")
        plan = self.fixture.source_plans["bot_fi"]
        self.fixture.install_identity("bot_fi")
        try:
            campaign.prepare_role(
                source_plan=plan,
                role="bot_fi",
                confirmation=campaign.source_confirmation_phrase(plan),
                machine_id_path=self.fixture.controller_machine,
                encrypt=self.fixture.fake_age,
                derive_recipient=self.fixture.fake_derive_recipient,
            )
        finally:
            self.fixture.identity_paths["bot_fi"].unlink(missing_ok=True)
        self.assertFalse(interrupted.exists())
        names = {path.name for path in root.iterdir()}
        self.assertFalse(any("plain" in name for name in names))
        self.assertEqual(
            {name for name in names if name.endswith(".age")},
            {"postgres.age", "uploads.age", "audit.age"},
        )

    def test_source_identity_must_derive_exact_approved_role_recipient_before_age(self):
        plan = self.fixture.source_plans["bot_fi"]
        self.fixture.install_identity("bot_fi")
        try:
            with self.assertRaisesRegex(
                campaign.CampaignSeedError,
                "approved role recipient",
            ):
                campaign.prepare_role(
                    source_plan=plan,
                    role="bot_fi",
                    confirmation=campaign.source_confirmation_phrase(plan),
                    machine_id_path=self.fixture.controller_machine,
                    encrypt=self.fixture.fake_age,
                    derive_recipient=lambda _path: "age1" + "z" * 58,
                )
        finally:
            self.fixture.identity_paths["bot_fi"].unlink(missing_ok=True)
        self.assertEqual(self.fixture.age_calls, [])
        self.assertFalse(self.fixture.source_roots["bot_fi"].exists())

    def test_private_state_roots_reject_unsafe_ancestors_and_path_swap(self):
        with self.assertRaisesRegex(campaign.CampaignSeedError, "unsafe private"):
            campaign._ensure_private_directory(Path("/"))

        unsafe_parent = self.fixture.root / "unsafe-parent"
        unsafe_parent.mkdir(mode=0o700)
        unsafe_parent.chmod(0o777)
        with self.assertRaisesRegex(campaign.CampaignSeedError, "unsafe private"):
            campaign._ensure_private_directory(unsafe_parent / "state")

        real_parent = self.fixture.root / "real-parent"
        real_parent.mkdir(mode=0o700)
        alias = self.fixture.root / "state-alias"
        alias.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaisesRegex(campaign.CampaignSeedError, "unsafe private"):
            campaign._ensure_private_directory(alias / "state")

        locked = self.fixture.root / "locked-state"
        moved = self.fixture.root / "moved-state"
        with self.assertRaisesRegex(campaign.CampaignSeedError, "path changed"):
            with campaign._exclusive_lock(locked):
                locked.rename(moved)
                locked.symlink_to(moved, target_is_directory=True)

    def test_source_backup_path_swap_cannot_change_bytes_read_by_age(self):
        plan = self.fixture.source_plans["bot_fi"]
        source = Path(
            self.fixture.backups["bot_fi"]["artifacts"]["postgres"]["path"]
        )
        original_path = source.parent / ".original-postgres"
        replacement = source.parent / ".replacement-postgres"
        _write_private(replacement, b"foreign-secret-payload")
        self.fixture.install_identity("bot_fi")
        calls = 0

        def swapping_age(arguments: list[str]) -> None:
            nonlocal calls
            calls += 1
            output = Path(arguments[arguments.index("--output") + 1])
            if calls == 1:
                source.rename(original_path)
                replacement.rename(source)
            _write_private(
                output,
                b"age-v1-encrypted-header:" + Path(arguments[-1]).read_bytes(),
            )

        try:
            with self.assertRaisesRegex(
                campaign.CampaignSeedError,
                "backup artifact path changed",
            ):
                campaign.prepare_role(
                    source_plan=plan,
                    role="bot_fi",
                    confirmation=campaign.source_confirmation_phrase(plan),
                    machine_id_path=self.fixture.controller_machine,
                    encrypt=swapping_age,
                    derive_recipient=self.fixture.fake_derive_recipient,
                )
            self.assertEqual(calls, 1)
            self.assertFalse(
                (self.fixture.source_roots["bot_fi"] / "postgres.age").exists()
            )
        finally:
            self.fixture.identity_paths["bot_fi"].unlink(missing_ok=True)
            source.unlink(missing_ok=True)
            if original_path.exists():
                original_path.rename(source)

    def test_source_confirmation_is_required_before_state_or_encryption(self):
        plan = self.fixture.source_plans["bot_fi"]
        with self.assertRaisesRegex(
            campaign.CampaignSeedError,
            "confirmation is missing or stale",
        ):
            campaign.prepare_role(
                source_plan=plan,
                role="bot_fi",
                confirmation="stale-confirmation",
                machine_id_path=self.fixture.controller_machine,
                encrypt=self.fixture.fake_age,
            )
        self.assertFalse(self.fixture.source_roots["bot_fi"].exists())
        self.assertEqual(self.fixture.age_calls, [])

    def test_source_requires_fresh_direct_preparation_approval_before_age(self):
        plan = copy.deepcopy(self.fixture.source_plans["bot_fi"])
        plan["preparation_approval_token"]["subject"]["artifact_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            campaign.CampaignSeedError,
            "direct source preparation approval",
        ):
            campaign.prepare_role(
                source_plan=plan,
                role="bot_fi",
                confirmation=campaign.source_confirmation_phrase(plan),
                machine_id_path=self.fixture.controller_machine,
                encrypt=self.fixture.fake_age,
            )
        self.assertEqual(self.fixture.age_calls, [])
        self.assertFalse(self.fixture.source_roots["bot_fi"].exists())

    def test_source_surface_has_no_credentials_private_key_s3_or_delete(self):
        source = inspect.getsource(campaign.prepare_role)
        source += inspect.getsource(campaign._publish_ciphertext)
        self.assertNotIn("credentials", source)
        self.assertNotIn("--decrypt", source)
        self.assertNotIn("AGE-SECRET-KEY", source)
        self.assertNotIn("put_object", source)
        self.assertNotIn("delete_object", inspect.getsource(campaign))
        self.assertNotIn("--machine-id-file", inspect.getsource(campaign._parser))
        self.assertNotIn("identity", inspect.signature(campaign.prepare_role).parameters)
        self.assertNotIn("credentials", inspect.signature(campaign.prepare_role).parameters)
        parser = campaign._parser()
        prepare = parser.parse_args(
            [
                "prepare-role",
                "--source-plan",
                "/tmp/source-plan",
                "--source-role",
                "bot_fi",
                "--confirm",
                "confirmation",
            ]
        )
        self.assertFalse(hasattr(prepare, "credentials"))
        self.assertFalse(hasattr(prepare, "identity"))
        self.assertFalse(hasattr(prepare, "machine_id_file"))
        encoded_plan = json.dumps(self.fixture.source_plans["webapp_fi"])
        self.assertNotIn("publication_approval_token", encoded_plan)
        self.assertIn('"preparation_approval_token"', encoded_plan)
        self.assertIn('"signature"', encoded_plan)
        self.assertIn('"approval_policy":', encoded_plan)
        self.assertNotIn('"access_key"', encoded_plan)
        self.assertNotIn('"secret_key"', encoded_plan)

    def test_contract_is_campaign_generic_and_keys_have_no_publication_uuid(self):
        source = inspect.getsource(campaign)
        self.assertNotIn("771c957b", source)
        self.assertNotIn("727c1f33", source)
        self.assertNotIn("--publication-id", source)
        keys = [
            row["object_key"] for row in self.fixture.preparation_core["objects"]
        ]
        self.assertEqual(len(set(keys)), 6)
        self.assertTrue(all("/seed-v2/" in key for key in keys))

    def test_credentials_must_name_the_signed_credential_id(self):
        path = self.fixture.root / "credentials.json"
        _write_private(
            path,
            json.dumps(
                {
                    "credential_id": "wrong",
                    "access_key": "access-key",
                    "secret_key": "s" * 40,
                }
            ).encode(),
        )
        with self.assertRaisesRegex(campaign.CampaignSeedError, "malformed"):
            campaign._credentials(
                path,
                expected_credential_id="staging-seed-publisher",
            )
        payload = json.loads(path.read_text())
        payload["credential_id"] = "staging-seed-publisher"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)
        self.assertEqual(
            campaign._credentials(
                path,
                expected_credential_id="staging-seed-publisher",
            ),
            ("access-key", "s" * 40),
        )

    def test_immutable_release_rejects_untracked_state(self):
        clean_calls = [
            SimpleNamespace(returncode=0, stdout=(self.fixture.release_sha + "\n").encode()),
            SimpleNamespace(returncode=0, stdout=("2" * 40 + "\n").encode()),
            SimpleNamespace(returncode=0, stdout=b"100644 blob 0\tfile.py\0"),
            SimpleNamespace(returncode=0, stdout=b"?? untracked-secret\n"),
        ]
        with patch.object(
            campaign.subprocess,
            "run",
            side_effect=clean_calls,
        ) as run:
            with self.assertRaisesRegex(campaign.CampaignSeedError, "clean immutable"):
                REAL_IMMUTABLE_RELEASE_BINDING(ROOT, self.fixture.release_sha)
        self.assertIn("--untracked-files=all", run.call_args_list[-1].args[0])

    def test_seal_rechecks_the_exact_clean_release_binding(self):
        self.fixture.prepare_and_ingest()
        changed = copy.deepcopy(self.fixture.immutable_release)
        changed["tracked_index_sha256"] = "f" * 64
        with patch.object(
            campaign,
            "_immutable_release_binding",
            return_value=changed,
        ):
            with self.assertRaisesRegex(
                campaign.CampaignSeedError,
                "differs from the sealed immutable release",
            ):
                campaign.seal_contract(
                    preparation_core=self.fixture.contract["core"],
                    publication_core=self.fixture.contract["publication_core"],
                    publication_approval=self.fixture.contract[
                        "publication_approval_token"
                    ],
                    approval_policy=self.fixture.approval_policy,
                    witness_relay_public_key=self.fixture.witness_key,
                )

    def test_dedicated_seed_action_is_bounded_and_not_session_scoped(self):
        actions = {
            row["action"]: row for row in human_approval_issuer.DEFAULT_ACTIONS
        }
        self.assertEqual(
            actions["approve_seed_preparation"],
            {
                "action": "approve_seed_preparation",
                "environments": ["staging"],
                "max_ttl_seconds": 3600,
            },
        )
        self.assertEqual(
            actions["approve_seed_publication"],
            {
                "action": "approve_seed_publication",
                "environments": ["staging"],
                "max_ttl_seconds": 3600,
            },
        )
        self.assertEqual(
            actions["approve_witness_relay_material"],
            {
                "action": "approve_witness_relay_material",
                "environments": ["staging"],
                "max_ttl_seconds": 3600,
            },
        )
        self.assertEqual(
            actions["approve_source_adoption_backup"],
            {
                "action": "approve_source_adoption_backup",
                "environments": ["staging"],
                "max_ttl_seconds": 3600,
            },
        )
        self.assertNotIn(
            "approve_seed_preparation",
            human_approval_issuer.DEFAULT_STAGING_SESSION_ACTIONS,
        )
        self.assertNotIn(
            "approve_seed_publication",
            human_approval_issuer.DEFAULT_STAGING_SESSION_ACTIONS,
        )
        self.assertNotIn(
            "approve_witness_relay_material",
            human_approval_issuer.DEFAULT_STAGING_SESSION_ACTIONS,
        )
        self.assertNotIn(
            "approve_source_adoption_backup",
            human_approval_issuer.DEFAULT_STAGING_SESSION_ACTIONS,
        )

    def test_direct_totp_contract_approval_and_wrong_witness_key(self):
        self.fixture.prepare_and_ingest()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment = create_enrollment(
            operator="operator",
            password="correct horse battery staple",
            now=now,
            scrypt_n=2**14,
        )
        witness_key = base64.b64encode(b"w" * 32).decode("ascii")
        core = copy.deepcopy(self.fixture.contract["core"])
        core["approval_policy_sha256"] = approval_policy_hash(
            enrollment.policy_payload
        )
        core["witness_relay_public_key_sha256"] = hashlib.sha256(
            (witness_key + "\n").encode()
        ).hexdigest()
        publication_core = copy.deepcopy(self.fixture.contract["publication_core"])
        publication_core["preparation_core_sha256"] = campaign._canonical_hash(core)
        subject = campaign.publication_contract_subject(
            publication_core,
            preparation_core=core,
        )
        token, _state, _audit = authenticate_and_issue(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password="correct horse battery staple",
            totp=totp_code(enrollment.totp_secret, at=now)[1],
            recovery_code=None,
            action="approve_seed_publication",
            environment="staging",
            subject=subject,
            ttl_seconds=600,
            now=now,
        )
        sealed = campaign.seal_contract(
            preparation_core=core,
            publication_core=publication_core,
            publication_approval=token,
            approval_policy=enrollment.policy_payload,
            witness_relay_public_key=witness_key,
            now=now,
        )
        self.assertEqual(
            sealed["publication_approval"]["action"],
            "approve_seed_publication",
        )
        with self.assertRaisesRegex(campaign.CampaignSeedError, "trust inputs"):
            campaign.seal_contract(
                preparation_core=core,
                publication_core=publication_core,
                publication_approval=token,
                approval_policy=enrollment.policy_payload,
                witness_relay_public_key=base64.b64encode(b"x" * 32).decode("ascii"),
                now=now,
            )
        relay_shaped = dict(token)
        relay_shaped["schema"] = "three-site-human-approval-witness-relay-receipt-v2"
        with self.assertRaisesRegex(campaign.CampaignSeedError, "direct password"):
            campaign.seal_contract(
                preparation_core=core,
                publication_core=publication_core,
                publication_approval=relay_shaped,
                approval_policy=enrollment.policy_payload,
                witness_relay_public_key=witness_key,
                now=now,
            )


if __name__ == "__main__":
    unittest.main()
