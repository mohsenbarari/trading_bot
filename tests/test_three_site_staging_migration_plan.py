from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts.verify_three_site_staging_inventory import _canonical_bytes
from scripts.verify_three_site_staging_image_inventory import _canonical_sha256
from scripts.verify_three_site_staging_migration_plan import (
    MigrationPlanError,
    ORDERED_PHASES,
    _verify_signatures,
    verify_migration_plan,
)
from tests.test_three_site_staging_signed_inventory import _inventory


def _approvals(document: dict, *, schema: str, hash_field: str, policy: dict, private_keys, now):
    unsigned = {
        "schema": schema,
        hash_field: hashlib.sha256(_canonical_bytes(document)).hexdigest(),
        "release_sha": document["release_sha"],
        "policy_hash": hashlib.sha256(_canonical_bytes(policy)).hexdigest(),
        "signed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    return {
        **unsigned,
        "approvals": [
            {
                "operator": f"person-{number}",
                "key_id": f"operator-{number}",
                "signature": base64.b64encode(
                    private.sign(_canonical_bytes(unsigned))
                ).decode(),
            }
            for number, private in enumerate(private_keys, 1)
        ],
    }


def _content(seed: str):
    descriptor = {
        "architecture": "amd64",
        "os": "linux",
        "created": "2026-07-22T00:00:00Z",
        "config_sha256": "sha256:" + seed * 64,
        "rootfs_type": "layers",
        "rootfs_layers": ["sha256:" + seed * 64],
    }
    return {
        "content_descriptor": descriptor,
        "content_identity": _canonical_sha256(descriptor),
    }


