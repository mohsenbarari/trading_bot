from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.collect_three_site_staging_migration_observation import (
    ObservationError,
    ROLE_SERVICES,
    collect_role,
)


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return b'{"bot_username":"staging_bot"}'


class MigrationObservationCollectorTests(unittest.TestCase):
    def _fixture(self):  # noqa: ANN202
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        env_file = root / "role.env"
        env_file.write_text(
            "BOT_FI_POSTGRES_USER=bot\n"
            "BOT_FI_POSTGRES_DB=bot\n"
            "TELEGRAM_DELIVERY_PRODUCER_MODE=legacy\n"
            "TELEGRAM_DELIVERY_EXECUTION_OWNER=legacy\n"
        )
        env_file.chmod(0o600)
        routing = root / "routing.json"
        routing.write_text(
            json.dumps(
                {
                    "schema": "three-site-staging-routing-observation-v2",
                    "campaign_id": CAMPAIGN_ID,
                    "release_sha": RELEASE_SHA,
                    "plan_sha256": PLAN_SHA,
                    "current_origin_ip": "192.0.2.10",
                    "expected_legacy_origin_ip": "192.0.2.10",
                }
            )
        )
        routing.chmod(0o600)
        args = argparse.Namespace(
            campaign_id=CAMPAIGN_ID,
            release_sha=RELEASE_SHA,
            plan_sha256=PLAN_SHA,
            role="bot_fi",
            role_compose=root / "compose.yml",
            env_file=env_file,
            routing_observation=routing,
            expected_head="d542e3f4a6b7",
        )
        return stack, args

    @staticmethod
    def _runner(*, wrong_release: bool = False):
        def run(arguments, **_kwargs):
            joined = " ".join(arguments)
            if "psql" in arguments:
                return json.dumps(
                    {"database": "bot", "user": "bot", "revision": "d542e3f4a6b7"}
                )
            if " ps -q " in f" {joined} ":
                return "container-id"
            if "{{json .State}}" in arguments:
                return json.dumps({"Running": True, "Health": {"Status": "healthy"}})
            if "{{.Image}}" in arguments:
                return "sha256:" + "c" * 64
            if "{{.RestartCount}}" in arguments:
                return "0"
            if "settings.release_sha" in joined:
                return "d" * 40 if wrong_release else RELEASE_SHA
            raise AssertionError(arguments)

        return run

    def test_role_collector_observes_every_service_release_health_and_restart(self):
        stack, args = self._fixture()
        with stack, patch(
                "scripts.collect_three_site_staging_migration_observation._run",
                side_effect=self._runner(),
            ), patch(
                "scripts.collect_three_site_staging_migration_observation.urllib.request.urlopen",
                return_value=_Response(),
            ):
            result = collect_role(args)
        services = result["checks"]["service_health"]["observation"]["services"]
        self.assertEqual(len(services), len(ROLE_SERVICES["bot_fi"]))
        self.assertTrue(all(item["restart_count"] == 0 for item in services))
        self.assertTrue(
            all(
                item["release_sha"] == RELEASE_SHA
                for item in services
                if not item["service"].endswith("_tls")
            )
        )

    def test_wrong_running_release_fails_closed(self):
        stack, args = self._fixture()
        with stack, patch(
                "scripts.collect_three_site_staging_migration_observation._run",
                side_effect=self._runner(wrong_release=True),
            ), patch(
                "scripts.collect_three_site_staging_migration_observation.urllib.request.urlopen",
                return_value=_Response(),
            ):
            with self.assertRaisesRegex(ObservationError, "wrong release"):
                collect_role(args)


if __name__ == "__main__":
    unittest.main()
