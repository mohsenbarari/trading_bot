from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import prepare_market_pipeline_primary_release as primary
from scripts.prepare_market_pipeline_release import parse_env
from tests.test_prepare_market_pipeline_release import (
    IMAGE_ID,
    RELEASE_SHA,
    RELEASE_TREE,
    _bot_values,
    _web_values,
    _write_source,
)


class PrepareMarketPipelinePrimaryReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-primary-pair-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.web_source = self.root / "web.source.env"
        self.bot_source = self.root / "bot.source.env"
        self.web_env = self.root / "web.primary.env"
        self.bot_env = self.root / "bot.primary.env"
        self.receipt = self.root / "receipt.json"
        _write_source(self.web_source, _web_values())
        _write_source(self.bot_source, _bot_values())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _arguments(self) -> dict[str, object]:
        return {
            "web_source": self.web_source,
            "bot_source": self.bot_source,
            "web_env": self.web_env,
            "bot_env": self.bot_env,
            "receipt": self.receipt,
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "project_name": "market-private-pipeline-stage13-shadow",
        }

    def test_pair_is_primary_bound_and_does_not_claim_product_cutover(self) -> None:
        document = primary.render_pair(**self._arguments())

        for output in (self.web_env, self.bot_env):
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            values = parse_env(output, secure_input=True)
            self.assertEqual(values["MARKET_PIPELINE_FEED_MODE"], "PRIVATE_PRIMARY")
            self.assertEqual(values["MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"], "1")
            self.assertEqual(
                values["MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"],
                "PRIVATE_PRIMARY",
            )
        self.assertFalse(document["product_authority_changed"])
        self.assertFalse(document["legacy_retirement_authorized"])
        self.assertNotIn("/srv/trading-bot/secure/market", self.receipt.read_text())
        verified = primary.verify_pair(**self._arguments())
        self.assertEqual(verified, document)

    def test_derive_source_strips_release_values_and_adds_only_web_key_path(self) -> None:
        rendered = {
            **_web_values(),
            "MARKET_PIPELINE_IMAGE": IMAGE_ID,
            "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
            "MARKET_PIPELINE_MODE": "live",
            "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-stage13-shadow",
            "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
            "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
            "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
        }
        rendered.pop("MARKET_RESEARCH_ENCRYPTION_KEY_FILE")
        old_env = self.root / "old-web.env"
        derived = self.root / "derived-web.source.env"
        _write_source(old_env, rendered)

        result = primary.derive_source(
            role="web",
            rendered_env=old_env,
            source_env=derived,
            research_key_file=Path(
                "/srv/trading-bot/secure/market/research-archive.key"
            ),
        )

        values = parse_env(derived, secure_input=True)
        self.assertFalse(set(primary.DYNAMIC_VALUES).intersection(values))
        self.assertEqual(
            values["MARKET_RESEARCH_ENCRYPTION_KEY_FILE"],
            "/srv/trading-bot/secure/market/research-archive.key",
        )
        self.assertFalse(result["secret_values_read"])

    def test_dynamic_authority_values_are_forbidden_in_source(self) -> None:
        values = _web_values()
        values["MARKET_PIPELINE_FEED_MODE"] = "PRIVATE_PRIMARY"
        _write_source(self.web_source, values)

        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_source_contains_dynamic_values",
        ):
            primary.render_pair(**self._arguments())
        self.assertFalse(self.web_env.exists())

    def test_cross_host_topology_drift_fails_before_output(self) -> None:
        values = _bot_values()
        values["MARKET_WEB_PRIVATE_IP"] = "10.240.1.21"
        _write_source(self.bot_source, values)

        with self.assertRaisesRegex(Exception, "cross_role_topology_mismatch"):
            primary.render_pair(**self._arguments())
        self.assertFalse(self.web_env.exists())
        self.assertFalse(self.bot_env.exists())

    def test_receipt_or_output_tampering_is_rejected(self) -> None:
        primary.render_pair(**self._arguments())
        values = parse_env(self.bot_env, secure_input=True)
        values["MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"] = "PRIVATE_SHADOW"
        _write_source(self.bot_env, values)

        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_output_content_mismatch",
        ):
            primary.verify_pair(**self._arguments())

        receipt = json.loads(self.receipt.read_text())
        receipt["legacy_retirement_authorized"] = True
        self.receipt.write_text(json.dumps(receipt), encoding="utf-8")
        self.receipt.chmod(0o600)
        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_receipt_identity_invalid",
        ):
            primary.verify_pair(**self._arguments())


if __name__ == "__main__":
    unittest.main()
