from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import prepare_production_private_primary_manifest as preparer


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts/production_deploy_online.sh"


def run_sourced(body: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f'source "$1"\n{body}',
            "private-primary-manifest-gate-test",
            str(DEPLOY_SCRIPT),
            *arguments,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TZ": "UTC",
        },
    )


class ProductionPrivatePrimaryManifestDeployGateTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root ownership contract requires a root test process")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="private-primary-deploy-gate-"
        )
        self.temporary_root = Path(self.temporary.name)
        self.approved_root = self.temporary_root / "release-control"
        self.approved_root.mkdir(mode=0o700)
        self.runtime_source = self.temporary_root / "immutable.production.env"
        self.runtime_source.write_text(
            "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY\n",
            encoding="utf-8",
        )
        self.runtime_source.chmod(0o600)
        self.source = self.approved_root / "online.source.env"
        self.manifest = self.approved_root / "online.private-primary.env"
        self.receipt = self.approved_root / "online.private-primary.receipt.json"
        self.source.write_text(self._source_manifest(), encoding="utf-8")
        self.source.chmod(0o600)
        with mock.patch.object(preparer, "APPROVED_ROOT", self.approved_root):
            preparer._prepare_locked(
                source=self.source,
                output=self.manifest,
                receipt=self.receipt,
                expected_source_sha256=sha256(self.source.read_bytes()).hexdigest(),
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _source_manifest(self) -> str:
        values = {
            "LOCAL_PROJECT_DIR": "/root/trading-bot/trading_bot",
            "LOCAL_FRONTEND_DIR": "/root/trading-bot/trading_bot/frontend",
            "LOCAL_DIST_DIR": "/root/trading-bot/trading_bot/mini_app_dist",
            "FOREIGN_PUBLIC_IP": "192.0.2.10",
            "FOREIGN_PUBLIC_DOMAIN": "foreign.invalid",
            "FOREIGN_COMPOSE_PROJECT_NAME": "trading_bot",
            "IRAN_HOST": "192.0.2.20",
            "IRAN_SSH_USER": "root",
            "IRAN_SSH_PORT": "22",
            "IRAN_PROJECT_DIR": "/srv/trading-bot/current",
            "IRAN_DEPLOY_BASE_DIR": "/srv/trading-bot",
            "IRAN_PUBLIC_IP": "192.0.2.20",
            "IRAN_APP_DOMAIN": "app.invalid",
            "IRAN_PUBLIC_DOMAIN": "app.invalid",
            "IRAN_CERTBOT_EMAIL": "ops@example.invalid",
            "RUNTIME_ENV_SOURCE_PATH": str(self.runtime_source),
            "FOREIGN_RUNTIME_ENV_PATH": "/root/secure-envs/trading-bot/runtime/foreign.production.env",
            "IRAN_RUNTIME_ENV_PATH": "/root/secure-envs/trading-bot/runtime/iran.production.env",
            "ALLOW_PROJECT_ENV_SOURCE": "0",
            "IRAN_ALLOW_DIRTY_RELEASE": "0",
            "IRAN_ALLOW_NON_MAIN_RELEASE": "0",
            "IRAN_ALLOW_RELEASE_BRANCH_DRIFT": "0",
            "IRAN_SKIP_FOREIGN_DEPLOY": "0",
            "PRODUCTION_RELEASE_BRANCH": "main",
        }
        values.update(preparer.PRIVATE_PRIMARY_MANIFEST_UPDATES)
        return "".join(f"{key}={value}\n" for key, value in values.items())

    def _gate_body(self) -> str:
        return r'''
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_APPROVED_ROOT="$2"
MANIFEST_PATH="$3"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_EXPECTED_SHA256="$4"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_PATH="$5"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_SHA256="$6"
verify_private_primary_deploy_manifest_before_source
printf '%s\n' "$PRODUCTION_PRIVATE_PRIMARY_MANIFEST_ATTESTATION_VERIFIED"
'''

    def _run_gate(
        self,
        *,
        manifest_sha256: str | None = None,
        receipt_sha256: str | None = None,
        receipt: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return run_sourced(
            self._gate_body(),
            str(self.approved_root),
            str(self.manifest),
            manifest_sha256 or sha256(self.manifest.read_bytes()).hexdigest(),
            str(receipt or self.receipt),
            receipt_sha256 or sha256((receipt or self.receipt).read_bytes()).hexdigest(),
        )

    def _rewrite_receipt(self, key: str, value: object) -> None:
        payload = json.loads(self.receipt.read_text(encoding="utf-8"))
        payload[key] = value
        self.receipt.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        self.receipt.chmod(0o600)

    def test_exact_preparation_receipt_authorizes_manifest_before_source(self) -> None:
        result = self._run_gate()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "1")
        self.assertNotIn("RUNTIME_ENV_SOURCE_PATH", result.stdout + result.stderr)

    def test_load_manifest_sources_attested_inode_and_restores_original_path(self) -> None:
        result = run_sourced(
            r'''
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_APPROVED_ROOT="$2"
MANIFEST_PATH="$3"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_EXPECTED_SHA256="$4"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_PATH="$5"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_SHA256="$6"
load_manifest
printf '%s|%s|%s\n' "$MANIFEST_PATH" "$PRODUCTION_PRIVATE_PRIMARY_MANIFEST_ATTESTATION_VERIFIED" "${PRODUCTION_PRIVATE_PRIMARY_MANIFEST_SOURCE_FD:-closed}"
''',
            str(self.approved_root),
            str(self.manifest),
            sha256(self.manifest.read_bytes()).hexdigest(),
            str(self.receipt),
            sha256(self.receipt.read_bytes()).hexdigest(),
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            result.stdout.strip().splitlines()[-1],
            f"{self.manifest}|1|closed",
        )

    def test_attested_manifest_cannot_authorize_legacy_runtime_source(self) -> None:
        self.runtime_source.write_text(
            "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY\n",
            encoding="utf-8",
        )
        result = run_sourced(
            r'''
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_APPROVED_ROOT="$2"
MANIFEST_PATH="$3"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_EXPECTED_SHA256="$4"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_PATH="$5"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_RECEIPT_SHA256="$6"
load_manifest
''',
            str(self.approved_root),
            str(self.manifest),
            sha256(self.manifest.read_bytes()).hexdigest(),
            str(self.receipt),
            sha256(self.receipt.read_bytes()).hexdigest(),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot authorize a non-PRIVATE_PRIMARY", result.stderr)

    def test_private_primary_without_attestation_is_rejected_before_source(self) -> None:
        marker = self.temporary_root / "manifest-was-sourced"
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8")
            + f"touch {marker}\n",
            encoding="utf-8",
        )
        self.manifest.chmod(0o600)

        result = run_sourced(
            r'''
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_APPROVED_ROOT="$2"
MANIFEST_PATH="$3"
load_manifest
''',
            str(self.approved_root),
            str(self.manifest),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("before the deploy manifest is sourced", result.stderr)
        self.assertFalse(marker.exists())

    def test_legacy_runtime_source_keeps_existing_no_attestation_behavior(self) -> None:
        self.runtime_source.write_text(
            "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY\n",
            encoding="utf-8",
        )
        result = run_sourced(
            r'''
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_APPROVED_ROOT="$2"
MANIFEST_PATH="$3"
verify_private_primary_deploy_manifest_before_source
printf '%s\n' "$PRODUCTION_PRIVATE_PRIMARY_MANIFEST_ATTESTATION_VERIFIED"
''',
            str(self.approved_root),
            str(self.manifest),
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "0")

    def test_partial_or_incorrect_external_binding_fails_closed(self) -> None:
        partial = run_sourced(
            r'''
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_APPROVED_ROOT="$2"
MANIFEST_PATH="$3"
PRODUCTION_PRIVATE_PRIMARY_MANIFEST_EXPECTED_SHA256="$4"
verify_private_primary_deploy_manifest_before_source
''',
            str(self.approved_root),
            str(self.manifest),
            sha256(self.manifest.read_bytes()).hexdigest(),
        )
        self.assertNotEqual(partial.returncode, 0)

        incorrect = self._run_gate(manifest_sha256="0" * 64)
        self.assertNotEqual(incorrect.returncode, 0)

    def test_receipt_contract_fields_are_independently_verified(self) -> None:
        original = self.receipt.read_bytes()
        mutations: tuple[tuple[str, object], ...] = (
            ("schema", "wrong-schema"),
            ("status", "FAILED"),
            ("action", "WRONG_ACTION"),
            ("output_sha256", "0" * 64),
            ("output_path_sha256", "0" * 64),
            ("receipt_path_sha256", "0" * 64),
            ("manifest_schema_sha256", "0" * 64),
            ("tool_sha256", "0" * 64),
            ("source_path_sha256", "not-a-digest"),
            ("normalized_keys", ["PRODUCTION_COIN_INFERENCE_RELAY_ENABLED"]),
            ("source_preserved_by_tool", False),
            ("secrets_disclosed", True),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                self.receipt.write_bytes(original)
                self.receipt.chmod(0o600)
                self._rewrite_receipt(key, value)
                result = self._run_gate()
                self.assertNotEqual(result.returncode, 0)

    def test_manifest_receipt_security_and_cas_are_fail_closed(self) -> None:
        self.receipt.chmod(0o640)
        insecure = self._run_gate()
        self.assertNotEqual(insecure.returncode, 0)
        self.receipt.chmod(0o600)

        expected_manifest = sha256(self.manifest.read_bytes()).hexdigest()
        self.manifest.write_bytes(self.manifest.read_bytes() + b"# changed\n")
        self.manifest.chmod(0o600)
        changed = self._run_gate(manifest_sha256=expected_manifest)
        self.assertNotEqual(changed.returncode, 0)

    def test_load_manifest_orders_attestation_before_source_and_mode_recheck(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        load = source.split("load_manifest() {", 1)[1].split("\n}", 1)[0]

        self.assertLess(
            load.index("verify_private_primary_deploy_manifest_before_source"),
            load.index('source "$MANIFEST_PATH"'),
        )
        self.assertLess(
            load.index('source "$MANIFEST_PATH"'),
            load.index("verify_private_primary_manifest_mode_after_source"),
        )
        info = self.manifest.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)


if __name__ == "__main__":
    unittest.main()
