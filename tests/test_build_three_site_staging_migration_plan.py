from __future__ import annotations

from contextlib import redirect_stdout
from datetime import timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from scripts.build_three_site_staging_migration_plan import (
    MigrationPlanBuildError,
    build_migration_plan,
    main,
)
from scripts.verify_three_site_staging_inventory import _canonical_bytes
from scripts.verify_three_site_staging_migration_plan import verify_migration_plan
from tests.test_three_site_staging_migration_plan import (
    ThreeSiteStagingMigrationPlanTests,
)


class BuildThreeSiteStagingMigrationPlanTests(unittest.TestCase):
    def _documents(self):
        return ThreeSiteStagingMigrationPlanTests(methodName="runTest")._documents()

    def _build(self, documents):
        (
            now,
            inventory,
            inventory_approval,
            policy,
            freezes,
            backups,
            seeds,
            images,
            expected_plan,
            _approval,
        ) = documents
        return build_migration_plan(
            inventory=inventory,
            inventory_approval=inventory_approval,
            approval_policy=policy,
            freezes={
                evidence["source_roles"][0]["source_role"]: evidence
                for evidence in freezes
            },
            backups=backups,
            seeds=seeds,
            images=images,
            created_at=expected_plan["created_at"],
            not_after=expected_plan["not_after"],
            now=now,
        )

    def test_builder_reconstructs_the_exact_verified_plan(self):
        documents = self._documents()
        plan = self._build(documents)
        self.assertEqual(plan, documents[-2])
        self.assertIn(
            "bootstrap_webapp_fi_writer_lease_epoch_1",
            plan["ordered_phases"],
        )
        result = verify_migration_plan(
            plan,
            approval=documents[-1],
            inventory=documents[1],
            inventory_approval=documents[2],
            approval_policy=documents[3],
            freeze_evidence=documents[4],
            image_inventories=documents[7],
            backup_manifests=documents[5],
            seed_manifests=documents[6],
            now=documents[0],
        )
        self.assertEqual(result["status"], "approved")

    def test_builder_is_deterministic_for_identical_inputs(self):
        documents = self._documents()
        first = self._build(documents)
        second = self._build(documents)
        self.assertEqual(first, second)

    def test_builder_rejects_a_seed_version_without_exact_readback_identity(self):
        documents = self._documents()
        documents[6]["bot_fi"]["objects"][0]["version_id"] = "null"
        with self.assertRaisesRegex(MigrationPlanBuildError, "seed object"):
            self._build(documents)

    def test_builder_rejects_cross_role_recipient_reuse(self):
        documents = self._documents()
        duplicate = documents[6]["bot_fi"]["recipient_fingerprints"]["bot_fi"]
        documents[6]["webapp_fi"]["recipient_fingerprints"]["webapp_ir"] = duplicate
        with self.assertRaisesRegex(MigrationPlanBuildError, "recipient"):
            self._build(documents)

    def test_builder_rejects_stale_or_overlong_plan_window(self):
        documents = self._documents()
        documents[-2]["not_after"] = (
            documents[0] + timedelta(hours=4, minutes=1)
        ).isoformat()
        with self.assertRaisesRegex(MigrationPlanBuildError, "four hours"):
            self._build(documents)

    def test_builder_rejects_backup_not_bound_to_exact_freeze(self):
        documents = self._documents()
        documents[5]["bot_fi"]["source_freeze_evidence_sha256"] = "f" * 64
        with self.assertRaisesRegex(MigrationPlanBuildError, "exact freeze"):
            self._build(documents)

    def test_builder_rejects_source_database_that_is_a_target(self):
        documents = self._documents()
        target_system_id = documents[1]["roles"][0]["postgres_system_id"]
        backup = documents[5]["bot_fi"]
        freeze = next(
            value
            for value in documents[4]
            if value["source_roles"][0]["source_role"] == "bot_fi"
        )
        backup["source_postgres_system_id"] = target_system_id
        freeze["postgres"]["system_id"] = target_system_id
        backup["source_freeze_evidence_sha256"] = hashlib.sha256(
            _canonical_bytes(freeze)
        ).hexdigest()
        with self.assertRaisesRegex(MigrationPlanBuildError, "unsupported"):
            self._build(documents)

    def test_builder_requires_two_distinct_source_database_system_ids(self):
        documents = self._documents()
        bot_system_id = documents[5]["bot_fi"]["source_postgres_system_id"]
        web_backup = documents[5]["webapp_fi"]
        web_freeze = next(
            value
            for value in documents[4]
            if value["source_roles"][0]["source_role"] == "webapp_fi"
        )
        web_backup["source_postgres_system_id"] = bot_system_id
        web_freeze["postgres"]["system_id"] = bot_system_id
        web_backup["source_freeze_evidence_sha256"] = hashlib.sha256(
            _canonical_bytes(web_freeze)
        ).hexdigest()
        with self.assertRaisesRegex(MigrationPlanBuildError, "unsupported"):
            self._build(documents)

    def test_builder_uses_the_verifier_canonicalization_for_unicode_paths(self):
        documents = self._documents()
        backup = documents[5]["bot_fi"]
        backup["artifacts"]["postgres"]["path"] = "/secure/caf\u00e9.postgres"
        plan = self._build(documents)
        row = next(
            value for value in plan["source_backups"] if value["source_role"] == "bot_fi"
        )
        self.assertEqual(
            row["manifest_sha256"],
            hashlib.sha256(_canonical_bytes(backup)).hexdigest(),
        )

    def test_builder_rejects_backup_created_before_freeze(self):
        documents = self._documents()
        freeze = next(
            value
            for value in documents[4]
            if value["source_roles"][0]["source_role"] == "bot_fi"
        )
        freeze_at = documents[0] - timedelta(minutes=1)
        freeze["observed_at"] = freeze_at.isoformat()
        backup = documents[5]["bot_fi"]
        backup["created_at"] = (freeze_at - timedelta(seconds=1)).isoformat()
        backup["source_freeze_evidence_sha256"] = hashlib.sha256(
            _canonical_bytes(freeze)
        ).hexdigest()
        with self.assertRaisesRegex(MigrationPlanBuildError, "freeze window"):
            self._build(documents)

    def test_cli_publishes_owner_only_outputs_once_without_overwrite(self):
        documents = self._documents()
        (
            _now,
            inventory,
            inventory_approval,
            policy,
            freezes,
            backups,
            seeds,
            images,
            expected_plan,
            _approval,
        ) = documents
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)

            def document(name, value):
                path = root / name
                path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
                path.chmod(0o600)
                return path

            inventory_path = document("inventory.json", inventory)
            inventory_approval_path = document(
                "inventory-approval.json", inventory_approval
            )
            policy_path = document("policy.json", policy)
            freeze_paths = {
                evidence["source_roles"][0]["source_role"]: document(
                    f"freeze-{evidence['source_roles'][0]['source_role']}.json",
                    evidence,
                )
                for evidence in freezes
            }
            backup_paths = {
                role: document(f"backup-{role}.json", value)
                for role, value in backups.items()
            }
            seed_paths = {
                role: document(f"seed-{role}.json", value)
                for role, value in seeds.items()
            }
            image_paths = {
                role: document(f"image-{role}.json", value)
                for role, value in images.items()
            }
            output = root / "migration-plan.json"
            subject = root / "migration-subject.json"
            argv = [
                "--inventory",
                str(inventory_path),
                "--inventory-approval",
                str(inventory_approval_path),
                "--approval-policy",
                str(policy_path),
                "--created-at",
                expected_plan["created_at"],
                "--not-after",
                expected_plan["not_after"],
                "--output",
                str(output),
                "--approval-subject-output",
                str(subject),
            ]
            for role, path in freeze_paths.items():
                argv.extend(("--freeze-evidence", f"{role}={path}"))
            for role, path in backup_paths.items():
                argv.extend(("--backup-manifest", f"{role}={path}"))
            for role, path in seed_paths.items():
                argv.extend(("--seed-manifest", f"{role}={path}"))
            for role, path in image_paths.items():
                argv.extend(("--image-inventory", f"{role}={path}"))

            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 0)
            original_plan = output.read_bytes()
            original_subject = subject.read_bytes()
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(subject.stat().st_mode), 0o600)
            self.assertEqual(output.stat().st_uid, os.geteuid())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 0)
            self.assertEqual(output.read_bytes(), original_plan)
            self.assertEqual(subject.read_bytes(), original_subject)
            subject.write_bytes(b'{"foreign":true}\n')
            subject.chmod(0o600)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 1)
            self.assertEqual(output.read_bytes(), original_plan)
            self.assertEqual(subject.read_bytes(), b'{"foreign":true}\n')


if __name__ == "__main__":
    unittest.main()
