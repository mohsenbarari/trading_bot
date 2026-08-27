"""Fail-closed integration contracts for Market Pipeline release evidence."""

from __future__ import annotations

import json
from hashlib import sha256
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
        self.assertIn("PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=0", manifest)
        self.assertIn(
            "load-and-preflight-production-market-pipeline-shadow-hosts", manifest
        )
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

    def test_host_preflight_requires_evidence_exact_confirmation_and_disk_floor(self) -> None:
        no_evidence = run_sourced(
            """
PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=1
PRODUCTION_MARKET_PIPELINE_EVIDENCE_REQUESTED=0
validate_production_market_pipeline_host_preflight_manifest
"""
        )
        self.assertNotEqual(no_evidence.returncode, 0)
        self.assertIn("requires exact release evidence", no_evidence.stderr)
        wrong_confirmation = run_sourced(
            """
PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=1
PRODUCTION_MARKET_PIPELINE_EVIDENCE_REQUESTED=1
PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM=wrong
PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=0
PRODUCTION_MARKET_PIPELINE_MIN_FREE_MIB=2048
PRODUCTION_MARKET_PIPELINE_RELEASE_BASE_DIR=/srv/trading-bot/market-pipeline-releases
validate_production_market_pipeline_host_preflight_manifest
"""
        )
        self.assertNotEqual(wrong_confirmation.returncode, 0)
        self.assertIn("exact load-and-preflight confirmation", wrong_confirmation.stderr)
        low_disk_floor = run_sourced(
            """
PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=1
PRODUCTION_MARKET_PIPELINE_EVIDENCE_REQUESTED=1
PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM=load-and-preflight-production-market-pipeline-shadow-hosts
PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=0
PRODUCTION_MARKET_PIPELINE_MIN_FREE_MIB=512
PRODUCTION_MARKET_PIPELINE_RELEASE_BASE_DIR=/srv/trading-bot/market-pipeline-releases
validate_production_market_pipeline_host_preflight_manifest
"""
        )
        self.assertNotEqual(low_disk_floor.returncode, 0)
        self.assertIn("minimum free space", low_disk_floor.stderr)

    def test_control_payload_is_commit_exact_minimal_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory(prefix="market-control-payload-") as temporary:
            root = Path(temporary)
            release_dir = root / "release"
            payload = release_dir / "control-payload"
            manifest = release_dir / "control-payload.sha256"
            result = run_sourced(
                """
PRODUCTION_MARKET_PIPELINE_EVIDENCE_REQUESTED=1
PRODUCTION_MARKET_PIPELINE_RELEASE_DIR="$2"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_DIR="$3"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST="$4"
LOCAL_PROJECT_DIR="$5"
RELEASE_SHA="$(git -C "$5" rev-parse HEAD)"
prepare_market_pipeline_control_payload
verify_market_pipeline_control_payload
printf '%s\n' "$PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST_SHA256"
""",
                str(release_dir),
                str(payload),
                str(manifest),
                str(REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            names = {
                path.relative_to(payload).as_posix()
                for path in payload.rglob("*")
                if path.is_file()
            }
            self.assertIn("scripts/manage_market_pipeline_stage3.py", names)
            self.assertIn("deploy/market-data/compose.web.yml", names)
            self.assertIn("deploy/market-data/compose.bot.yml", names)
            self.assertFalse(any(".env" in name or "session" in name for name in names))
            target = payload / "deploy/market-data/compose.yml"
            target.write_text(target.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
            tampered = run_sourced(
                """
PRODUCTION_MARKET_PIPELINE_EVIDENCE_REQUESTED=1
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_DIR="$2"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST="$3"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST_SHA256="$4"
verify_market_pipeline_control_payload
""",
                str(payload),
                str(manifest),
                result.stdout.strip().splitlines()[-1],
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("contents drifted", tampered.stderr)

    def test_local_release_directory_is_atomic_idempotent_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory(prefix="market-stable-release-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir(mode=0o700)
            control = payload / "control.txt"
            control.write_text("committed-control\n", encoding="utf-8")
            manifest = root / "control-payload.sha256"
            manifest.write_text(
                f"{sha256(control.read_bytes()).hexdigest()}  ./control.txt\n",
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            bot_env = root / "bot.env"
            image_receipt = root / "image.json"
            pair_receipt = root / "pair.json"
            for path, body in (
                (bot_env, "MARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW\n"),
                (image_receipt, '{"image":"exact"}\n'),
                (pair_receipt, '{"pair":"exact"}\n'),
            ):
                path.write_text(body, encoding="utf-8")
                path.chmod(0o600)
            stable = root / "stable-releases"
            release_sha = "a" * 40
            body = """
PRODUCTION_MARKET_PIPELINE_RELEASE_BASE_DIR="$2"
RELEASE_SHA="$3"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_DIR="$4"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST="$5"
PRODUCTION_MARKET_PIPELINE_CONTROL_PAYLOAD_MANIFEST_SHA256="$6"
PRODUCTION_MARKET_PIPELINE_BOT_ENV="$7"
PRODUCTION_MARKET_PIPELINE_IMAGE_RECEIPT="$8"
PRODUCTION_MARKET_PIPELINE_PAIR_RECEIPT="$9"
PRODUCTION_MARKET_PIPELINE_IMAGE_RECEIPT_SHA256="${10}"
PRODUCTION_MARKET_PIPELINE_PAIR_RECEIPT_SHA256="${11}"
install_market_pipeline_control_release_local
printf '%s\n' "$LOCAL_MARKET_PIPELINE_CONTROL_RELEASE_DIR"
"""
            arguments = (
                str(stable),
                release_sha,
                str(payload),
                str(manifest),
                sha256(manifest.read_bytes()).hexdigest(),
                str(bot_env),
                str(image_receipt),
                str(pair_receipt),
                sha256(image_receipt.read_bytes()).hexdigest(),
                sha256(pair_receipt.read_bytes()).hexdigest(),
            )
            first = run_sourced(body, *arguments)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            installed = stable / release_sha
            self.assertTrue(installed.is_dir())
            self.assertEqual(installed.stat().st_mode & 0o777, 0o700)
            self.assertEqual((installed / "control.txt").read_bytes(), control.read_bytes())
            second = run_sourced(body, *arguments)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            (installed / "control.txt").write_text("tampered\n", encoding="utf-8")
            rejected = run_sourced(body, *arguments)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("contents drifted", rejected.stderr)

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

        run_release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            run_release.index("prepare_market_pipeline_two_host_preflight"),
            run_release.index("begin_two_host_release_transaction"),
        )
        self.assertLess(
            run_release.index("prepare_market_pipeline_two_host_preflight"),
            run_release.index("quiesce_two_host_writers_for_migration"),
        )
        load_image = source.split("load_market_pipeline_image_remote() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn('docker image save "$PRODUCTION_MARKET_PIPELINE_IMAGE_ID"', load_image)
        self.assertIn('"${SSH_IRAN_CMD[@]}" "$IRAN_SSH_TARGET"', load_image)
        self.assertNotIn("/tmp", load_image)
        self.assertNotIn("docker image save -o", load_image)


if __name__ == "__main__":
    unittest.main()
