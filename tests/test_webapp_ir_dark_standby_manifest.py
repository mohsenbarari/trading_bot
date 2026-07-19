from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


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
        "SOURCE_PROJECT_DIR": "/srv/source",
        "SOURCE_RUNTIME_ENV": "/root/secure/source.env",
        "WEBAPP_FI_HOST": "192.0.2.10",
        "WEBAPP_FI_SSH_USER": "root",
        "WEBAPP_FI_SSH_PORT": "22",
        "WEBAPP_FI_SSH_KEY": "/root/.ssh/source",
        "WEBAPP_FI_PROJECT_DIR": "/srv/app/current",
        "WA_IR_HOST": "192.0.2.20",
        "WA_IR_SSH_USER": "ubuntu",
        "WA_IR_SSH_PORT": "22",
        "WA_IR_SSH_KEY": "/root/.ssh/target",
        "WA_IR_PROJECT_DIR": "/srv/app/current",
        "WA_IR_DEPLOY_BASE_DIR": "/srv/app",
        "OBJECT_STORAGE_ENDPOINT": "https://s3.example.invalid",
        "OBJECT_STORAGE_REGION": "region-1",
        "OBJECT_STORAGE_PREFIX": "dark-standby",
        "OBJECT_STORAGE_CREDENTIAL_FILE": "/root/secure/s3.env",
        "OBJECT_URL_TTL_SECONDS": "900",
        "AGE_IDENTITY_FILE": "/root/secure/identity.txt",
        "AGE_RECIPIENT_FILE": "/root/secure/recipient.txt",
        "LOCAL_ARTIFACT_DIR": "/srv/artifacts",
        "REMOTE_BACKUP_DIR": "/srv/backups",
    })
    return values


class DarkStandbyManifestTests(unittest.TestCase):
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
        self.assertTrue(any("HTTPS" in item for item in failures))
        self.assertTrue(any("between 60 and 900" in item for item in failures))

    def test_same_source_and_target_host_is_rejected(self) -> None:
        values = valid_values()
        values["WA_IR_HOST"] = values["WEBAPP_FI_HOST"]
        failures, _ = MODULE.validate(values, check_files=False)
        self.assertTrue(any("different physical hosts" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
