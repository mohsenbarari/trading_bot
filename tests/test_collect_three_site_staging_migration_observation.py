from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.collect_three_site_staging_migration_observation import (
    ObservationError,
    ROLE_SERVICES,
    collect_routing,
    collect_role,
    main,
)
from scripts.legacy_three_site_staging_runtime_fence import (
    LegacyThreeSiteStagingRuntimeRetiredError,
)
from scripts.verify_three_site_staging_image_inventory import _canonical_sha256


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64


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


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return b'{"bot_username":"staging_bot"}'


class MigrationObservationCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._retirement_patch = patch(
            "scripts.collect_three_site_staging_migration_observation.assert_retired",
            return_value=None,
        )
        self._retirement_patch.start()
        self.addCleanup(self._retirement_patch.stop)

    def _fixture(self):  # noqa: ANN202
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        env_file = root / "role.env"
        env_file.write_text(
            "BOT_FI_POSTGRES_USER=bot\n"
            "BOT_FI_POSTGRES_DB=bot\n"
            "TELEGRAM_DELIVERY_PRODUCER_MODE=legacy\n"
            "TELEGRAM_DELIVERY_EXECUTION_OWNER=legacy\n"
            "BOT_FI_DR_BIND_ADDRESS=127.0.0.1\n"
            f"STAGING_DR_CA_CERT={root / 'ca.crt'}\n"
        )
        env_file.chmod(0o600)
        role_compose = root / "compose.yml"
        role_compose.write_text(
            "services:\n"
            + "".join(
                f"  {service}:\n    image: "
                + ("nginx:1.27-alpine" if service.endswith("_tls") else f"trading_bot_three_site_staging:{RELEASE_SHA}")
                + "\n"
                for service in ROLE_SERVICES["bot_fi"]
            )
        )
        role_compose.chmod(0o640)
        image_inventory = root / "images.json"
        image_inventory.write_text(
            json.dumps(
                {
                    "schema": "three-site-staging-image-inventory-v2",
                    "campaign_id": CAMPAIGN_ID,
                    "release_sha": RELEASE_SHA,
                    "role": "bot-fi",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "role_compose_sha256": hashlib.sha256(role_compose.read_bytes()).hexdigest(),
                    "role_env_sha256": hashlib.sha256(env_file.read_bytes()).hexdigest(),
                    "images": [
                        {
                            "reference": f"trading_bot_three_site_staging:{RELEASE_SHA}",
                            "image_id": "sha256:" + "c" * 64,
                            "repo_digests": [],
                            "release_label": RELEASE_SHA,
                            **_content("1"),
                        },
                        {
                            "reference": "nginx:1.27-alpine",
                            "image_id": "sha256:" + "c" * 64,
                            "repo_digests": ["nginx@sha256:" + "d" * 64],
                            "release_label": None,
                            **_content("2"),
                        },
                        {
                            "reference": f"trading_bot_postgres_boottime:15-{RELEASE_SHA}",
                            "image_id": "sha256:" + "c" * 64,
                            "repo_digests": [],
                            "release_label": RELEASE_SHA,
                            **_content("3"),
                        },
                    ],
                }
            )
        )
        image_inventory.chmod(0o600)
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
            role_compose=role_compose,
            env_file=env_file,
            image_inventory=image_inventory,
            routing_observation=routing,
            expected_head="c097d8e9f1a2",
        )
        return stack, args

    @staticmethod
    def _runner(*, wrong_release: bool = False):
        def run(arguments, **_kwargs):
            joined = " ".join(arguments)
            if "config" in arguments and "--format" in arguments:
                return json.dumps(
                    {
                        "services": {
                            service: {
                                "image": (
                                    "nginx:1.27-alpine"
                                    if service.endswith("_tls")
                                    else (
                                        "trading_bot_three_site_staging:"
                                        f"{RELEASE_SHA}"
                                    )
                                )
                            }
                            for service in ROLE_SERVICES["bot_fi"]
                        }
                    }
                )
            if "psql" in arguments:
                return json.dumps(
                    {"database": "bot", "user": "bot", "revision": "c097d8e9f1a2"}
                )
            if " ps -q " in f" {joined} ":
                return "container-id"
            if "{{json .State}}" in arguments:
                return json.dumps({"Running": True, "Health": {"Status": "healthy"}})
            if "{{.Image}}" in arguments:
                return "sha256:" + "c" * 64
            if "{{.RestartCount}}" in arguments:
                return "0"
            if "logs" in arguments:
                return ""
            if "urllib.request" in joined:
                return '200\n{"bot_username":"staging_bot"}'
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
            ), patch(
                "scripts.collect_three_site_staging_migration_observation._tls_observation",
                return_value={
                    "server_name": "bot-fi-dr.staging.internal",
                    "bind_address": "127.0.0.1",
                    "port": 8443,
                    "protocol": "TLSv1.3",
                    "certificate_sha256": "e" * 64,
                    "readiness_status_code": 200,
                },
            ):
            result = collect_role(args)
        services = result["checks"]["service_health"]["observation"]["services"]
        self.assertEqual(len(services), len(ROLE_SERVICES["bot_fi"]))
        self.assertTrue(all(item["restart_count"] == 0 for item in services))
        self.assertTrue(
            all(
                item["release_sha"] == RELEASE_SHA
                for item in services
                if not item["service"].endswith(("_tls", "_redis"))
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
            ), patch(
                "scripts.collect_three_site_staging_migration_observation._tls_observation",
                return_value={
                    "server_name": "bot-fi-dr.staging.internal",
                    "bind_address": "127.0.0.1",
                    "port": 8443,
                    "protocol": "TLSv1.3",
                    "certificate_sha256": "e" * 64,
                    "readiness_status_code": 200,
                },
            ):
            with self.assertRaisesRegex(ObservationError, "wrong release"):
                collect_role(args)


class MigrationObservationCollectorRetirementTests(unittest.TestCase):
    def test_collectors_and_cli_stop_before_docker_provider_or_path_access(self):
        with (
            patch(
                "scripts.collect_three_site_staging_migration_observation.subprocess.run"
            ) as run,
            patch(
                "scripts.collect_three_site_staging_migration_observation.inspect_or_switch"
            ) as provider,
            patch(
                "scripts.collect_three_site_staging_migration_observation.urllib.request.urlopen"
            ) as request,
        ):
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                collect_role(argparse.Namespace())
            with self.assertRaises(LegacyThreeSiteStagingRuntimeRetiredError):
                collect_routing(argparse.Namespace())
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                self.assertEqual(main(["--forged-argument"]), 2)
        run.assert_not_called()
        provider.assert_not_called()
        request.assert_not_called()
        self.assertEqual(
            json.loads(stdout.getvalue())["status"],
            "blocked_legacy_three_site_staging_runtime_retired",
        )


if __name__ == "__main__":
    unittest.main()
