import copy
import base64
import json
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from scripts import verify_three_site_production_shadow_compose as MODULE
from scripts.verify_three_site_production_shadow_compose import (
    DATA_ROOT_PREFIX,
    PROJECT_ROOT_PREFIX,
    SECRET_ROOT_PREFIX,
    ProductionShadowComposeError,
    WEBAPP_API_PROVIDER_KEYS,
    _required_values,
    _provider_config_sha256,
    collect_environment_failures,
    collect_source_failures,
    load_compose,
    run_compose_config,
    validate_api_runtime_envs,
    validate_pristine_redis_targets,
    verify_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "deploy/production/docker-compose.three-site-shadow.yml"
OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "1" * 40


class ThreeSiteProductionShadowComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_text, cls.document = load_compose(COMPOSE_PATH)

    def valid_environment(self) -> dict[str, str]:
        values = {
            key: f"test-value-for-{key.lower()}"
            for key in _required_values(self.source_text)
        }
        values.update(
            {
                "PRODUCTION_SHADOW_OPERATION_ID": OPERATION_ID,
                "PRODUCTION_SHADOW_RELEASE_SHA": RELEASE_SHA,
                "PRODUCTION_SHADOW_PROJECT": "tb3p-123e4567e89b42d3a456426614174000",
                "PRODUCTION_SHADOW_CGROUP_PARENT": "tb3p-123e4567e89b42d3a456426614174000",
                "PRODUCTION_SHADOW_PROJECT_ROOT": (
                    f"{PROJECT_ROOT_PREFIX}/{OPERATION_ID}"
                ),
                "PRODUCTION_SHADOW_RELEASE_ROOT": (
                    f"{PROJECT_ROOT_PREFIX}/{OPERATION_ID}/releases/{RELEASE_SHA}"
                ),
                "PRODUCTION_SHADOW_DATA_ROOT": (
                    f"{DATA_ROOT_PREFIX}/{OPERATION_ID}"
                ),
                "PRODUCTION_SHADOW_SECRET_ROOT": (
                    f"{SECRET_ROOT_PREFIX}/{OPERATION_ID}"
                ),
                "PRODUCTION_SHADOW_APP_IMAGE_ID": f"sha256:{'1' * 64}",
                "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": f"sha256:{'2' * 64}",
                "PRODUCTION_SHADOW_REDIS_IMAGE_ID": f"sha256:{'3' * 64}",
                "PRODUCTION_SHADOW_NGINX_IMAGE_ID": f"sha256:{'4' * 64}",
                "BOT_FI_POSTGRES_USER": "bot_fi_owner",
                "BOT_FI_POSTGRES_DB": "bot_fi_shadow",
                "WEBAPP_FI_POSTGRES_USER": "webapp_fi_owner",
                "WEBAPP_FI_POSTGRES_DB": "webapp_fi_shadow",
                "WEBAPP_IR_POSTGRES_USER": "webapp_ir_owner",
                "WEBAPP_IR_POSTGRES_DB": "webapp_ir_shadow",
                "BOT_FI_SHADOW_API_PORT": "9311",
                "WEBAPP_FI_SHADOW_API_PORT": "9312",
                "WEBAPP_IR_SHADOW_API_PORT": "9313",
                "BOT_FI_SHADOW_DR_PORT": "9442",
                "WEBAPP_FI_SHADOW_DR_PORT": "9443",
                "WEBAPP_IR_SHADOW_DR_PORT": "9444",
                "BOT_FI_SHADOW_DR_BIND_ADDRESS": "65.109.216.187",
                "WEBAPP_FI_SHADOW_DR_BIND_ADDRESS": "65.109.220.59",
                "WEBAPP_IR_SHADOW_DR_BIND_ADDRESS": "95.38.164.29",
                "BOT_FI_PEER_WEBAPP_FI_IP": "65.109.220.59",
                "WEBAPP_FI_PEER_BOT_FI_IP": "65.109.216.187",
                "WEBAPP_FI_PEER_WEBAPP_IR_IP": "95.38.164.29",
                "WEBAPP_IR_PEER_WEBAPP_FI_IP": "65.109.220.59",
                "BOT_FI_PUBLIC_WEBAPP_URL": "https://bot-fi.example.invalid",
                "WEBAPP_FI_PUBLIC_WEBAPP_URL": "https://webapp-fi.example.invalid",
                "WEBAPP_IR_PUBLIC_WEBAPP_URL": "https://webapp-ir.example.invalid",
                "BOT_FI_BACKGROUND_JOBS_ENABLED": "true",
                "WEBAPP_FI_BACKGROUND_JOBS_ENABLED": "true",
                "WEBAPP_IR_BACKGROUND_JOBS_ENABLED": "true",
                "BOT_FI_API_WORKERS": "2",
                "WEBAPP_FI_API_WORKERS": "2",
                "WEBAPP_IR_API_WORKERS": "2",
                "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                "TELEGRAM_WEBAPP_VALIDATION_KEY": "e" * 64,
                "PRODUCTION_SHADOW_DR_CA_SHA256": "5" * 64,
                "PRODUCTION_SHADOW_WITNESS_URL": "https://37.152.191.11",
                "PRODUCTION_SHADOW_WITNESS_IP": "37.152.191.11",
                "PRODUCTION_SHADOW_WITNESS_TLS_SAN": "IP:37.152.191.11",
                "PRODUCTION_SHADOW_WITNESS_CA_SHA256": "6" * 64,
                "PRODUCTION_SHADOW_WITNESS_SERVER_CERT_SHA256": "7" * 64,
                "PRODUCTION_SHADOW_WITNESS_RELEASE_SHA": RELEASE_SHA,
                "PRODUCTION_SHADOW_WITNESS_RELEASE_MANIFEST_SHA256": "8" * 64,
                "PRODUCTION_SHADOW_WITNESS_HEALTH_ATTESTATION_SHA256": "9" * 64,
                "PRODUCTION_SHADOW_WITNESS_HEALTH_ATTESTED_AT_EPOCH": str(
                    int(time.time())
                ),
                "WRITER_WITNESS_PUBLIC_KEY": base64.b64encode(
                    b"w" * 32
                ).decode("ascii"),
                "WEBAPP_FI_WITNESS_KEY_ID": "webapp-fi-operation-key",
                "WEBAPP_IR_WITNESS_KEY_ID": "webapp-ir-operation-key",
                "WEBAPP_FI_WITNESS_SECRET": "f" * 32,
                "WEBAPP_IR_WITNESS_SECRET": "i" * 32,
                "DR_BLOB_OBJECT_ENDPOINT": (
                    "https://s3.ir-thr-at1.arvanstorage.ir"
                ),
                "DR_BLOB_OBJECT_REGION": "ir-thr-at1",
                "DR_BLOB_OBJECT_BUCKET": "production-sync-coin",
                "DR_BLOB_OBJECT_PREFIX": f"production-shadow/{OPERATION_ID}/blobs",
                "DR_BLOB_POLICY_ATTESTATION_SHA256": "a" * 64,
                "DR_BLOB_POLICY_ATTESTED_AT_EPOCH": str(int(time.time())),
                "DR_BLOB_COMPATIBILITY_ATTESTATION_SHA256": "b" * 64,
                "DR_BLOB_COMPATIBILITY_ATTESTED_AT_EPOCH": str(
                    int(time.time())
                ),
                "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256": "c" * 64,
                "PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH": str(
                    int(time.time())
                ),
                "BOT_FI_DR_PEERS_JSON": json.dumps(
                    [
                        {
                            "site": "webapp_fi",
                            "base_url": (
                                "https://webapp-fi-dr.production.internal:9443"
                            ),
                        }
                    ],
                    separators=(",", ":"),
                ),
                "WEBAPP_FI_DR_PEERS_JSON": json.dumps(
                    [
                        {
                            "site": "bot_fi",
                            "base_url": "https://bot-fi-dr.production.internal:9442",
                        },
                        {
                            "site": "webapp_ir",
                            "base_url": "https://webapp-ir-dr.production.internal:9444",
                        },
                    ],
                    separators=(",", ":"),
                ),
                "WEBAPP_IR_DR_PEERS_JSON": json.dumps(
                    [
                        {
                            "site": "webapp_fi",
                            "base_url": "https://webapp-fi-dr.production.internal:9443",
                        }
                    ],
                    separators=(",", ":"),
                ),
                "BOT_FI_DR_PAIRWISE_KEYS_JSON": json.dumps(
                    [
                        {
                            "key_id": "bot-fi-to-webapp-fi",
                            "source_site": "bot_fi",
                            "destination_site": "webapp_fi",
                            "secret": "a" * 32,
                        },
                        {
                            "key_id": "webapp-fi-to-bot-fi",
                            "source_site": "webapp_fi",
                            "destination_site": "bot_fi",
                            "secret": "b" * 32,
                        },
                    ],
                    separators=(",", ":"),
                ),
                "WEBAPP_FI_DR_PAIRWISE_KEYS_JSON": json.dumps(
                    [
                        {
                            "key_id": "bot-fi-to-webapp-fi",
                            "source_site": "bot_fi",
                            "destination_site": "webapp_fi",
                            "secret": "a" * 32,
                        },
                        {
                            "key_id": "webapp-fi-to-bot-fi",
                            "source_site": "webapp_fi",
                            "destination_site": "bot_fi",
                            "secret": "b" * 32,
                        },
                        {
                            "key_id": "webapp-fi-to-webapp-ir",
                            "source_site": "webapp_fi",
                            "destination_site": "webapp_ir",
                            "secret": "c" * 32,
                        },
                        {
                            "key_id": "webapp-ir-to-webapp-fi",
                            "source_site": "webapp_ir",
                            "destination_site": "webapp_fi",
                            "secret": "d" * 32,
                        },
                    ],
                    separators=(",", ":"),
                ),
                "WEBAPP_IR_DR_PAIRWISE_KEYS_JSON": json.dumps(
                    [
                        {
                            "key_id": "webapp-fi-to-webapp-ir",
                            "source_site": "webapp_fi",
                            "destination_site": "webapp_ir",
                            "secret": "c" * 32,
                        },
                        {
                            "key_id": "webapp-ir-to-webapp-fi",
                            "source_site": "webapp_ir",
                            "destination_site": "webapp_fi",
                            "secret": "d" * 32,
                        },
                    ],
                    separators=(",", ":"),
                ),
            }
        )
        values["WEBAPP_PROVIDER_CONFIG_SHA256"] = _provider_config_sha256(values)
        return values

    def assert_source_failure(
        self,
        document: dict,
        expected: str,
        *,
        source_text: str | None = None,
    ) -> None:
        failures = collect_source_failures(
            document,
            self.source_text if source_text is None else source_text,
        )
        self.assertIn(expected, "\n".join(failures))

    def write_api_runtime_envs(
        self,
        root: Path,
        values: dict[str, str],
        *,
        validation_key: str = "e" * 64,
    ) -> None:
        for role in ("bot_fi", "webapp_fi", "webapp_ir"):
            is_bot = role == "bot_fi"
            role_path = root / role.replace("_", "-")
            role_path.mkdir(mode=0o700)
            payload = {
                "SERVER_MODE": "foreign" if is_bot else "iran",
                "LOGICAL_AUTHORITY": "foreign" if is_bot else "webapp",
                "PHYSICAL_SITE": role,
                "RELEASE_SHA": values["PRODUCTION_SHADOW_RELEASE_SHA"],
                "JWT_SECRET_KEY": values[
                    "BOT_FI_JWT_SECRET_KEY" if is_bot else "WEBAPP_JWT_SECRET_KEY"
                ],
                "TELEGRAM_DELIVERY_PRODUCER_MODE": values[
                    "TELEGRAM_DELIVERY_PRODUCER_MODE"
                ],
                "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": values[
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER"
                ],
                "TELEGRAM_WEBAPP_VALIDATION_KEY": validation_key,
            }
            if not is_bot:
                payload["ORIGIN_READINESS_API_KEY"] = values[
                    "ORIGIN_READINESS_API_KEY"
                ]
                payload.update(
                    {
                        key: values[key]
                        for key in WEBAPP_API_PROVIDER_KEYS
                    }
                )
            path = role_path / "runtime.env.api"
            path.write_text(
                "\n".join(f"{key}={value}" for key, value in payload.items()) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
        bot_path = root / "bot-fi/runtime.env.bot"
        bot_path.write_text(
            "\n".join(
                f"{key}={value}"
                for key, value in {
                    "SERVER_MODE": "foreign",
                    "LOGICAL_AUTHORITY": "foreign",
                    "PHYSICAL_SITE": "bot_fi",
                    "RELEASE_SHA": values["PRODUCTION_SHADOW_RELEASE_SHA"],
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                    "BOT_TOKEN": "test-bot-token",
                    "BOT_USERNAME": "test_bot",
                    "CHANNEL_ID": "-1001234567890",
                }.items()
            )
            + "\n",
            encoding="utf-8",
        )
        bot_path.chmod(0o600)

    def test_contract_accepts_operation_scoped_production_slice(self):
        summary = verify_contract(
            document=self.document,
            source_text=self.source_text,
            values=self.valid_environment(),
        )

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["service_count"], 50)
        self.assertEqual(summary["profile_count"], 24)
        self.assertTrue(summary["full_product_topology"])
        self.assertEqual(
            summary["witness_mode"],
            "external-canonical-attestation-values-bound-only",
        )

    def test_docker_compose_config_accepts_all_profiles_and_data_ready_is_store_only(self):
        values = self.valid_environment()
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "shadow.env"
            env_path.write_text(
                "\n".join(f"{key}={value}" for key, value in sorted(values.items())) + "\n",
                encoding="utf-8",
            )
            env_path.chmod(0o600)
            run_compose_config(
                compose_path=COMPOSE_PATH,
                env_file=env_path,
                values=values,
                resolve_service_env_files=False,
            )
            completed = subprocess.run(
                [
                    "/usr/bin/docker",
                    "compose",
                    "--env-file",
                    str(env_path),
                    "--file",
                    str(COMPOSE_PATH),
                    "--profile",
                    "webapp-fi-data-ready",
                    "config",
                    "--no-env-resolution",
                    "--services",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/root",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    **values,
                },
            )
            default_completed = subprocess.run(
                [
                    "/usr/bin/docker",
                    "compose",
                    "--env-file",
                    str(env_path),
                    "--file",
                    str(COMPOSE_PATH),
                    "config",
                    "--no-env-resolution",
                    "--services",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/root",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    **values,
                },
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            set(completed.stdout.splitlines()),
            {"webapp_fi_db", "webapp_fi_redis"},
        )
        self.assertEqual(default_completed.returncode, 0, default_completed.stderr)
        self.assertEqual(default_completed.stdout.strip(), "")

    def test_rejects_build_container_name_and_mutable_image(self):
        for key in ("build", "container_name"):
            with self.subTest(key=key):
                document = copy.deepcopy(self.document)
                document["services"]["webapp_fi_api"][key] = "forbidden"
                self.assert_source_failure(document, "forbidden compose key")

        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["image"] = "example/app:latest"
        self.assert_source_failure(
            document,
            "image must be a required immutable local image ID",
        )

    def test_rejects_unprofiled_or_data_ready_application_service(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["profiles"] = []
        self.assert_source_failure(
            document,
            "webapp_fi_api profiles must be exactly",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["profiles"] = [
            "webapp-fi-activation",
            "webapp-fi-data-ready",
        ]
        self.assert_source_failure(
            document,
            "webapp-fi-data-ready must contain only PostgreSQL and Redis",
        )

    def test_rejects_dependency_cycles_and_incomplete_activation_plane(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["depends_on"][
            "webapp_fi_api"
        ] = {"condition": "service_healthy"}
        self.assert_source_failure(
            document,
            "compose dependency graph contains a cycle",
        )

        document = copy.deepcopy(self.document)
        del document["services"]["webapp_ir_api"]["depends_on"][
            "webapp_ir_dr_tls"
        ]
        self.assert_source_failure(
            document,
            "webapp_ir_api must depend on the complete role-local private plane",
        )

        document = copy.deepcopy(self.document)
        del document["services"]["webapp_fi_effects"]["depends_on"][
            "webapp_fi_api"
        ]
        self.assert_source_failure(
            document,
            "webapp_fi_effects must wait for its role-local healthy API",
        )

    def test_rejects_prepare_order_or_remote_witness_local_fence(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_db_fencing"]["depends_on"] = {
            "webapp_ir_migration": {
                "condition": "service_completed_successfully",
            }
        }
        self.assert_source_failure(
            document,
            "roles-to-migration-to-roles-to-fencing sequence",
        )

        document = copy.deepcopy(self.document)
        fence = document["services"]["webapp_ir_writer_fence"]
        fence["environment"]["WRITER_WITNESS_INTERNAL_URL"] = (
            "${PRODUCTION_SHADOW_WITNESS_URL:?required}"
        )
        self.assert_source_failure(
            document,
            "webapp_ir_writer_fence must retain the exact epoch-1 local standby gate",
        )

    def test_rejects_direct_prepare_mutation_commands(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_db_roles_post_migration"]["command"] = [
            "python",
            "scripts/activate_three_site_database_fencing.py",
        ]
        self.assert_source_failure(
            document,
            "webapp_fi_db_roles_post_migration must default fail closed",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_db_fencing"]["command"] = [
            "python",
            "scripts/activate_three_site_database_fencing.py",
        ]
        self.assert_source_failure(
            document,
            "webapp_ir_db_fencing must default fail closed",
        )

        document = copy.deepcopy(self.document)
        document["services"]["bot_fi_db_roles"]["command"] = [
            "python",
            "scripts/provision_bot_database_roles.py",
        ]
        self.assert_source_failure(
            document,
            "bot_fi_db_roles must default fail closed",
        )

        document = copy.deepcopy(self.document)
        document["services"]["bot_fi_db_fencing"]["depends_on"] = {
            "bot_fi_migration": {"condition": "service_completed_successfully"}
        }
        self.assert_source_failure(
            document,
            "bot_fi_db_fencing must apply only the confirmed fence phase",
        )

    def test_rejects_database_policy_dependency_environment_or_mount_drift(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_db_fencing"]["depends_on"][
            "webapp_fi_db_roles_post_migration"
        ]["condition"] = "service_started"
        self.assert_source_failure(
            document,
            "service_completed_successfully",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_db_roles_post_migration"][
            "environment"
        ]["DATABASE_URL"] = "forbidden"
        self.assert_source_failure(
            document,
            "exact minimal environment and CA mount",
        )

        document = copy.deepcopy(self.document)
        document["services"]["bot_fi_db_fencing"]["volumes"].append(
            "/tmp/foreign-secret:/run/foreign-secret:ro"
        )
        self.assert_source_failure(
            document,
            "exact minimal environment and CA mount",
        )

        document = copy.deepcopy(self.document)
        del document["services"]["webapp_ir_db_roles"]["environment"][
            "THREE_SITE_OBSERVER_DB_PASSWORD"
        ]
        self.assert_source_failure(
            document,
            "webapp_ir_db_roles must create only the exact runtime roles",
        )

    def test_rejects_unpinned_postgres_runtime_or_swap(self):
        for key, value in (
            ("cgroup", "host"),
            ("runtime", "unreviewed"),
            ("shm_size", "32m"),
            ("init", True),
            ("oom_kill_disable", True),
            ("oom_score_adj", 1),
            ("mem_swappiness", 1),
        ):
            with self.subTest(key=key):
                document = copy.deepcopy(self.document)
                document["services"]["webapp_ir_db"][key] = value
                self.assert_source_failure(
                    document,
                    f"webapp_ir_db must pin PostgreSQL {key}=",
                )

        document = copy.deepcopy(self.document)
        document["services"]["bot_fi_db"]["memswap_limit"] = "3g"
        self.assert_source_failure(
            document,
            "bot_fi_db must disable swap by matching memswap_limit to mem_limit",
        )

    def test_rejects_non_loopback_api_and_unscoped_dr_listener(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["ports"] = ["0.0.0.0:9312:8000"]
        self.assert_source_failure(
            document,
            "webapp_fi_api must publish only",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_dr_tls"]["ports"] = ["0.0.0.0:443:443"]
        self.assert_source_failure(
            document,
            "webapp_ir_dr_tls must publish only",
        )

    def test_rejects_background_or_effect_activation_drift(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_api"]["environment"][
            "BACKGROUND_JOBS_ENABLED"
        ] = "false"
        self.assert_source_failure(
            document,
            "webapp_ir_api must use its explicit active/standby background-job gate",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_effects"]["profiles"] = [
            "webapp-fi-activation"
        ]
        self.assert_source_failure(
            document,
            "webapp_fi_effects profiles must be exactly",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["environment"]["BOT_TOKEN"] = (
            "${BOT_TOKEN:?forbidden}"
        )
        self.assert_source_failure(
            document,
            "webapp_fi_api must scrub provider credential BOT_TOKEN",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_effects"]["environment"][
            "TELEGRAM_WEBAPP_VALIDATION_KEY"
        ] = "${TELEGRAM_WEBAPP_VALIDATION_KEY:?forbidden}"
        self.assert_source_failure(
            document,
            "webapp_ir_effects must not receive TELEGRAM_WEBAPP_VALIDATION_KEY",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_api"]["environment"][
            "WEB_PUSH_VAPID_PRIVATE_KEY"
        ] = "${WEB_PUSH_VAPID_PRIVATE_KEY:?required}"
        self.assert_source_failure(
            document,
            "only its effect worker may receive it",
        )

        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_api_acceptance"]["environment"][
            "TELEGRAM_WEBAPP_VALIDATION_KEY"
        ] = ""
        self.assert_source_failure(
            document,
            "invalid derived Telegram WebApp key scope",
        )

    def test_rejects_missing_operation_ownership_label(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_ir_db"].pop("labels")
        self.assert_source_failure(
            document,
            "webapp_ir_db must carry only the exact operation ownership label",
        )

    def test_rejects_shared_capability_egress(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_writer_control"]["networks"] = [
            "webapp_fi",
            "webapp_fi_blob_egress",
        ]
        self.assert_source_failure(
            document,
            "webapp_fi_writer_control must use only its dedicated capability egress",
        )

    def test_rejects_legacy_or_test_identity_in_source(self):
        self.assert_source_failure(
            self.document,
            "compose source contains a legacy, test, or non-production identity",
            source_text=self.source_text + "\n# forbidden environment: staging\n",
        )

    def test_rejects_unscoped_data_and_secret_mounts(self):
        document = copy.deepcopy(self.document)
        document["services"]["webapp_fi_db"]["volumes"] = [
            (
                "${PRODUCTION_SHADOW_DATA_ROOT:"
                "?operation-bound data root is required}"
                "/bot-fi/postgres:/var/lib/postgresql/data"
            )
        ]
        self.assert_source_failure(
            document,
            "webapp_fi_db must bind only its exact canonical PostgreSQL directory",
        )
        self.assertIn(
            "webapp_fi_db must bind only its exact canonical PostgreSQL directory",
            collect_source_failures(document, self.source_text),
        )

        document = copy.deepcopy(self.document)
        document["volumes"] = {"poisoned": {"driver": "local"}}
        self.assert_source_failure(
            document,
            "top-level named volumes are forbidden; all stores must be exact direct binds",
        )

        document = copy.deepcopy(self.document)
        mounts = document["services"]["webapp_ir_dr_tls"]["volumes"]
        document["services"]["webapp_ir_dr_tls"]["volumes"] = [
            entry.replace(
                "${PRODUCTION_SHADOW_SECRET_ROOT:?operation-bound secret root is required}/",
                "/tmp/",
            )
            for entry in mounts
        ]
        self.assert_source_failure(
            document,
            "webapp_ir_dr_tls must mount its operation TLS certificate",
        )

    def test_rejects_project_root_image_and_port_collisions(self):
        cases = {
            "PRODUCTION_SHADOW_PROJECT": "trading_bot",
            "PRODUCTION_SHADOW_PROJECT_ROOT": "/srv/trading-bot",
            "PRODUCTION_SHADOW_DATA_ROOT": "/srv/trading-bot",
            "PRODUCTION_SHADOW_SECRET_ROOT": "/tmp/secrets",
            "PRODUCTION_SHADOW_APP_IMAGE_ID": "example/app:latest",
            "WEBAPP_FI_SHADOW_API_PORT": "8212",
        }
        for key, bad_value in cases.items():
            with self.subTest(key=key):
                values = self.valid_environment()
                values[key] = bad_value
                failures = collect_environment_failures(values, self.source_text)
                self.assertTrue(
                    any(key in failure for failure in failures),
                    "\n".join(failures),
                )

        values = self.valid_environment()
        values["WEBAPP_IR_SHADOW_API_PORT"] = values["WEBAPP_FI_SHADOW_API_PORT"]
        self.assertIn(
            "all shadow host ports must be distinct",
            "\n".join(collect_environment_failures(values, self.source_text)),
        )

    def test_rejects_non_operation_blob_prefix_and_nonproduction_urls(self):
        values = self.valid_environment()
        values["DR_BLOB_OBJECT_PREFIX"] = "shared/blobs"
        values["WEBAPP_IR_PUBLIC_WEBAPP_URL"] = "https://staging.example.invalid"
        failures = "\n".join(
            collect_environment_failures(values, self.source_text)
        )
        self.assertIn("DR_BLOB_OBJECT_PREFIX", failures)
        self.assertIn("WEBAPP_IR_PUBLIC_WEBAPP_URL", failures)

        values = self.valid_environment()
        values["DR_BLOB_OBJECT_ENDPOINT"] = "https://objects.example.invalid"
        values["WEBAPP_FI_PEER_WEBAPP_IR_IP"] = "203.0.113.99"
        failures = "\n".join(
            collect_environment_failures(values, self.source_text)
        )
        self.assertIn("exact reviewed private/versioned Arvan", failures)
        self.assertIn(
            "WEBAPP_FI_PEER_WEBAPP_IR_IP must match the canonical",
            failures,
        )

    def test_dr_configuration_uses_runtime_list_shapes_and_exact_pairs(self):
        values = self.valid_environment()
        values["WEBAPP_FI_DR_PEERS_JSON"] = (
            '{"bot_fi":"https://bot-fi-dr.production.internal:9442"}'
        )
        failures = "\n".join(
            collect_environment_failures(values, self.source_text)
        )
        self.assertIn(
            "WEBAPP_FI_DR_PEERS_JSON must pass the real runtime "
            "sparse-topology list parser",
            failures,
        )

        values = self.valid_environment()
        pairwise = json.loads(values["WEBAPP_FI_DR_PAIRWISE_KEYS_JSON"])
        pairwise.pop()
        values["WEBAPP_FI_DR_PAIRWISE_KEYS_JSON"] = json.dumps(pairwise)
        failures = "\n".join(
            collect_environment_failures(values, self.source_text)
        )
        self.assertIn(
            "WEBAPP_FI_DR_PAIRWISE_KEYS_JSON must contain exactly one key "
            "for every required directed pair",
            failures,
        )

        values = self.valid_environment()
        ir_pairwise = json.loads(values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"])
        ir_pairwise[0]["secret"] = "z" * 32
        values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"] = json.dumps(ir_pairwise)
        failures = "\n".join(
            collect_environment_failures(values, self.source_text)
        )
        self.assertIn(
            "must share identical keys for their overlapping directed pairs",
            failures,
        )

    def test_rejects_unsafe_postgres_identifiers_and_unencoded_passwords(self):
        cases = {
            "BOT_FI_POSTGRES_USER": "quoted-owner",
            "WEBAPP_FI_POSTGRES_DB": "MixedCase",
            "WEBAPP_IR_POSTGRES_USER": "owner;drop",
            "WEBAPP_IR_APP_DB_PASSWORD": "contains@dsn-delimiter",
            "BOT_FI_POSTGRES_PASSWORD": "contains/slash",
            "WEBAPP_FI_OBSERVER_DB_PASSWORD": "contains%escape",
        }
        for key, unsafe_value in cases.items():
            with self.subTest(key=key):
                values = self.valid_environment()
                values[key] = unsafe_value
                failures = collect_environment_failures(
                    values,
                    self.source_text,
                )
                self.assertTrue(
                    any(key in failure for failure in failures),
                    "\n".join(failures),
                )
                self.assertNotIn(unsafe_value, "\n".join(failures))

    def test_api_runtime_env_requires_shared_derived_key_without_bot_token(self):
        values = self.valid_environment()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            values["PRODUCTION_SHADOW_SECRET_ROOT"] = str(root)
            self.write_api_runtime_envs(root, values)
            validate_api_runtime_envs(values)

            fi_path = root / "webapp-fi/runtime.env.api"
            with fi_path.open("a", encoding="utf-8") as stream:
                stream.write("BOT_TOKEN=must-not-cross\n")
            with self.assertRaisesRegex(
                ProductionShadowComposeError,
                "Telegram executor-only fields",
            ):
                validate_api_runtime_envs(values)

    def test_api_runtime_env_rejects_mismatched_validation_key(self):
        values = self.valid_environment()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            values["PRODUCTION_SHADOW_SECRET_ROOT"] = str(root)
            self.write_api_runtime_envs(root, values)
            ir_path = root / "webapp-ir/runtime.env.api"
            text = ir_path.read_text(encoding="utf-8")
            ir_path.write_text(
                text.replace("e" * 64, "f" * 64),
                encoding="utf-8",
            )
            ir_path.chmod(0o600)
            with self.assertRaisesRegex(
                ProductionShadowComposeError,
                "lacks a valid derived",
            ):
                validate_api_runtime_envs(values)

    def test_api_runtime_env_rejects_vapid_private_key(self):
        values = self.valid_environment()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            values["PRODUCTION_SHADOW_SECRET_ROOT"] = str(root)
            self.write_api_runtime_envs(root, values)
            fi_path = root / "webapp-fi/runtime.env.api"
            with fi_path.open("a", encoding="utf-8") as stream:
                stream.write("WEB_PUSH_VAPID_PRIVATE_KEY=must-not-cross\n")
            with self.assertRaisesRegex(
                ProductionShadowComposeError,
                "must not receive WEB_PUSH_VAPID_PRIVATE_KEY",
            ):
                validate_api_runtime_envs(values)

    def test_failure_messages_do_not_echo_secret_values(self):
        values = self.valid_environment()
        sentinel = "do-not-echo-this-secret"
        values["WEBAPP_FI_POSTGRES_PASSWORD"] = sentinel
        values["PRODUCTION_SHADOW_RELEASE_ROOT"] = "/wrong"
        with self.assertRaises(ProductionShadowComposeError) as raised:
            verify_contract(
                document=self.document,
                source_text=self.source_text,
                values=values,
            )
        self.assertNotIn(sentinel, str(raised.exception))

    def test_pristine_redis_gate_rejects_any_legacy_state(self):
        values = self.valid_environment()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            values["PRODUCTION_SHADOW_DATA_ROOT"] = str(root)
            for role in ("bot-fi", "webapp-fi", "webapp-ir"):
                role_root = root / role
                role_root.mkdir(mode=0o700)
                role_root.chmod(0o700)
                target = role_root / "redis"
                target.mkdir(mode=0o700)
                target.chmod(0o700)
            validate_pristine_redis_targets(values)

            target.chmod(0o750)
            with self.assertRaisesRegex(
                ProductionShadowComposeError,
                "mode-0700",
            ):
                validate_pristine_redis_targets(values)
            target.chmod(0o700)

            legacy = root / "webapp-ir/redis/dump.rdb"
            legacy.write_bytes(b"legacy")
            with self.assertRaisesRegex(
                ProductionShadowComposeError,
                "rollback evidence only",
            ):
                validate_pristine_redis_targets(values)

            real_root = root / "real-operation"
            real_root.mkdir(mode=0o700)
            real_root.chmod(0o700)
            linked_root = root / "linked-operation"
            linked_root.symlink_to(real_root, target_is_directory=True)
            values["PRODUCTION_SHADOW_DATA_ROOT"] = str(linked_root)
            with self.assertRaisesRegex(
                ProductionShadowComposeError,
                "without following symlinks",
            ):
                validate_pristine_redis_targets(values)

    def test_restore_tools_have_exact_role_binds(self):
        data_root = (
            "${PRODUCTION_SHADOW_DATA_ROOT:"
            "?operation-bound data root is required}"
        )
        for role in ("bot_fi", "webapp_fi", "webapp_ir"):
            role_path = role.replace("_", "-")
            expected = {
                (
                    f"{data_root}/restore-input/{role_path}:"
                    "/run/restore-input:ro"
                ),
                (
                    f"{data_root}/{role_path}/uploads:"
                    "/run/restore-target/uploads"
                ),
                (
                    f"{data_root}/{role_path}/audit:"
                    "/run/restore-target/audit"
                ),
            }
            with self.subTest(role=role):
                self.assertEqual(
                    set(
                        self.document["services"][
                            f"{role}_restore_tool"
                        ].get("volumes", [])
                    ),
                    expected,
                )

                swapped = copy.deepcopy(self.document)
                swapped["services"][f"{role}_restore_tool"]["volumes"][1] = (
                    f"{data_root}/wrong-role/uploads:"
                    "/run/restore-target/uploads"
                )
                self.assert_source_failure(
                    swapped,
                    f"{role}_restore_tool must bind only its exact restore input",
                )

    def test_restore_tools_reject_redis_and_legacy_source_mounts(self):
        data_root = (
            "${PRODUCTION_SHADOW_DATA_ROOT:"
            "?operation-bound data root is required}"
        )
        for role in ("bot_fi", "webapp_fi", "webapp_ir"):
            service_name = f"{role}_restore_tool"
            role_path = role.replace("_", "-")

            with self.subTest(role=role, source="redis"):
                redis_mount = copy.deepcopy(self.document)
                redis_mount["services"][service_name]["volumes"].append(
                    f"{data_root}/{role_path}/redis:/run/restore-target/redis"
                )
                self.assert_source_failure(
                    redis_mount,
                    f"{service_name} must bind only its exact restore input",
                )

            with self.subTest(role=role, source="legacy"):
                legacy_mount = copy.deepcopy(self.document)
                legacy_mount["services"][service_name]["volumes"].append(
                    "/srv/trading-bot/current/uploads:/run/legacy-input:ro"
                )
                self.assert_source_failure(
                    legacy_mount,
                    f"{service_name} must bind only its exact restore input",
                )

    def test_local_runner_delegates_to_identity_bounded_execution(self):
        arguments = ["/usr/bin/docker", "version"]
        environment = {"PATH": "/usr/bin:/bin"}
        bounded_result = MODULE.BoundedCommandResult(
            returncode=0,
            stdout=b"ok",
            stderr=b"",
        )
        with mock.patch.object(
            MODULE,
            "_bounded_command",
            return_value=bounded_result,
        ) as bounded:
            result = MODULE._run_bounded_local_command(
                arguments,
                timeout=7,
                environment=environment,
                stdout_limit=123,
                stderr_limit=456,
            )

        self.assertIs(result, bounded_result)
        bounded.assert_called_once_with(
            arguments,
            timeout=7,
            env=environment,
            stdout_limit=123,
            stderr_limit=456,
        )

    def test_local_runner_maps_bounded_failure_but_not_baseexception(self):
        arguments = ["/usr/bin/docker", "version"]
        environment = {"PATH": "/usr/bin:/bin"}
        with (
            mock.patch.object(
                MODULE,
                "_bounded_command",
                side_effect=MODULE.BoundedCommandError("timed out"),
            ),
            self.assertRaisesRegex(
                ProductionShadowComposeError,
                "failed closed",
            ),
        ):
            MODULE._run_bounded_local_command(
                arguments,
                timeout=1,
                environment=environment,
            )
        with (
            mock.patch.object(
                MODULE,
                "_bounded_command",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            MODULE._run_bounded_local_command(
                arguments,
                timeout=1,
                environment=environment,
            )


if __name__ == "__main__":
    unittest.main()
