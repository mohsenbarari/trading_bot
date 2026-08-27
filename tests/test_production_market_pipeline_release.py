"""Fail-closed integration contracts for Market Pipeline release evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "production_deploy_online.sh"
MANIFEST_EXAMPLE = REPO_ROOT / "deploy" / "production" / "online.env.example"
MARKET_DOCKERFILE = REPO_ROOT / "deploy" / "market-data" / "Dockerfile"


def run_sourced(body: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f'source "$1"\n{body}',
            "market-production-release-test",
            str(RELEASE_SCRIPT),
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


def _write_source(path: Path, role: str) -> None:
    common = {
        "MARKET_WEB_PRIVATE_IP": "10.240.1.20",
        "MARKET_BOT_PRIVATE_IP": "10.240.1.10",
        "MARKET_WEB_SNAPSHOT_RECEIVER_PORT": "9443",
        "MARKET_BOT_FACT_RECEIVER_PORT": "9443",
        "MARKET_TRANSPORT_CA_FILE": "/srv/trading-bot/secure/market/ca.pem",
        "MARKET_HMAC_ACTIVE_FILE": "/srv/trading-bot/secure/market/hmac-active",
        "MARKET_HMAC_PREVIOUS_FILE": "/srv/trading-bot/secure/market/hmac-previous",
    }
    if role == "web":
        values = {
            **common,
            "MARKET_PRIVATE_BIND_IP": "10.240.1.20",
            "MARKET_WEB_DATA_ROOT": "/srv/trading-bot/market-data-production",
            "MARKET_POSTGRES_PASSWORD_FILE": "/srv/trading-bot/secure/market/postgres-password",
            "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE": "/srv/trading-bot/secure/market/account1.json",
            "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE": "/srv/trading-bot/secure/market/account2.json",
            "MARKET_CAPTURE_ACCOUNT2_HMAC_FILE": "/srv/trading-bot/secure/market/account2-hmac",
            "MARKET_WEB_TRANSPORT_CERT_FILE": "/srv/trading-bot/secure/market/web-cert.pem",
            "MARKET_WEB_TRANSPORT_KEY_FILE": "/srv/trading-bot/secure/market/web-key.pem",
        }
    else:
        values = {
            **common,
            "MARKET_PRIVATE_BIND_IP": "10.240.1.10",
            "MARKET_BOT_DATA_ROOT": "/srv/trading-bot/production-data/market-pipeline",
            "MARKET_BOT_TRANSPORT_CERT_FILE": "/srv/trading-bot/secure/market/bot-cert.pem",
            "MARKET_BOT_TRANSPORT_KEY_FILE": "/srv/trading-bot/secure/market/bot-key.pem",
        }
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)


class ProductionMarketPipelineReleaseTests(unittest.TestCase):
    def test_manifest_defaults_are_evidence_only_and_capture_cutover_is_off(self) -> None:
        manifest = MANIFEST_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn(
            "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED=0", manifest
        )
        self.assertIn("PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=0", manifest)
        self.assertIn("prepare-production-market-pipeline-shadow-evidence", manifest)
        self.assertIn("does not transfer/load the image", manifest)

    def test_capture_cutover_is_rejected_even_when_evidence_is_disabled(self) -> None:
        result = run_sourced(
            """
PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED=0
PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=1
validate_production_market_pipeline_evidence_manifest
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an authority surface", result.stderr)

    def test_evidence_requires_exact_confirmation_and_valid_role_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="market-release-manifest-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            web = root / "web.env"
            bot = root / "bot.env"
            _write_source(web, "web")
            _write_source(bot, "bot")
            common = f"""
PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED=1
PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=0
PRODUCTION_MARKET_PIPELINE_WEB_ENV_SOURCE_PATH="$2"
PRODUCTION_MARKET_PIPELINE_BOT_ENV_SOURCE_PATH="$3"
PRODUCTION_MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-production
LOCAL_PROJECT_DIR="$4"
"""
            rejected = run_sourced(
                common
                + """
PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_CONFIRM=wrong
validate_production_market_pipeline_evidence_manifest
""",
                str(web),
                str(bot),
                str(REPO_ROOT),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("exact shadow-only confirmation", rejected.stderr)

            accepted = run_sourced(
                common
                + """
PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_CONFIRM=prepare-production-market-pipeline-shadow-evidence
validate_production_market_pipeline_evidence_manifest
printf '%s\n' "$PRODUCTION_MARKET_PIPELINE_EVIDENCE_REQUESTED"
""",
                str(web),
                str(bot),
                str(REPO_ROOT),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr + accepted.stdout)
            self.assertTrue(accepted.stdout.rstrip().endswith("1"))

    def test_image_receipt_is_exact_release_bound_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="market-image-receipt-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir(mode=0o700)
            receipt = artifacts / "image.json"
            release_sha = "a" * 40
            release_tree = "b" * 40
            image_id = "sha256:" + "c" * 64
            signature = "d" * 64
            result = run_sourced(
                r'''
RELEASE_ARTIFACT_DIR="$2"
PRODUCTION_MARKET_PIPELINE_IMAGE_RECEIPT="$3"
RELEASE_SHA="$4"
PRODUCTION_RELEASE_TREE="$5"
IMAGE_ID="$6"
INPUT_SIGNATURE="$7"
docker() {
  [[ "$1 $2" == "image inspect" ]] || return 41
  printf '%s\n' "$IMAGE_ID|linux/amd64|10001:10001|$RELEASE_SHA|$PRODUCTION_RELEASE_TREE|$INPUT_SIGNATURE"
}
write_market_pipeline_image_receipt "$IMAGE_ID" "$INPUT_SIGNATURE" market:test
printf '%s\n' "$PRODUCTION_MARKET_PIPELINE_IMAGE_RECEIPT_SHA256"
''',
                str(artifacts),
                str(receipt),
                release_sha,
                release_tree,
                image_id,
                signature,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["image_id"], image_id)
            self.assertEqual(payload["release_sha"], release_sha)
            self.assertEqual(payload["release_tree"], release_tree)
            self.assertEqual(payload["input_signature"], signature)
            self.assertFalse(payload["secrets_disclosed"])
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def test_image_labels_and_release_hooks_cover_exact_evidence_without_rollout(self) -> None:
        dockerfile = MARKET_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("ARG SOURCE_TREE", dockerfile)
        self.assertIn("ARG IMAGE_INPUT_SIGNATURE", dockerfile)
        self.assertIn('io.gold-trade.release.tree="${SOURCE_TREE}"', dockerfile)
        self.assertIn(
            'io.gold-trade.release.input-signature="${IMAGE_INPUT_SIGNATURE}"',
            dockerfile,
        )
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        prepare = source.split("prepare_release_evidence_artifacts() {", 1)[1].split(
            "\n}", 1
        )[0]
        verify = source.split("verify_prepared_release_artifacts() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("prepare_market_pipeline_release_evidence", prepare)
        self.assertIn("verify_market_pipeline_release_evidence", verify)
        market_prepare = source.split(
            "prepare_market_pipeline_release_evidence() {", 1
        )[1].split("\n}", 1)[0]
        for forbidden in ("ssh_iran", "docker save", "docker load", "compose up", "systemctl"):
            self.assertNotIn(forbidden, market_prepare)


if __name__ == "__main__":
    unittest.main()
