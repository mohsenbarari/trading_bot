from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_webapp_ir_dark_standby_manifest",
    ROOT / "scripts" / "verify_webapp_ir_dark_standby_manifest.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_values() -> dict[str, str]:
    values = dict(MODULE.REQUIRED_VALUES)
    values.update({
        "SOURCE_RELEASE_SHA": "a" * 40,
        "SOURCE_TREE_SHA": "b" * 40,
        "SOURCE_PROJECT_DIR": "/srv/source",
        "SOURCE_RUNTIME_ENV": "/root/secure/source.env",
        "RELEASE_ARTIFACT_PATH": "/srv/artifacts/release.tar",
        "RELEASE_ARTIFACT_SHA256": "c" * 64,
        **MODULE.EXPECTED_TOPOLOGY,
        "WEBAPP_FI_SSH_USER": "root",
        "WEBAPP_FI_SSH_PORT": "22",
        "WEBAPP_FI_SSH_KEY": "/root/.ssh/source",
        "WEBAPP_FI_PROJECT_DIR": "/srv/app/current",
        "WA_IR_SSH_USER": "ubuntu",
        "WA_IR_SSH_PORT": "22",
        "WA_IR_SSH_KEY": "/root/.ssh/target",
        "WA_IR_PROJECT_DIR": "/srv/app/current",
        "WA_IR_DEPLOY_BASE_DIR": "/srv/app",
        "OBJECT_STORAGE_CREDENTIAL_FILE": "/root/secure/s3.env",
        "OBJECT_URL_TTL_SECONDS": "900",
        "AGE_IDENTITY_FILE": "/root/secure/identity.txt",
        "AGE_RECIPIENT_FILE": "/root/secure/recipient.txt",
        "LOCAL_ARTIFACT_DIR": "/srv/artifacts",
        "REMOTE_BACKUP_DIR": "/srv/backups",
        "RESTORE_OPERATION_ID": "12345678-1234-4234-8234-123456789abc",
        "TARGET_DB_VOLUME_NAME": "trading_bot_dark_ir_postgres_data",
    })
    return values


class DarkStandbyManifestTests(unittest.TestCase):
    def test_git_identity_probe_is_bounded_and_ignores_ambient_config(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="a" * 40, stderr="")
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertIs(MODULE.run_git(Path("/srv/source"), "rev-parse", "HEAD"), completed)
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(argv[0], "/usr/bin/git")
        self.assertIn("core.fsmonitor=false", argv)
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["HOME"], "/nonexistent")
        self.assertEqual(kwargs["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_valid_data_only_manifest_passes_without_file_probe(self) -> None:
        failures, warnings = MODULE.validate(valid_values(), check_files=False)
        self.assertEqual(failures, [])
        self.assertEqual(
            warnings,
            ["writer witness is disabled; this host must remain dark and non-writer"],
        )

    def test_writer_or_public_activation_is_rejected(self) -> None:
        values = valid_values()
        values["START_APP_SERVICE"] = "true"
        values["ALLOW_CDN_MUTATION"] = "true"
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("START_APP_SERVICE" in item for item in failures))
        self.assertTrue(any("ALLOW_CDN_MUTATION" in item for item in failures))

    def test_unencrypted_or_long_lived_transport_is_rejected(self) -> None:
        values = valid_values()
        values["PAYLOAD_ENCRYPTION"] = "none"
        values["OBJECT_STORAGE_ENDPOINT"] = "http://s3.example.invalid"
        values["OBJECT_URL_TTL_SECONDS"] = "3600"
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("PAYLOAD_ENCRYPTION" in item for item in failures))
        self.assertTrue(any("OBJECT_STORAGE_ENDPOINT" in item for item in failures))
        self.assertTrue(any("between 60 and 900" in item for item in failures))

    def test_same_source_and_target_host_is_rejected(self) -> None:
        values = valid_values()
        values["WA_IR_HOST"] = values["WEBAPP_FI_HOST"]
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("different physical hosts" in item for item in failures))
        self.assertTrue(any("approved topology" in item for item in failures))

    def test_production_and_test_domain_boundaries_cannot_be_mixed(self) -> None:
        values = valid_values()
        values["FAILOVER_TEST_ROOT_DOMAIN"] = "gold-trade.ir"
        values["FAILOVER_TEST_PUBLIC_HOST"] = "app.gold-trade.ir"
        values["ARVAN_CDN_CONFIGURED_ROOT_DOMAIN"] = "gold-trade.ir"
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("must be different" in item for item in failures))
        self.assertTrue(any("must not include the production" in item for item in failures))

    def test_test_public_host_must_belong_to_test_root(self) -> None:
        values = valid_values()
        values["FAILOVER_TEST_PUBLIC_HOST"] = "app.example.invalid"
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("must be below" in item for item in failures))

    def test_wrong_role_host_or_object_storage_location_is_rejected(self) -> None:
        values = valid_values()
        values["WA_IR_HOST"] = "192.0.2.99"
        values["OBJECT_STORAGE_ENDPOINT"] = "https://attacker.invalid"
        values["OBJECT_STORAGE_REGION"] = "wrong-region"
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("approved topology" in item for item in failures))
        self.assertTrue(any("OBJECT_STORAGE_ENDPOINT" in item for item in failures))
        self.assertTrue(any("OBJECT_STORAGE_REGION" in item for item in failures))

    def test_release_artifact_and_operation_identity_are_mandatory(self) -> None:
        values = valid_values()
        values["RELEASE_ARTIFACT_SHA256"] = "short"
        values["RESTORE_OPERATION_ID"] = "not-a-uuid"
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("RELEASE_ARTIFACT_SHA256" in item for item in failures))
        self.assertTrue(any("RESTORE_OPERATION_ID" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
