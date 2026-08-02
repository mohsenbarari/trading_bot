from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.adopt_three_site_staging_source_freeze import (
    SourceFreezeError,
    build_plan,
    confirmation_phrase,
    execute,
)


class AdoptThreeSiteStagingSourceFreezeTests(unittest.TestCase):
    def _args(self, directory: str) -> argparse.Namespace:
        campaign = "11111111-1111-4111-8111-111111111111"
        target = "a" * 40
        return argparse.Namespace(
            source_role=["webapp_fi"],
            expected_source_release_sha={"webapp_fi": "b" * 40},
            project_name="trading_bot_staging_iran",
            prior_freeze_evidence=Path(directory) / "prior.json",
            output=Path(directory) / "fresh.json",
            confirm=confirmation_phrase(campaign, "webapp_fi", target),
        )

    def test_plan_is_explicitly_non_mutating(self):
        args = argparse.Namespace(
            source_role="webapp_fi", project_name="trading_bot_staging_iran"
        )
        inventory = {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
        }
        plan = build_plan(args, inventory)
        self.assertFalse(plan["application_mutation"])
        self.assertFalse(plan["redis_restore"])

    def test_apply_re_attests_frozen_source_without_container_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self._args(directory)
            inventory = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "release_sha": "a" * 40,
            }
            prior = {
                "previously_running_services": ["app", "db", "redis"],
                "stopped_services": ["app", "migration"],
                "postgres": {
                    "system_id": "8000000000000000001",
                    "alembic_revision": "f2c7d8e9a0b1",
                },
                "legacy_restore_bundle": {"reference": True},
            }
            prefix = ["/usr/bin/docker", "compose"]

            def fake_run(arguments, *, timeout=30):  # noqa: ANN001, ARG001
                if "--status" in arguments:
                    return "db\nredis"
                if arguments[-2:] == ["-q", "app"]:
                    return "container-app"
                if arguments[:2] == ["/usr/bin/docker", "inspect"]:
                    return "false"
                if "DBSIZE" in arguments:
                    return "7"
                if "CONFIG" in arguments:
                    return "appendonly\nyes"
                if "LASTSAVE" in arguments:
                    return "1700000000"
                raise AssertionError(arguments)

            def fake_psql(_prefix, _service, _user, _database, sql):  # noqa: ANN001
                if "system_identifier" in sql:
                    return "8000000000000000001"
                if "alembic_version" in sql:
                    return "f2c7d8e9a0b1"
                raise AssertionError(sql)

            with patch(
                "scripts.adopt_three_site_staging_source_freeze._validate_static",
                return_value=(Path(directory), {}, prefix, [], "postgres", "trading"),
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._load_prior_freeze",
                return_value=prior,
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._verify_restore_bundle",
                return_value={
                    "schema": "three-site-staging-legacy-restore-bundle-reference-v1",
                    "path": "/secure/rollback.json",
                    "sha256": "c" * 64,
                    "size": 100,
                },
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._run",
                side_effect=fake_run,
            ) as run, patch(
                "scripts.adopt_three_site_staging_source_freeze._psql",
                side_effect=fake_psql,
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._database_fingerprint",
                return_value=("d" * 64, 1410, 46),
            ):
                result = execute(args, inventory_result=inventory)
            self.assertEqual(result["status"], "adopted-frozen")
            self.assertFalse(result["application_mutation"])
            evidence = json.loads(args.output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["running_services"], ["db", "redis"])
            self.assertEqual(evidence["postgres"]["database_row_count"], 1410)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertFalse(any("start" in command or "stop" in command for command in commands))

    def test_apply_fails_closed_if_application_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self._args(directory)
            inventory = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "release_sha": "a" * 40,
            }
            prior = {
                "previously_running_services": ["app", "db", "redis"],
                "stopped_services": ["app"],
                "postgres": {
                    "system_id": "8000000000000000001",
                    "alembic_revision": "f2c7d8e9a0b1",
                },
                "legacy_restore_bundle": {"reference": True},
            }
            prefix = ["/usr/bin/docker", "compose"]

            def fake_run(arguments, *, timeout=30):  # noqa: ANN001, ARG001
                if "--status" in arguments:
                    return "db\nredis"
                if arguments[-2:] == ["-q", "app"]:
                    return "container-app"
                if arguments[:2] == ["/usr/bin/docker", "inspect"]:
                    return "true"
                raise AssertionError(arguments)

            with patch(
                "scripts.adopt_three_site_staging_source_freeze._validate_static",
                return_value=(Path(directory), {}, prefix, [], "postgres", "trading"),
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._load_prior_freeze",
                return_value=prior,
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._verify_restore_bundle",
                return_value={"reference": True},
            ), patch(
                "scripts.adopt_three_site_staging_source_freeze._run",
                side_effect=fake_run,
            ):
                with self.assertRaisesRegex(SourceFreezeError, "unexpectedly running"):
                    execute(args, inventory_result=inventory)


if __name__ == "__main__":
    unittest.main()
