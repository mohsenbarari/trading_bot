"""Fail-closed contracts for PRIVATE_PRIMARY control-release preparation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from scripts import prepare_private_primary_control_release as preparer
from scripts import verify_production_private_primary_promotion as verifier
from scripts.crypt_market_pipeline_backup import generate_key


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "production_deploy_online.sh"
SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64
SIGNATURE = "d" * 64
FORBIDDEN = (
    "run_release",
    "deploy_foreign",
    "deploy_iran",
    "docker compose up",
    "docker compose down",
    "promote_production_private_primary_product",
    "update_production_coin_inference_source",
    "authorize-captures",
    "start-captures",
    "PRIMARY_COMMITTED",
    "run_private_primary_choreography_controller execute",
    "CAS",
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: str | bytes, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        payload = payload.encode()
    path.write_bytes(payload)
    os.chmod(path, mode)
    return path


def _json(path: Path, document: dict[str, object]) -> Path:
    return _write(
        path,
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
    )


class PreparePrivatePrimaryControlReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="pp-prep-", dir="/root"))
        os.chmod(self.workspace, 0o700)
        self.payload = self.workspace / "payload"
        self.payload.mkdir(mode=0o700)
        self.control = _write(self.payload / "control.txt", "exact-control\n")
        self.manifest = _write(
            self.payload / "control-payload.sha256",
            f"{_digest(self.control)}  ./control.txt\n",
        )
        self.bot_env = _write(
            self.workspace / "inputs" / "bot.release.env",
            (
                f"MARKET_BOT_DATA_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}\n"
                f"MARKET_PRODUCT_SNAPSHOT_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}/snapshots\n"
                f"MARKET_PIPELINE_RELEASE_SHA={SHA}\n"
                f"MARKET_PIPELINE_IMAGE={IMAGE}\n"
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
            ),
        )
        self.web_env = _write(
            self.workspace / "inputs" / "web.release.env",
            (
                f"MARKET_WEB_DATA_ROOT={preparer.CANONICAL_WEB_DATA_ROOT}\n"
                f"MARKET_PRODUCT_SNAPSHOT_ROOT={preparer.CANONICAL_WEB_DATA_ROOT}/snapshots\n"
                f"MARKET_PIPELINE_RELEASE_SHA={SHA}\n"
                f"MARKET_PIPELINE_IMAGE={IMAGE}\n"
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
            ),
        )
        self.image_receipt = _json(
            self.workspace / "inputs" / "image.json",
            {
                "schema": "market_pipeline_image_release/1.0",
                "release_sha": SHA,
                "release_tree": TREE,
                "image_id": IMAGE,
                "input_signature": SIGNATURE,
                "secrets_disclosed": False,
            },
        )
        self.pair_receipt = _json(
            self.workspace / "inputs" / "pair.json",
            {
                "schema": "market_pipeline_primary_release_pair/1.0",
                "release_sha": SHA,
                "release_tree": TREE,
                "image_id": IMAGE,
                "feed_mode": "PRIVATE_PRIMARY",
                "product_authority_changed": False,
                "secrets_disclosed": False,
            },
        )
        self.base = self.workspace / "releases"
        self.receipt = self.workspace / "receipts" / "install.json"
        (self.workspace / "keys").mkdir(mode=0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def extras(self) -> dict[str, Path]:
        return {
            "bot.release.env": self.bot_env,
            "web.release.env": self.web_env,
            "market-pipeline-image-prebuild-receipt.json": self.image_receipt,
            "market-pipeline-release-pair-receipt.json": self.pair_receipt,
            "control-payload.sha256": self.manifest,
        }

    def install(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "base_dir": self.base,
            "release_sha": SHA,
            "release_tree": TREE,
            "host_role": "bot",
            "payload_dir": self.payload,
            "extras": self.extras(),
            "image_id": IMAGE,
            "image_input_signature": SIGNATURE,
            "receipt": self.receipt,
        }
        values.update(overrides)
        return preparer.install_control_release(**values)  # type: ignore[arg-type]

    def test_cli_accepts_confirm_after_subcommand(self) -> None:
        source = self.workspace / "inputs" / "bot.source.env"
        _write(
            source,
            (
                f"MARKET_BOT_DATA_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}\n"
                f"MARKET_PRODUCT_SNAPSHOT_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}/snapshots\n"
            ),
        )
        self.assertEqual(
            preparer.main(
                [
                    "validate-topology-source",
                    "--confirm",
                    preparer.CONFIRMATION,
                    "--role",
                    "bot",
                    "--source",
                    str(source),
                    "--repository-root",
                    str(REPO_ROOT),
                ]
            ),
            0,
        )

    def test_help_lists_the_official_command(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("prepare-private-primary-control-release", source)
        self.assertIn("run_prepare_private_primary_control_release() {", source)

    def test_prepare_body_never_calls_forbidden_mutations(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        body = source.split("run_prepare_private_primary_control_release() {", 1)[1]
        body = body.split("\nrun_private_primary_choreography_controller() {", 1)[0]
        for token in FORBIDDEN:
            self.assertNotIn(token, body, token)
        self.assertNotIn("PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED=1", body)
        self.assertNotIn("PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=1", body)
        self.assertNotIn("PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED=1", body)
        self.assertNotIn("PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED=1", body)
        self.assertNotIn("PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=1", body)
        self.assertNotIn("PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=", body)
        self.assertLess(
            body.index('--confirm "render-market-pipeline-private-primary"'),
            body.index("render-pair"),
        )
        self.assertIn("--control-manifest", body)
        self.assertIn("PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST", body)
        self.assertIn("services_started", body)
        self.assertIn("database_mutated", body)
        self.assertIn("authority_changed", body)
        self.assertIn("capture_owner_changed", body)

    def test_queue_cutover_receipt_is_not_minted(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        body = source.split("run_prepare_private_primary_control_release() {", 1)[1]
        body = body.split("\nrun_private_primary_choreography_controller() {", 1)[0]
        self.assertNotIn("TELEGRAM_QUEUE_PRODUCTION_PHASE_RECEIPT", body)
        self.assertNotIn("verify_queue_cutover_deploy_authority", body)

    def test_evidence_age_ceiling_is_unchanged(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("PRODUCTION_RELEASE_EVIDENCE_MAXIMUM_AGE_SECONDS=3600", source)
        self.assertEqual(preparer.MAXIMUM_RECEIPT_AGE_SECONDS, 3600)

    def test_historical_flags_stay_off(self) -> None:
        preparer.validate_historical_flags({key: "0" for key in preparer.HISTORICAL_FLAGS})
        with self.assertRaises(preparer.PrepareError):
            preparer.validate_historical_flags(
                {**{key: "0" for key in preparer.HISTORICAL_FLAGS}, preparer.HISTORICAL_FLAGS[0]: "1"}
            )

    def test_sibling_control_manifest_is_installed(self) -> None:
        sibling = _write(
            self.workspace / "inputs" / "control-payload.sha256",
            self.manifest.read_text(encoding="utf-8"),
        )
        self.manifest.unlink()
        extras = self.extras()
        extras["control-payload.sha256"] = sibling
        payload = self.install(extras=extras)
        installed = self.base / SHA / "control-payload.sha256"
        self.assertTrue(installed.is_file())
        self.assertEqual(installed.read_bytes(), sibling.read_bytes())
        self.assertFalse(payload["idempotent_reuse"])

    def test_cli_install_binds_explicit_sibling_manifest(self) -> None:
        sibling = _write(
            self.workspace / "inputs" / "control-payload.sha256",
            self.manifest.read_text(encoding="utf-8"),
        )
        self.manifest.unlink()
        argv = [
            "install-control-release",
            "--confirm",
            preparer.CONFIRMATION,
            "--base-dir",
            str(self.base),
            "--release-sha",
            SHA,
            "--release-tree",
            TREE,
            "--host-role",
            "bot",
            "--payload-dir",
            str(self.payload),
            "--control-manifest",
            str(sibling),
            "--bot-env",
            str(self.bot_env),
            "--web-env",
            str(self.web_env),
            "--image-receipt",
            str(self.image_receipt),
            "--pair-receipt",
            str(self.pair_receipt),
            "--image-id",
            IMAGE,
            "--image-input-signature",
            SIGNATURE,
            "--receipt",
            str(self.receipt),
        ]
        self.assertEqual(preparer.main(argv), 0)
        installed = self.base / SHA / "control-payload.sha256"
        self.assertEqual(installed.read_bytes(), sibling.read_bytes())

    def test_atomic_install_is_idempotent_when_exact(self) -> None:
        first = self.install()
        release = self.base / SHA
        self.assertTrue(release.is_dir())
        self.assertEqual(stat.S_IMODE(release.stat().st_mode), 0o700)
        self.assertFalse(release.is_symlink())
        self.assertEqual(first["idempotent_reuse"], False)
        self.assertEqual(first["services_started"], False)
        self.assertEqual(first["database_mutated"], False)
        self.assertEqual(first["authority_changed"], False)
        self.assertEqual(first["capture_owner_changed"], False)
        self.assertEqual(first["secrets_disclosed"], False)
        second = self.install(receipt=self.workspace / "receipts" / "install-2.json")
        self.assertEqual(second["idempotent_reuse"], True)
        self.assertEqual((release / "control.txt").read_bytes(), self.control.read_bytes())

    def test_existing_divergent_release_is_fail_closed(self) -> None:
        self.install()
        (self.base / SHA / "control.txt").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(preparer.PrepareError, "existing_release_digest_mismatch"):
            self.install(receipt=self.workspace / "receipts" / "mismatch.json")
        self.assertEqual((self.base / SHA / "control.txt").read_text(encoding="utf-8"), "tampered\n")

    def test_payload_symlink_is_rejected(self) -> None:
        (self.payload / "link").symlink_to(self.control)
        with self.assertRaisesRegex(preparer.PrepareError, "payload_symlink_forbidden"):
            self.install()

    def test_world_writable_owner_mode_is_rejected(self) -> None:
        self.base.mkdir(mode=0o777)
        os.chmod(self.base, 0o777)
        with self.assertRaisesRegex(preparer.PrepareError, "release_base_owner_mode_invalid"):
            self.install()
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o777)

    def test_existing_0755_base_is_tightened_without_deleting_children(self) -> None:
        self.base.mkdir(mode=0o755)
        os.chmod(self.base, 0o755)
        marker = self.base / "keep-me"
        marker.mkdir()
        (marker / "child").write_text("stay\n", encoding="utf-8")
        self.install()
        self.assertEqual(stat.S_IMODE(self.base.stat().st_mode), 0o700)
        self.assertEqual((marker / "child").read_text(encoding="utf-8"), "stay\n")

    def test_foreign_incoming_is_not_deleted(self) -> None:
        self.base.mkdir(mode=0o700)
        os.chmod(self.base, 0o700)
        incoming = self.base / f".{SHA}.incoming"
        incoming.mkdir()
        marker = incoming / "foreign"
        marker.write_text("keep\n", encoding="utf-8")
        with self.assertRaisesRegex(preparer.PrepareError, "incoming_transaction_present"):
            self.install()
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_sha_tree_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(preparer.PrepareError, "release_sha_mismatch"):
            self.install(release_sha="e" * 40)

    def test_stale_receipt_is_rejected(self) -> None:
        created = datetime.now(timezone.utc) - timedelta(seconds=3601)
        stale = _json(
            self.workspace / "receipts" / "stale.json",
            {"created_at": created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "status": "PASS"},
        )
        with self.assertRaisesRegex(preparer.PrepareError, "receipt_stale"):
            preparer.assert_fresh_receipt(stale)

    def test_image_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(preparer.PrepareError, "image_mismatch"):
            self.install(image_id="sha256:" + "e" * 64)

    def test_env_mismatch_is_rejected(self) -> None:
        _write(
            self.bot_env,
            (
                f"MARKET_BOT_DATA_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}\n"
                f"MARKET_PIPELINE_RELEASE_SHA={SHA}\n"
                f"MARKET_PIPELINE_IMAGE=sha256:{'e' * 64}\n"
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
            ),
        )
        with self.assertRaisesRegex(preparer.PrepareError, "env_image_mismatch"):
            self.install()

    def test_control_payload_drift_is_rejected(self) -> None:
        self.control.write_text("drifted\n", encoding="utf-8")
        with self.assertRaisesRegex(preparer.PrepareError, "control_payload_drift"):
            self.install()

    def test_host_role_mismatch_is_rejected(self) -> None:
        _write(
            self.bot_env,
            (
                f"MARKET_WEB_DATA_ROOT={preparer.CANONICAL_WEB_DATA_ROOT}\n"
                f"MARKET_PIPELINE_RELEASE_SHA={SHA}\n"
                f"MARKET_PIPELINE_IMAGE={IMAGE}\n"
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
            ),
        )
        with self.assertRaisesRegex(preparer.PrepareError, "host_role_mismatch"):
            self.install()

    def test_insecure_data_root_is_rejected(self) -> None:
        with self.assertRaisesRegex(preparer.PrepareError, "bot_data_root_path_forbidden"):
            preparer.prepare_directory(Path("/tmp/private-primary-data"), label="bot_data_root")

    def test_insecure_offhost_root_is_rejected(self) -> None:
        with self.assertRaisesRegex(preparer.PrepareError, "offhost_root_path_forbidden"):
            preparer.prepare_directory(Path("/var/tmp/offhost-backups"), label="offhost_root")

    def test_valid_backup_key_is_reused(self) -> None:
        key = self.workspace / "keys" / "backup.key"
        first = preparer.generate_or_reuse_backup_key(key)
        material = key.read_text(encoding="ascii")
        second = preparer.generate_or_reuse_backup_key(key)
        self.assertTrue(first["created"])
        self.assertTrue(second["reused"])
        self.assertEqual(key.read_text(encoding="ascii"), material)
        self.assertNotIn("key", first)
        self.assertNotIn(material.strip(), json.dumps(first))

    def test_invalid_backup_key_is_not_overwritten(self) -> None:
        key = self.workspace / "keys" / "bad.key"
        _write(key, "not-a-key\n")
        with self.assertRaisesRegex(preparer.PrepareError, "backup_key_invalid_existing"):
            preparer.generate_or_reuse_backup_key(key)
        self.assertEqual(key.read_text(encoding="ascii"), "not-a-key\n")

    def test_secret_never_enters_receipt_or_log_payload(self) -> None:
        key = self.workspace / "keys" / "secret.key"
        generate_key(key_file=key)
        foundation = preparer.prepare_foundation(
            bot_data_root=self.workspace / "bot-data",
            web_data_root=self.workspace / "web-data",
            web_backup_root=self.workspace / "web-data" / "backups",
            offhost_root=self.workspace / "offhost",
            backup_key=key,
            receipt=self.workspace / "receipts" / "foundation.json",
            release_sha=SHA,
            release_tree=TREE,
        )
        material = key.read_text(encoding="ascii").strip()
        encoded = json.dumps(foundation)
        self.assertNotIn(material, encoded)
        self.assertFalse(foundation["secrets_disclosed"])
        self.assertEqual(foundation["backup_key"]["mode"], "0600")
        self.assertNotIn("sha256", foundation["backup_key"])

    def test_prepare_receipt_keeps_authority_and_queue_unchanged(self) -> None:
        payload = preparer.write_prepare_receipt(
            receipt=self.workspace / "receipts" / "prepare.json",
            release_sha=SHA,
            release_tree=TREE,
            foundation={"schema": preparer.FOUNDATION_SCHEMA},
            local_install={"installation_status": "PASS", "idempotent_reuse": False},
            remote_install={"installation_status": "PASS", "idempotent_reuse": False},
            preflight_sha256="e" * 64,
            control_manifest_sha256="f" * 64,
            image_id=IMAGE,
            historical_flags={key: "0" for key in preparer.HISTORICAL_FLAGS},
        )
        self.assertEqual(payload["authority_changed"], False)
        self.assertEqual(payload["queue_owner_changed"], False)
        self.assertEqual(payload["capture_owner_changed"], False)
        self.assertEqual(payload["historical_flags"], {key: "0" for key in preparer.HISTORICAL_FLAGS})

    def test_topology_source_rejects_staging_and_plaintext_secret(self) -> None:
        source = _write(
            self.workspace / "inputs" / "bot.source.env",
            (
                f"MARKET_BOT_DATA_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}\n"
                f"MARKET_PRODUCT_SNAPSHOT_ROOT={preparer.CANONICAL_BOT_DATA_ROOT}/snapshots\n"
                "MARKET_CAPTURE_TOKEN=secret-value\n"
            ),
        )
        with self.assertRaisesRegex(preparer.PrepareError, "bot_source_plaintext_secret"):
            preparer.validate_topology_source(
                source, role="bot", repository_root=REPO_ROOT
            )

    def test_sparse_one_gram_contract_stays_narrow(self) -> None:
        self.assertEqual(
            verifier.SAFE_SPARSE_NO_DATA_CELLS,
            {("COIN_ONE_GRAM", "CASH"), ("COIN_ONE_GRAM", "TOMORROW")},
        )
        self.assertEqual(
            verifier.SAFE_SPARSE_NO_DATA_METHOD,
            "ABSTAIN_NO_SAFE_SAME_COMMODITY_ANCHOR",
        )
        self.assertEqual(
            verifier.SAFE_SPARSE_NO_DATA_REASON,
            "NO_SAFE_SAME_COMMODITY_ANCHOR",
        )

    def test_unauthorized_no_data_and_product_safe_no_data_remain_blocking(self) -> None:
        source = Path(verifier.__file__).read_text(encoding="utf-8")
        self.assertIn("private_primary_estimated_rate_coverage_invalid", source)
        self.assertIn("health_snapshot_identity_invalid", source)
        self.assertIn('value.get("snapshot_status") != "OK"', source)
        self.assertIn('snapshot.status != "OK"', source)


if __name__ == "__main__":
    unittest.main()
