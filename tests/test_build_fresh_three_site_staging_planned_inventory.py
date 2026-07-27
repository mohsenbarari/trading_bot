from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    SHARED_HOST_SAFE,
)
from scripts.build_fresh_three_site_staging_planned_inventory import (
    INVENTORY_NAME,
    SUBJECT_NAME,
    FreshInventoryError,
    derive_fresh_planned_inventory,
    main,
)
from scripts.verify_three_site_staging_inventory import (
    ROLE_VOLUME_LOGICAL_NAMES,
    _canonical_bytes,
    verify_inventory,
)
from tests.test_three_site_staging_signed_inventory import _inventory


class BuildFreshThreeSiteStagingPlannedInventoryTests(unittest.TestCase):
    def _derive(
        self,
        template: dict | None = None,
        *,
        execution_class: str = DEDICATED_HOST_DESTRUCTIVE,
    ):
        return derive_fresh_planned_inventory(
            template_inventory=_inventory() if template is None else template,
            release_sha="b" * 40,
            campaign_id="22222222-2222-4222-8222-222222222222",
            deployment_id="three-site-fresh-campaign-test",
            execution_class=execution_class,
        )

    def test_derivation_rebinds_only_campaign_owned_mutable_identity(self):
        template = _inventory()
        result, subject = self._derive(template)

        verified = verify_inventory(result, host_destructive=True)
        self.assertEqual(verified["inventory_stage"], "planned")
        self.assertEqual(result["host_safety_mode"], DEDICATED_HOST_DESTRUCTIVE)
        self.assertEqual(result["release_sha"], "b" * 40)
        self.assertNotEqual(
            result["compose_project_namespace"],
            template.get("compose_project_namespace"),
        )
        self.assertTrue(
            all(role["postgres_system_id"] is None for role in result["roles"])
        )
        old_roles = {role["role"]: role for role in template["roles"]}
        for role in result["roles"]:
            old = old_roles[role["role"]]
            for field in (
                "host_ip",
                "machine_id",
                "docker_daemon_id",
                "storage_root",
                "storage_mount_uuid",
                "resource_limits",
            ):
                self.assertEqual(role[field], old[field])
            for field in ROLE_VOLUME_LOGICAL_NAMES[role["role"]]:
                if role[field] is not None:
                    self.assertNotEqual(role[field], old[field])
            self.assertEqual(role["release_sha"], "b" * 40)
            self.assertEqual(role["deployment_id"], result["deployment_id"])
        self.assertEqual(subject["artifact_type"], result["schema"])
        self.assertEqual(
            subject["artifact_sha256"],
            __import__("hashlib").sha256(_canonical_bytes(result)).hexdigest(),
        )
        self.assertEqual(
            subject["bindings"],
            {
                "campaign_id": result["campaign_id"],
                "deployment_id": result["deployment_id"],
                "host_safety_mode": DEDICATED_HOST_DESTRUCTIVE,
                "inventory_stage": "planned",
            },
        )

    def test_shared_host_mode_rejects_role_colocation(self):
        template = _inventory()
        template["host_safety_mode"] = SHARED_HOST_SAFE
        template["roles"][1]["host_ip"] = template["roles"][0]["host_ip"]
        template["roles"][1]["machine_id"] = template["roles"][0]["machine_id"]
        template["roles"][1]["docker_daemon_id"] = template["roles"][0][
            "docker_daemon_id"
        ]

        with self.assertRaisesRegex(FreshInventoryError, "distinct"):
            self._derive(template, execution_class=SHARED_HOST_SAFE)

    def test_release_campaign_and_deployment_must_all_be_fresh(self):
        template = _inventory()
        common = {
            "template_inventory": template,
            "release_sha": "b" * 40,
            "campaign_id": str(uuid4()),
            "deployment_id": "three-site-fresh-campaign-test",
            "execution_class": DEDICATED_HOST_DESTRUCTIVE,
        }
        for field, old_value in (
            ("release_sha", template["release_sha"]),
            ("campaign_id", template["campaign_id"]),
            ("deployment_id", template["deployment_id"]),
        ):
            values = dict(common)
            values[field] = old_value
            with self.subTest(field=field):
                with self.assertRaises(FreshInventoryError):
                    derive_fresh_planned_inventory(**values)

    def test_builder_cli_uses_strict_root_only_inputs_and_no_overwrite_outputs(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            template = root / "template.json"
            template.write_bytes(_canonical_bytes(_inventory()) + b"\n")
            template.chmod(0o600)
            output_directory = root / "planned-output"
            argv = [
                "--template-inventory",
                str(template),
                "--release-sha",
                "b" * 40,
                "--campaign-id",
                "22222222-2222-4222-8222-222222222222",
                "--deployment-id",
                "three-site-fresh-campaign-test",
                "--execution-class",
                DEDICATED_HOST_DESTRUCTIVE,
                "--output",
                str(output_directory),
            ]

            output = io.StringIO()
            with patch(
                "scripts.build_fresh_three_site_staging_planned_inventory.prove_exact_git_release"
            ) as proof, contextlib.redirect_stdout(output):
                proof.return_value.recheck.return_value = None
                self.assertEqual(main(argv), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["action"], "approve_inventory")
            self.assertFalse(report["secret_inputs_read"])
            inventory_output = output_directory / INVENTORY_NAME
            subject_output = output_directory / SUBJECT_NAME
            self.assertEqual(stat.S_IMODE(inventory_output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(subject_output.stat().st_mode), 0o600)

            original_inventory = inventory_output.read_bytes()
            original_subject = subject_output.read_bytes()
            with patch(
                "scripts.build_fresh_three_site_staging_planned_inventory.prove_exact_git_release"
            ) as proof, contextlib.redirect_stdout(io.StringIO()):
                proof.return_value.recheck.return_value = None
                self.assertEqual(main(argv), 1)
            self.assertEqual(inventory_output.read_bytes(), original_inventory)
            self.assertEqual(subject_output.read_bytes(), original_subject)

    def test_builder_rejects_duplicate_json_broad_mode_and_symlink(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            payload = _canonical_bytes(_inventory()).decode("utf-8")
            duplicate = payload.replace(
                '{"campaign_id":',
                '{"schema":"three-site-staging-inventory-v3","campaign_id":',
                1,
            )
            duplicate_path = root / "duplicate.json"
            duplicate_path.write_text(duplicate, encoding="utf-8")
            duplicate_path.chmod(0o600)

            def argv(source: Path, suffix: str) -> list[str]:
                return [
                    "--template-inventory",
                    str(source),
                    "--release-sha",
                    "b" * 40,
                    "--campaign-id",
                    str(uuid4()),
                    "--deployment-id",
                    f"three-site-fresh-{suffix}",
                    "--execution-class",
                    DEDICATED_HOST_DESTRUCTIVE,
                    "--output",
                    str(root / f"{suffix}.output"),
                ]

            with patch(
                "scripts.build_fresh_three_site_staging_planned_inventory.prove_exact_git_release"
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv(duplicate_path, "duplicate")), 1)

            broad = root / "broad.json"
            broad.write_bytes(_canonical_bytes(_inventory()))
            broad.chmod(0o644)
            with patch(
                "scripts.build_fresh_three_site_staging_planned_inventory.prove_exact_git_release"
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv(broad, "broad-mode")), 1)

            safe = root / "safe.json"
            safe.write_bytes(_canonical_bytes(_inventory()))
            safe.chmod(0o600)
            link = root / "link.json"
            os.symlink(safe, link)
            with patch(
                "scripts.build_fresh_three_site_staging_planned_inventory.prove_exact_git_release"
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv(link, "symlink")), 1)
            self.assertTrue(stat.S_ISLNK(link.lstat().st_mode))


if __name__ == "__main__":
    unittest.main()
