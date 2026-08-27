"""Offline contracts for the formal Market Pipeline release evidence pair."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import prepare_market_pipeline_release as release


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
IMAGE_SIGNATURE = "d" * 64


def _common(*, bind_ip: str) -> dict[str, str]:
    return {
        "MARKET_PRIVATE_BIND_IP": bind_ip,
        "MARKET_WEB_PRIVATE_IP": "10.240.1.20",
        "MARKET_BOT_PRIVATE_IP": "10.240.1.10",
        "MARKET_WEB_SNAPSHOT_RECEIVER_PORT": "9443",
        "MARKET_BOT_FACT_RECEIVER_PORT": "9443",
        "MARKET_TRANSPORT_CA_FILE": "/srv/trading-bot/secure/market/ca.pem",
        "MARKET_HMAC_ACTIVE_FILE": "/srv/trading-bot/secure/market/hmac-active",
        "MARKET_HMAC_PREVIOUS_FILE": "/srv/trading-bot/secure/market/hmac-previous",
    }


def _web_values() -> dict[str, str]:
    return {
        **_common(bind_ip="10.240.1.20"),
        "MARKET_WEB_DATA_ROOT": "/srv/trading-bot/market-data-production",
        "MARKET_POSTGRES_PASSWORD_FILE": "/srv/trading-bot/secure/market/postgres-password",
        "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE": "/srv/trading-bot/secure/market/account1.json",
        "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE": "/srv/trading-bot/secure/market/account2.json",
        "MARKET_CAPTURE_ACCOUNT2_HMAC_FILE": "/srv/trading-bot/secure/market/account2-hmac",
        "MARKET_RESEARCH_ENCRYPTION_KEY_FILE": "/srv/trading-bot/secure/market/research-archive.key",
        "MARKET_WEB_TRANSPORT_CERT_FILE": "/srv/trading-bot/secure/market/web-cert.pem",
        "MARKET_WEB_TRANSPORT_KEY_FILE": "/srv/trading-bot/secure/market/web-key.pem",
    }


def _bot_values() -> dict[str, str]:
    return {
        **_common(bind_ip="10.240.1.10"),
        "MARKET_BOT_DATA_ROOT": "/srv/trading-bot/production-data/market-pipeline",
        "MARKET_BOT_TRANSPORT_CERT_FILE": "/srv/trading-bot/secure/market/bot-cert.pem",
        "MARKET_BOT_TRANSPORT_KEY_FILE": "/srv/trading-bot/secure/market/bot-key.pem",
    }


def _write_source(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)


class PrepareMarketPipelineReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-release-pair-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.web_source = self.root / "web.source.env"
        self.bot_source = self.root / "bot.source.env"
        self.web_env = self.root / "web.release.env"
        self.bot_env = self.root / "bot.release.env"
        self.receipt = self.root / "receipt.json"
        _write_source(self.web_source, _web_values())
        _write_source(self.bot_source, _bot_values())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _render(self) -> dict[str, object]:
        return release.render_pair(
            web_source=self.web_source,
            bot_source=self.bot_source,
            web_output=self.web_env,
            bot_output=self.bot_env,
            receipt=self.receipt,
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
            project_name="market-private-pipeline-production",
        )

    def test_pair_is_release_bound_shadow_only_and_secret_free(self) -> None:
        document = self._render()
        self.assertEqual(self.web_env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.bot_env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        for output in (self.web_env, self.bot_env):
            values = release.parse_env(output, secure_input=False)
            self.assertEqual(values["MARKET_PIPELINE_IMAGE"], IMAGE_ID)
            self.assertEqual(values["MARKET_PIPELINE_RELEASE_SHA"], RELEASE_SHA)
            self.assertEqual(values["MARKET_PIPELINE_MODE"], "live")
            self.assertEqual(values["MARKET_PIPELINE_FEED_MODE"], "PRIVATE_SHADOW")
            self.assertEqual(values["MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"], "0")
            self.assertEqual(
                values["MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"], "PRIVATE_SHADOW"
            )
        encoded = self.receipt.read_text(encoding="utf-8")
        self.assertNotIn("/srv/trading-bot/secure/market/", encoded)
        self.assertFalse(document["authority"]["product_authority_changed"])
        self.assertFalse(document["authority"]["telegram_capture_cutover_authorized"])
        verified = release.verify_pair(
            web_source=self.web_source,
            bot_source=self.bot_source,
            web_output=self.web_env,
            bot_output=self.bot_env,
            receipt=self.receipt,
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
        )
        self.assertEqual(verified, document)

    def test_release_controlled_keys_are_rejected_from_source(self) -> None:
        values = _web_values()
        values["MARKET_PIPELINE_FEED_MODE"] = "PRIVATE_PRIMARY"
        _write_source(self.web_source, values)
        with self.assertRaisesRegex(
            release.ReleaseContractError,
            "source_env_contains_release_controlled_keys",
        ):
            self._render()
        self.assertFalse(self.web_env.exists())
        self.assertFalse(self.bot_env.exists())
        self.assertFalse(self.receipt.exists())

    def test_cross_role_network_drift_is_rejected_before_outputs(self) -> None:
        values = _bot_values()
        values["MARKET_WEB_PRIVATE_IP"] = "10.240.1.21"
        _write_source(self.bot_source, values)
        with self.assertRaisesRegex(
            release.ReleaseContractError, "cross_role_topology_mismatch"
        ):
            self._render()
        self.assertFalse(self.web_env.exists())
        self.assertFalse(self.bot_env.exists())

    def test_public_or_role_wrong_bind_is_rejected(self) -> None:
        public = _web_values()
        public["MARKET_PRIVATE_BIND_IP"] = "8.8.8.8"
        _write_source(self.web_source, public)
        with self.assertRaisesRegex(
            release.ReleaseContractError,
            "market_private_bind_ip_must_be_provider_private_ipv4",
        ):
            self._render()

        _write_source(self.web_source, _web_values())
        wrong = _bot_values()
        wrong["MARKET_PRIVATE_BIND_IP"] = wrong["MARKET_WEB_PRIVATE_IP"]
        _write_source(self.bot_source, wrong)
        with self.assertRaisesRegex(
            release.ReleaseContractError, "market_private_bind_ip_role_mismatch"
        ):
            self._render()

    def test_tmp_data_root_and_plaintext_secret_are_rejected(self) -> None:
        values = _bot_values()
        values["MARKET_BOT_DATA_ROOT"] = "/tmp/market-data"
        _write_source(self.bot_source, values)
        with self.assertRaisesRegex(release.ReleaseContractError, "tmp_forbidden"):
            self._render()
        values = _bot_values()
        values["MARKET_API_TOKEN"] = "must-not-be-here"
        _write_source(self.bot_source, values)
        with self.assertRaisesRegex(
            release.ReleaseContractError, "plaintext_secret_key_forbidden"
        ):
            self._render()

    def test_tampered_env_or_receipt_identity_fails_verification(self) -> None:
        self._render()
        self.bot_env.write_text(
            self.bot_env.read_text(encoding="utf-8").replace(
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW",
                "MARKET_PIPELINE_FEED_MODE=LEGACY",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(release.ReleaseContractError, "feed_mode_mismatch"):
            release.verify_pair(
                web_source=self.web_source,
                bot_source=self.bot_source,
                web_output=self.web_env,
                bot_output=self.bot_env,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )

        document = json.loads(self.receipt.read_text(encoding="utf-8"))
        document["release_tree"] = "e" * 40
        self.receipt.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseContractError, "identity_mismatch"):
            release.verify_pair(
                web_source=self.web_source,
                bot_source=self.bot_source,
                web_output=self.web_env,
                bot_output=self.bot_env,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )

    def test_source_and_output_parents_are_owner_only(self) -> None:
        self.root.chmod(0o755)
        with self.assertRaisesRegex(
            release.ReleaseContractError, "source_env_parent_owner_mode_invalid"
        ):
            self._render()


if __name__ == "__main__":
    unittest.main()
