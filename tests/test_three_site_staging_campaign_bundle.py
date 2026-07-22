from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

import yaml

from scripts.render_three_site_staging_role_compose import (
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_staging_campaign_bundle import (
    CampaignBundleError,
    verify_campaign_bundle,
)
from tests.test_three_site_staging_signed_inventory import (
    _inventory,
    _signed_documents,
)


ROOT = Path(__file__).resolve().parents[1]


class ThreeSiteStagingCampaignBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = yaml.safe_load(
            (ROOT / "deploy/staging/docker-compose.three-site.yml").read_text()
        )
        values = parse_env_values(
            (ROOT / "deploy/staging/env.three-site.staging.example").read_text()
        )
        cls.values = {
            name: value.replace("CHANGE_ME", f"campaign_{name.lower()}")
            for name, value in values.items()
        }
        bot_to_fi = "campaign-bot-to-fi-directional-secret-0001"
        fi_to_bot = "campaign-fi-to-bot-directional-secret-0002"
        fi_to_ir = "campaign-fi-to-ir-directional-secret-0003"
        ir_to_fi = "campaign-ir-to-fi-directional-secret-0004"
        cls.values["BOT_FI_DR_PAIRWISE_KEYS_JSON"] = json.dumps(
            [
                {
                    "key_id": "bot-to-fi",
                    "source_site": "bot_fi",
                    "destination_site": "webapp_fi",
                    "secret": bot_to_fi,
                },
                {
                    "key_id": "fi-to-bot",
                    "source_site": "webapp_fi",
                    "destination_site": "bot_fi",
                    "secret": fi_to_bot,
                },
            ],
            separators=(",", ":"),
        )
        cls.values["WEBAPP_FI_DR_PAIRWISE_KEYS_JSON"] = json.dumps(
            [
                {
                    "key_id": "bot-to-fi",
                    "source_site": "bot_fi",
                    "destination_site": "webapp_fi",
                    "secret": bot_to_fi,
                },
                {
                    "key_id": "fi-to-bot",
                    "source_site": "webapp_fi",
                    "destination_site": "bot_fi",
                    "secret": fi_to_bot,
                },
                {
                    "key_id": "fi-to-ir",
                    "source_site": "webapp_fi",
                    "destination_site": "webapp_ir",
                    "secret": fi_to_ir,
                },
                {
                    "key_id": "ir-to-fi",
                    "source_site": "webapp_ir",
                    "destination_site": "webapp_fi",
                    "secret": ir_to_fi,
                },
            ],
            separators=(",", ":"),
        )
        cls.values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"] = json.dumps(
            [
                {
                    "key_id": "fi-to-ir",
                    "source_site": "webapp_fi",
                    "destination_site": "webapp_ir",
                    "secret": fi_to_ir,
                },
                {
                    "key_id": "ir-to-fi",
                    "source_site": "webapp_ir",
                    "destination_site": "webapp_fi",
                    "secret": ir_to_fi,
                },
            ],
            separators=(",", ":"),
        )
        cls.inventory = _inventory()
        cls.values["DR_BLOB_OBJECT_BUCKET"] = cls.inventory["object_storage"]["bucket"]
        cls.values["DR_BLOB_OBJECT_PREFIX"] = (
            cls.inventory["object_storage"]["prefix"] + "blobs/sha256"
        )
        bind_names = {
            "bot_fi": "BOT_FI_DR_BIND_ADDRESS",
            "webapp_fi": "WEBAPP_FI_DR_BIND_ADDRESS",
            "webapp_ir": "WEBAPP_IR_DR_BIND_ADDRESS",
            "witness": "WITNESS_DR_BIND_ADDRESS",
        }
        for role in cls.inventory["roles"]:
            cls.values[bind_names[role["role"]]] = role["host_ip"]
        by_role = {role["role"]: role for role in cls.inventory["roles"]}
        cls.values.update(
            BOT_FI_PEER_WEBAPP_FI_IP=by_role["webapp_fi"]["host_ip"],
            WEBAPP_FI_PEER_BOT_FI_IP=by_role["bot_fi"]["host_ip"],
            WEBAPP_FI_PEER_WEBAPP_IR_IP=by_role["webapp_ir"]["host_ip"],
            WEBAPP_FI_WITNESS_IP=by_role["witness"]["host_ip"],
            WEBAPP_IR_PEER_WEBAPP_FI_IP=by_role["webapp_fi"]["host_ip"],
            WEBAPP_IR_WITNESS_IP=by_role["witness"]["host_ip"],
        )
        cls.policy, cls.approval = _signed_documents(
            cls.inventory, datetime.now(timezone.utc)
        )

    def _bundles(self, values=None):
        active = values or self.values
        result = {}
        for role in ("bot-fi", "webapp-fi", "webapp-ir", "witness"):
            payload = render_role_compose(self.canonical, role=role)
            result[role] = (
                canonical_role_compose_bytes(payload),
                canonical_role_env_bytes(
                    active,
                    required_names=referenced_environment_names(payload),
                ),
            )
        return result

    def test_four_role_campaign_proves_cross_host_secret_contract(self):
        result = verify_campaign_bundle(
            canonical_compose=self.canonical,
            bundles=self._bundles(),
            inventory=self.inventory,
            approval=self.approval,
            signer_policy=self.policy,
            verify_files=False,
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["directional_pairwise_key_count"], 4)
        self.assertEqual(result["database_credential_count"], 27)
        self.assertEqual(len(result["campaign_bundle_sha256"]), 64)

    def test_mismatched_endpoint_pairwise_secret_is_rejected(self):
        bundles = self._bundles()
        compose_bytes, env_bytes = bundles["webapp-ir"]
        values = parse_env_values(env_bytes.decode())
        pairs = json.loads(values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"])
        pairs[0]["secret"] = "different-endpoint-secret-that-is-at-least-32-bytes"
        values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"] = json.dumps(
            pairs, separators=(",", ":")
        )
        payload = render_role_compose(self.canonical, role="webapp-ir")
        bundles["webapp-ir"] = (
            compose_bytes,
            canonical_role_env_bytes(
                values,
                required_names=referenced_environment_names(payload),
            ),
        )
        with self.assertRaisesRegex(CampaignBundleError, "identical at both endpoints"):
            verify_campaign_bundle(
                canonical_compose=self.canonical,
                bundles=bundles,
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )

    def test_cross_role_database_password_reuse_is_rejected(self):
        values = dict(self.values)
        values["WEBAPP_IR_APP_DB_PASSWORD"] = values["WEBAPP_FI_APP_DB_PASSWORD"]
        with self.assertRaisesRegex(CampaignBundleError, "database credential is reused"):
            verify_campaign_bundle(
                canonical_compose=self.canonical,
                bundles=self._bundles(values),
                inventory=self.inventory,
                approval=self.approval,
                signer_policy=self.policy,
                verify_files=False,
            )


if __name__ == "__main__":
    unittest.main()