class ThreeSiteStagingMigrationPlanTests(unittest.TestCase):
    def _documents(self):
        now = datetime.now(timezone.utc)
        inventory = _inventory()
        private_keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
        policy = {
            "schema": "three-site-staging-inventory-signers-v1",
            "policy_id": "55555555-5555-4555-8555-555555555555",
            "release_sha": inventory["release_sha"],
            "signers": [
                {
                    "key_id": f"operator-{number}",
                    "operator": f"person-{number}",
                    "custody_domain": f"device-{number}",
                    "public_key": base64.b64encode(
                        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                    ).decode(),
                }
                for number, private in enumerate(private_keys, 1)
            ],
        }
        inventory_approval = _approvals(
            inventory,
            schema="three-site-staging-inventory-approval-v1",
            hash_field="inventory_sha256",
            policy=policy,
            private_keys=private_keys,
            now=now,
        )
        backups = {}
        seeds = {}
        freezes = []
        images = {}
        source_rows = []
        seed_rows = []
        for number, role in enumerate(("bot_fi", "webapp_fi"), 1):
            artifacts = {
                kind: {
                    "path": f"/secure/{role}/{kind}",
                    "bytes": number * 1000 + index,
                    "sha256": hashlib.sha256(f"{role}-{kind}".encode()).hexdigest(),
                    **({} if kind == "postgres" else {"safe_member_count": index}),
                }
                for index, kind in enumerate(("postgres", "uploads", "audit"), 1)
            }
            database_fingerprint = hashlib.sha256(f"database-{role}".encode()).hexdigest()
            redis_observation = {
                "dbsize": number,
                "appendonly": True,
                "lastsave_unix": 1700000000 + number,
                "restore": False,
            }
            freeze = {
                "schema": "three-site-staging-source-freeze-v1",
                "campaign_id": inventory["campaign_id"],
                "target_release_sha": inventory["release_sha"],
                "project_name": "trading_bot_staging",
                "observed_at": (now - timedelta(minutes=1)).isoformat(),
                "source_roles": [
                    {
                        "source_role": role,
                        "app_service": "foreign_app" if role == "bot_fi" else "app",
                        "source_release_sha": str(number) * 40,
                    }
                ],
                "previously_running_services": [
                    "db", "redis", "foreign_app" if role == "bot_fi" else "app"
                ],
                "stopped_services": ["foreign_app" if role == "bot_fi" else "app"],
                "running_services": ["db", "redis"],
                "postgres": {
                    "system_id": str(8000000000000000000 + number),
                    "alembic_revision": "c431d2e3f5a6",
                    "database_fingerprint_sha256": database_fingerprint,
                    "database_row_count": 50,
                    "public_table_count": 10,
                },
                "redis_observation": redis_observation,
                "legacy_restore_bundle": {
                    "schema": "three-site-staging-legacy-restore-bundle-reference-v1",
                    "path": f"/secure/{role}/legacy-restore-bundle.json",
                    "sha256": hashlib.sha256(
                        f"legacy-restore-{role}".encode()
                    ).hexdigest(),
                    "size": 1024,
                },
            }
            freezes.append(freeze)
            freeze_hash = hashlib.sha256(_canonical_bytes(freeze)).hexdigest()
            backup = {
                "schema": "three-site-staging-source-backup-v2",
                "campaign_id": inventory["campaign_id"],
                "source_role": role,
                "source_release_sha": str(number) * 40,
                "target_release_sha": inventory["release_sha"],
                "created_at": now.isoformat(),
                "source_postgres_system_id": str(8000000000000000000 + number),
                "source_alembic_revision": "c431d2e3f5a6",
                "source_freeze_evidence_sha256": freeze_hash,
                "redis_observation": redis_observation,
                "artifacts": artifacts,
                "restore_drill": {
                    "status": "passed",
                    "restored_alembic_revision": "c431d2e3f5a6",
                    "scratch_postgres_system_id": str(7000000000000000000 + number),
                    "database_fingerprint_sha256": database_fingerprint,
                    "database_row_count": 50,
                    "public_table_count": 10,
                },
                "redis_restore": False,
                "application_mutation": False,
            }
            backups[role] = backup
            source_rows.append(
                {
                    "source_role": role,
                    "source_release_sha": backup["source_release_sha"],
                    "manifest_sha256": hashlib.sha256(_canonical_bytes(backup)).hexdigest(),
                    "postgres_system_id": backup["source_postgres_system_id"],
                    "alembic_revision": backup["source_alembic_revision"],
                    "database_fingerprint_sha256": database_fingerprint,
                }
            )
            object_prefix = f"{inventory['object_storage']['prefix']}seed/{role}/"
            readback = hashlib.sha256(f"readback-{role}".encode()).hexdigest()
            seed = {
                "schema": "three-site-staging-seed-manifest-v1",
                "campaign_id": inventory["campaign_id"],
                "release_sha": inventory["release_sha"],
                "source_role": role,
                "bucket": inventory["object_storage"]["bucket"],
                "object_prefix": object_prefix,
                "encryption": "age-x25519",
                "recipient_fingerprint": hashlib.sha256(b"recipient").hexdigest(),
                "objects": [
                    {
                        "kind": kind,
                        "object_key": f"{object_prefix}{hashlib.sha256(f'{role}-{kind}-key'.encode()).hexdigest()}.age",
                        "version_id": f"version-{role}-{kind}",
                        "plaintext_sha256": artifact["sha256"],
                        "plaintext_bytes": artifact["bytes"],
                        "ciphertext_sha256": hashlib.sha256(f"cipher-{role}-{kind}".encode()).hexdigest(),
                        "ciphertext_bytes": artifact["bytes"] + 256,
                    }
                    for kind, artifact in artifacts.items()
                ],
                "readback_evidence_sha256": readback,
            }
            seeds[role] = seed
            seed_rows.append(
                {
                    "source_role": role,
                    "manifest_sha256": hashlib.sha256(_canonical_bytes(seed)).hexdigest(),
                    "object_prefix": object_prefix,
                    "encryption": "age-x25519",
                    "recipient_fingerprint": seed["recipient_fingerprint"],
                    "readback_evidence_sha256": readback,
                }
            )
        image_rows = []
        for number, role in enumerate(("bot_fi", "webapp_fi", "webapp_ir", "witness"), 1):
            compose_hash = hashlib.sha256(f"compose-{role}".encode()).hexdigest()
            env_hash = hashlib.sha256(f"env-{role}".encode()).hexdigest()
            document = {
                "schema": "three-site-staging-image-inventory-v2",
                "campaign_id": inventory["campaign_id"],
                "release_sha": inventory["release_sha"],
                "role": role.replace("_", "-"),
                "observed_at": now.isoformat(),
                "role_compose_sha256": compose_hash,
                "role_env_sha256": env_hash,
                "images": [
                    {
                        "reference": f"trading_bot_three_site_staging:{inventory['release_sha']}",
                        "image_id": "sha256:" + format(number, "x") * 64,
                        "repo_digests": [],
                        "release_label": inventory["release_sha"],
                        **_content("1"),
                    },
                    {
                        "reference": f"trading_bot_postgres_boottime:15-{inventory['release_sha']}",
                        "image_id": "sha256:" + format(number + 4, "x") * 64,
                        "repo_digests": [],
                        "release_label": inventory["release_sha"],
                        **_content("2"),
                    },
                    {
                        "reference": "redis:7-alpine",
                        "image_id": "sha256:" + format(number + 8, "x") * 64,
                        "repo_digests": ["redis@sha256:" + "4" * 64],
                        "release_label": None,
                        **_content("3"),
                    },
                ],
            }
            images[role] = document
            image_rows.append(
                {
                    "role": role,
                    "document_sha256": hashlib.sha256(_canonical_bytes(document)).hexdigest(),
                    "role_compose_sha256": compose_hash,
                    "role_env_sha256": env_hash,
                }
            )
        plan = {
            "schema": "three-site-staging-migration-plan-v1",
            "campaign_id": inventory["campaign_id"],
            "release_sha": inventory["release_sha"],
            "deployment_id": inventory["deployment_id"],
            "provisioned_inventory_sha256": hashlib.sha256(
                _canonical_bytes(inventory)
            ).hexdigest(),
            "created_at": now.isoformat(),
            "not_after": (now + timedelta(hours=2)).isoformat(),
            "source_freeze": {
                "required": True,
                "evidence": [
                    {
                        "evidence_sha256": hashlib.sha256(
                            _canonical_bytes(evidence)
                        ).hexdigest(),
                        "source_roles": [
                            row["source_role"] for row in evidence["source_roles"]
                        ],
                    }
                    for evidence in freezes
                ],
                "redis_restore": False,
            },
            "source_backups": source_rows,
            "seed_bundles": seed_rows,
            "image_inventories": image_rows,
            "target_seed_map": [
                {"target_role": "bot_fi", "source_role": "bot_fi", "mode": "restore"},
                {"target_role": "webapp_fi", "source_role": "webapp_fi", "mode": "restore"},
                {"target_role": "webapp_ir", "source_role": "webapp_fi", "mode": "clone"},
                {"target_role": "witness", "source_role": None, "mode": "empty"},
            ],
            "ordered_phases": list(ORDERED_PHASES),
            "rollback_policy": {
                "strategy": "forward-rollback-or-separate-restore",
                "alembic_downgrade": False,
                "routing_held_until_commit": True,
                "legacy_sources_retained": True,
                "cleanup_requires_explicit_finish": True,
            },
        }
        approval = _approvals(
            plan,
            schema="three-site-staging-migration-approval-v1",
            hash_field="plan_sha256",
            policy=policy,
            private_keys=private_keys,
            now=now,
        )
        return now, inventory, inventory_approval, policy, freezes, backups, seeds, images, plan, approval

    def _verify(self, documents):
        now, inventory, inventory_approval, policy, freezes, backups, seeds, images, plan, approval = documents
        return verify_migration_plan(
            plan,
            approval=approval,
            inventory=inventory,
            inventory_approval=inventory_approval,
            signer_policy=policy,
            freeze_evidence=freezes,
            image_inventories=images,
            backup_manifests=backups,
            seed_manifests=seeds,
            now=now,
        )

    def test_exact_signed_plan_with_backup_and_encrypted_seed_evidence_passes(self):
        result = self._verify(self._documents())
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["source_roles"], ["bot_fi", "webapp_fi"])

    def test_migration_policy_cannot_alias_one_key_as_two_people(self):
        documents = self._documents()
        documents[3]["signers"][1]["public_key"] = documents[3]["signers"][0]["public_key"]
        with self.assertRaisesRegex(MigrationPlanError, "not independent"):
            _verify_signatures(
                documents[-2],
                approval=documents[-1],
                signer_policy=documents[3],
                release_sha=documents[-2]["release_sha"],
                now=documents[0],
            )

    def test_webapp_ir_must_clone_the_webapp_fi_seed(self):
        documents = self._documents()
        documents[-2]["target_seed_map"][2]["source_role"] = "bot_fi"
        with self.assertRaisesRegex(MigrationPlanError, "seed map"):
            self._verify(documents)

    def test_plan_mutation_after_signing_is_rejected(self):
        documents = self._documents()
        documents[-2]["source_freeze"]["redis_restore"] = True
        with self.assertRaises(MigrationPlanError):
            self._verify(documents)

    def test_seed_plaintext_must_match_the_verified_backup(self):
        documents = self._documents()
        documents[-4]["webapp_fi"]["objects"][0]["plaintext_sha256"] = "0" * 64
        with self.assertRaises(MigrationPlanError):
            self._verify(documents)


if __name__ == "__main__":
    unittest.main()
