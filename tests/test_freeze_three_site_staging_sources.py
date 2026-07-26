from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.freeze_three_site_staging_sources import (
    SourceFreezeError,
    _compose,
    build_plan,
    confirmation_phrase,
    execute,
)


class FreezeThreeSiteStagingSourcesTests(unittest.TestCase):
    def test_webapp_source_accepts_only_the_deployed_staging_project_alias(self):
        args = argparse.Namespace(
            project_name="trading_bot_staging_iran",
            source_role=["webapp_fi"],
            compose=Path("/secure/compose.yml"),
            env_file=Path("/secure/staging.env"),
        )
        prefix = _compose(args)
        self.assertEqual(prefix[prefix.index("-p") + 1], "trading_bot_staging_iran")
        args.source_role = ["bot_fi"]
        with self.assertRaisesRegex(SourceFreezeError, "not approved"):
            _compose(args)

    def test_bot_source_enables_reviewed_staging_bot_profile(self):
        args = argparse.Namespace(
            project_name="trading_bot_staging",
            source_role=["bot_fi"],
            compose=Path("/secure/compose.yml"),
            env_file=Path("/secure/staging.env"),
        )
        prefix = _compose(args)
        self.assertIn("--profile", prefix)
        self.assertEqual(prefix[prefix.index("--profile") + 1], "staging-bot")

    def test_plan_excludes_database_and_redis_from_stop_set(self):
        plan = build_plan(
            campaign_id="11111111-1111-4111-8111-111111111111",
            release_sha="a" * 40,
            roles=["webapp_fi", "bot_fi"],
            services=["db", "redis", "app", "foreign_app", "bot"],
        )
        self.assertEqual(plan["services_to_keep"], ["db", "redis"])
        self.assertEqual(plan["services_to_stop"], ["app", "bot", "foreign_app"])
        self.assertEqual(
            plan["required_confirmation"],
            confirmation_phrase(
                "11111111-1111-4111-8111-111111111111",
                ["webapp_fi", "bot_fi"],
                "a" * 40,
            ),
        )

    def test_apply_records_exact_rollback_set_and_frozen_database_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "freeze.json"
            args = argparse.Namespace(
                source_role=["bot_fi", "webapp_fi"],
                expected_source_release_sha={"bot_fi": "b" * 40, "webapp_fi": "c" * 40},
                project_name="trading_bot_staging",
                output=output,
                rollback_bundle_dir=Path(directory) / "rollback-bundle",
                confirm=confirmation_phrase(
                    "11111111-1111-4111-8111-111111111111",
                    ["bot_fi", "webapp_fi"],
                    "a" * 40,
                ),
            )
            inventory_result = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "release_sha": "a" * 40,
            }
            prefix = ["/usr/bin/docker", "compose"]
            services = ["db", "redis", "app", "sync_worker", "foreign_app", "bot"]
            ps_calls = 0

            def fake_run(arguments, *, timeout=30):  # noqa: ANN001, ARG001
                nonlocal ps_calls
                if "config" in arguments and "--format" in arguments:
                    return "services:\n  app:\n    image: pinned"
                if arguments[:2] == ["/usr/bin/docker", "inspect"]:
                    return "sha256:" + "1" * 64
                if "ps" in arguments and "--status" in arguments:
                    ps_calls += 1
                    return (
                        "db\nredis\napp\nsync_worker\nforeign_app\nbot"
                        if ps_calls == 1 else "db\nredis"
                    )
                if "ps" in arguments and "-q" in arguments:
                    return "container-" + arguments[-1]
                if "printenv" in arguments:
                    service = arguments[arguments.index("exec") + 2]
                    return "b" * 40 if service == "foreign_app" else "c" * 40
                if "DBSIZE" in arguments:
                    return "12"
                if "CONFIG" in arguments:
                    return "appendonly\nyes"
                if "LASTSAVE" in arguments:
                    return "1700000000"
                if "stop" in arguments:
                    return ""
                raise AssertionError(arguments)

            def fake_psql(_prefix, _service, _user, _database, sql):  # noqa: ANN001
                if "system_identifier" in sql:
                    return "8000000000000000001"
                if "alembic_version" in sql:
                    return "f1b6e7f8a9dc"
                raise AssertionError(sql)

            with patch(
                "scripts.freeze_three_site_staging_sources._validate_static",
                return_value=(Path(directory), {}, prefix, services, "postgres", "trading"),
            ), patch(
                "scripts.freeze_three_site_staging_sources._run", side_effect=fake_run
            ), patch(
                "scripts.freeze_three_site_staging_sources._psql", side_effect=fake_psql
            ), patch(
                "scripts.freeze_three_site_staging_sources._database_fingerprint",
                return_value=("d" * 64, 20, 5),
            ):
                result = execute(args, inventory_result=inventory_result)
            self.assertEqual(result["status"], "frozen")
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["running_services"], ["db", "redis"])
            self.assertEqual(
                evidence["stopped_services"],
                ["app", "bot", "foreign_app", "sync_worker"],
            )
            self.assertEqual(evidence["postgres"]["system_id"], "8000000000000000001")
            self.assertFalse(evidence["redis_observation"]["restore"])
            self.assertIn("bot", evidence["previously_running_services"])
            reference = evidence["legacy_restore_bundle"]
            self.assertEqual(
                reference["schema"],
                "three-site-staging-legacy-restore-bundle-reference-v1",
            )
            self.assertTrue(Path(reference["path"]).is_file())
            manifest = json.loads(Path(reference["path"]).read_text(encoding="utf-8"))
            self.assertEqual(
                set(manifest["service_images"]), set(evidence["previously_running_services"])
            )


if __name__ == "__main__":
    unittest.main()
