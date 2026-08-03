from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.collect_three_site_staging_migration_observation import (
    ObservationError,
    ROLE_SERVICES,
    _direct_origin_http_observation,
    _service_observation,
    collect_role,
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
            expected_head="b986c7d8e0f1",
        )
        return stack, args

    @staticmethod
    def _runner(*, wrong_release: bool = False):
        def run(arguments, **_kwargs):
            joined = " ".join(arguments)
            if arguments[-3:] == ["config", "--format", "json"]:
                return json.dumps(
                    {
                        "services": {
                            service: {
                                "image": (
                                    "nginx:1.27-alpine"
                                    if service.endswith("_tls")
                                    else f"trading_bot_three_site_staging:{RELEASE_SHA}"
                                )
                            }
                            for service in ROLE_SERVICES["bot_fi"]
                        }
                    }
                )
            if "psql" in arguments:
                return json.dumps(
                    {"database": "bot", "user": "bot", "revision": "b986c7d8e0f1"}
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
            if "settings.release_sha" in joined:
                return "d" * 40 if wrong_release else RELEASE_SHA
            if "urllib.request.urlopen" in joined:
                return '200\n{"bot_username":"staging_bot"}'
            raise AssertionError(arguments)

        return run

    def test_role_collector_observes_every_service_release_health_and_restart(self):
        stack, args = self._fixture()
        expected_image_inventory_sha = hashlib.sha256(
            json.dumps(
                json.loads(args.image_inventory.read_text()),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
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
        self.assertEqual(
            result["checks"]["signed_runtime_bundle"]["observation"]
            ["image_inventory_sha256"],
            expected_image_inventory_sha,
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

    def test_witness_release_probe_uses_witness_specific_environment(self):
        stack, args = self._fixture()
        with stack:
            calls = []

            def run(arguments, **_kwargs):
                calls.append(arguments)
                joined = " ".join(arguments)
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
                if "WRITER_WITNESS_RELEASE_SHA" in joined:
                    return RELEASE_SHA
                raise AssertionError(arguments)

            with patch(
                "scripts.collect_three_site_staging_migration_observation._run",
                side_effect=run,
            ):
                observation = _service_observation(
                    args,
                    "witness_api",
                    expected_image_reference=f"trading_bot_three_site_staging:{RELEASE_SHA}",
                    expected_image_id="sha256:" + "c" * 64,
                )

        self.assertEqual(observation["release_sha"], RELEASE_SHA)
        release_calls = [call for call in calls if "python" in call and "-c" in call]
        self.assertEqual(len(release_calls), 1)
        self.assertIn("WRITER_WITNESS_RELEASE_SHA", release_calls[0][-1])
        self.assertNotIn("core.config", release_calls[0][-1])

    def test_witness_http_probe_runs_inside_unpublished_api_container(self):
        stack, args = self._fixture()
        args.role = "witness"
        with stack, patch(
            "scripts.collect_three_site_staging_migration_observation._run",
            return_value='200\n{"status":"ready"}',
        ) as run:
            status, body, url = _direct_origin_http_observation(
                args, port=8214, path="/health/ready"
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"status":"ready"}')
        self.assertEqual(url, "container://witness_api:8000/health/ready")
        command = run.call_args.args[0]
        self.assertIn("witness_api", command)
        self.assertIn("http://127.0.0.1:8000/health/ready", command[-1])

    def test_bot_service_observation_binds_live_runtime_token_fingerprint(self):
        stack, args = self._fixture()
        token_sha256 = "f" * 64
        args._bot_material = {"token_sha256": token_sha256}

        def run(arguments, **_kwargs):
            joined = " ".join(arguments)
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
            if "settings.release_sha" in joined:
                return RELEASE_SHA
            if "BOT_TOKEN" in joined:
                return token_sha256
            raise AssertionError(arguments)

        with stack, patch(
            "scripts.collect_three_site_staging_migration_observation._run",
            side_effect=run,
        ):
            observation = _service_observation(
                args,
                "bot_fi_bot",
                expected_image_reference=f"trading_bot_three_site_staging:{RELEASE_SHA}",
                expected_image_id="sha256:" + "c" * 64,
            )

        self.assertEqual(observation["runtime_material_token_sha256"], token_sha256)


if __name__ == "__main__":
    unittest.main()
