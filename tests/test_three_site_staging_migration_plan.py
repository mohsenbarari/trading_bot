from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from core.human_approval import approval_subject
from core.human_approval_issuer import (
    authenticate_and_issue,
    create_enrollment,
    totp_code,
)

from scripts.verify_three_site_staging_inventory import _canonical_bytes
from scripts.verify_three_site_staging_image_inventory import _canonical_sha256
from scripts.verify_three_site_staging_migration_plan import (
    MigrationPlanError,
    ORDERED_PHASES,
    _verify_approval,
    verify_migration_plan,
)
from tests.test_three_site_staging_signed_inventory import _inventory


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
        now = datetime.now(timezone.utc).replace(microsecond=0)
        inventory = _inventory()
        inventory["production_boundaries"]["postgres_system_ids"].extend(
            [
                str(8000000000000000000 + number)
                for number in (1, 2)
            ]
        )
        enrollment = create_enrollment(
            operator="person-1",
            password="test migration approval passphrase",
            now=now,
            scrypt_n=2**14,
        )
        policy = enrollment.policy_payload
        inventory_subject = approval_subject(
            artifact_type="three-site-staging-inventory-v3",
            artifact_sha256=hashlib.sha256(_canonical_bytes(inventory)).hexdigest(),
            release_sha=inventory["release_sha"],
            bindings={
                "campaign_id": inventory["campaign_id"],
                "deployment_id": inventory["deployment_id"],
                "host_safety_mode": inventory["host_safety_mode"],
                "inventory_stage": inventory["inventory_stage"],
            },
        )
        inventory_approval, issuer_state, _audit = authenticate_and_issue(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=policy,
            private_key_envelope=enrollment.private_key_envelope,
            password="test migration approval passphrase",
            totp=totp_code(enrollment.totp_secret, at=now)[1],
            recovery_code=None,
            action="approve_inventory",
            environment="staging",
            subject=inventory_subject,
            ttl_seconds=3600,
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
                "project_name": (
                    "trading_bot_staging"
                    if role == "bot_fi"
                    else "trading_bot_staging_iran"
                ),
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
        approval_time = now + timedelta(seconds=30)
        migration_subject = approval_subject(
            artifact_type="three-site-staging-migration-plan-v1",
            artifact_sha256=hashlib.sha256(_canonical_bytes(plan)).hexdigest(),
            release_sha=plan["release_sha"],
            bindings={
                "campaign_id": plan["campaign_id"],
                "deployment_id": plan["deployment_id"],
                "provisioned_inventory_sha256": plan["provisioned_inventory_sha256"],
            },
        )
        approval, _state, _audit = authenticate_and_issue(
            secrets_payload=enrollment.secrets_payload,
            state_payload=issuer_state,
            policy_payload=policy,
            private_key_envelope=enrollment.private_key_envelope,
            password="test migration approval passphrase",
            totp=totp_code(enrollment.totp_secret, at=approval_time)[1],
            recovery_code=None,
            action="approve_migration",
            environment="staging",
            subject=migration_subject,
            ttl_seconds=3600,
            now=approval_time,
        )
        return approval_time, inventory, inventory_approval, policy, freezes, backups, seeds, images, plan, approval

    def _verify(self, documents):
        now, inventory, inventory_approval, policy, freezes, backups, seeds, images, plan, approval = documents
        return verify_migration_plan(
            plan,
            approval=approval,
            inventory=inventory,
            inventory_approval=inventory_approval,
            approval_policy=policy,
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

    def test_migration_policy_or_token_tampering_is_rejected(self):
        documents = self._documents()
        documents[3]["issuer"]["operator"] = "attacker"
        with self.assertRaisesRegex(MigrationPlanError, "human approval is invalid"):
            _verify_approval(
                documents[-2],
                approval=documents[-1],
                approval_policy=documents[3],
                release_sha=documents[-2]["release_sha"],
                now=documents[0],
                require_fresh=True,
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

    def test_source_postgres_must_be_a_declared_protected_input(self):
        documents = self._documents()
        documents[-2]["source_backups"][0]["postgres_system_id"] = (
            "8111111111111111111"
        )
        with self.assertRaisesRegex(MigrationPlanError, "source-backup identity"):
            self._verify(documents)

    def test_deployed_repeat_offer_revision_is_a_supported_source(self):
        documents = self._documents()
        freezes = documents[4]
        backups = documents[5]
        plan = documents[-2]
        planned_freeze = {}
        for freeze in freezes:
            freeze["postgres"]["alembic_revision"] = "f2c7d8e9a0b1"
            freeze_hash = hashlib.sha256(_canonical_bytes(freeze)).hexdigest()
            source_role = freeze["source_roles"][0]["source_role"]
            planned_freeze[source_role] = freeze_hash
            backup = backups[source_role]
            backup["source_alembic_revision"] = "f2c7d8e9a0b1"
            backup["restore_drill"]["restored_alembic_revision"] = "f2c7d8e9a0b1"
            backup["source_freeze_evidence_sha256"] = freeze_hash
        plan["source_freeze"]["evidence"] = [
            {
                "evidence_sha256": planned_freeze[
                    freeze["source_roles"][0]["source_role"]
                ],
                "source_roles": [
                    row["source_role"] for row in freeze["source_roles"]
                ],
            }
            for freeze in freezes
        ]
        for row in plan["source_backups"]:
            backup = backups[row["source_role"]]
            row["alembic_revision"] = "f2c7d8e9a0b1"
            row["manifest_sha256"] = hashlib.sha256(
                _canonical_bytes(backup)
            ).hexdigest()
        with self.assertRaisesRegex(MigrationPlanError, "human approval is invalid"):
            self._verify(documents)

    def test_store_local_provider_digest_may_differ_when_content_is_identical(self):
        documents = self._documents()
        images = documents[7]
        plan = documents[-2]
        document = images["webapp_ir"]
        third_party = next(
            item
            for item in document["images"]
            if not item["reference"].startswith(
                (
                    "trading_bot_three_site_staging:",
                    "trading_bot_postgres_boottime:",
                )
            )
        )
        third_party["repo_digests"] = ["redis@sha256:" + "5" * 64]
        next(
            row for row in plan["image_inventories"] if row["role"] == "webapp_ir"
        )["document_sha256"] = hashlib.sha256(
            _canonical_bytes(document)
        ).hexdigest()
        with self.assertRaisesRegex(MigrationPlanError, "human approval is invalid"):
            self._verify(documents)


if __name__ == "__main__":
    unittest.main()
