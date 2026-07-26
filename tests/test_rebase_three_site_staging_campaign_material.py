from __future__ import annotations

import json
import unittest
from uuid import uuid4

from scripts.rebase_three_site_staging_campaign_material import (
    CampaignMaterialError,
    derive_campaign_material,
)
from scripts.render_three_site_staging_role_compose import parse_env_values
from tests.test_three_site_staging_signed_inventory import _inventory


class RebaseThreeSiteStagingCampaignMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = _inventory()
        self.template["inventory_stage"] = "provisioned"
        values = {
            "STAGING_RELEASE_SHA": self.template["release_sha"],
            "DR_BLOB_OBJECT_PREFIX": self.template["object_storage"]["prefix"] + "blobs/sha256",
            "UNCHANGED_SECRET": "not-a-placeholder-and-never-printed",
        }
        self.template_env = "".join(f"{key}={value}\n" for key, value in values.items())

    def test_derivation_rebinds_only_campaign_material_and_drops_postgres_ids(self):
        inventory, env, subject = derive_campaign_material(
            template_inventory=self.template,
            template_env=self.template_env,
            release_sha="b" * 40,
            campaign_id=str(uuid4()),
            deployment_id="three-site-4c171289-campaign-a",
            object_prefix="staging/three-site/4c171289/campaign-a/",
        )
        self.assertEqual(inventory["inventory_stage"], "planned")
        self.assertEqual(inventory["release_sha"], "b" * 40)
        self.assertTrue(all(role["postgres_system_id"] is None for role in inventory["roles"]))
        self.assertTrue(all(role["release_sha"] == "b" * 40 for role in inventory["roles"]))
        self.assertEqual(inventory["object_storage"]["prefix"], "staging/three-site/4c171289/campaign-a/")
        values = parse_env_values(env.decode())
        self.assertEqual(values["STAGING_RELEASE_SHA"], "b" * 40)
        self.assertEqual(values["DR_BLOB_OBJECT_PREFIX"], "staging/three-site/4c171289/campaign-a/blobs/sha256")
        self.assertEqual(values["UNCHANGED_SECRET"], "not-a-placeholder-and-never-printed")
        self.assertEqual(subject["release_sha"], "b" * 40)
        self.assertEqual(subject["bindings"]["inventory_stage"], "planned")

    def test_rejects_reused_campaign_identity_or_prefix(self):
        with self.assertRaisesRegex(CampaignMaterialError, "fresh campaign/deployment"):
            derive_campaign_material(
                template_inventory=self.template,
                template_env=self.template_env,
                release_sha="b" * 40,
                campaign_id=self.template["campaign_id"],
                deployment_id="three-site-4c171289-campaign-a",
                object_prefix="staging/three-site/4c171289/campaign-a/",
            )
        with self.assertRaisesRegex(CampaignMaterialError, "fresh Object Storage"):
            derive_campaign_material(
                template_inventory=self.template,
                template_env=self.template_env,
                release_sha="b" * 40,
                campaign_id=str(uuid4()),
                deployment_id="three-site-4c171289-campaign-a",
                object_prefix=self.template["object_storage"]["prefix"],
            )

    def test_rejects_env_that_is_not_bound_to_template_inventory(self):
        values = parse_env_values(self.template_env)
        values["STAGING_RELEASE_SHA"] = "c" * 40
        stale_env = "".join(f"{key}={value}\n" for key, value in values.items())
        with self.assertRaisesRegex(CampaignMaterialError, "does not match"):
            derive_campaign_material(
                template_inventory=self.template,
                template_env=stale_env,
                release_sha="b" * 40,
                campaign_id=str(uuid4()),
                deployment_id="three-site-4c171289-campaign-a",
                object_prefix="staging/three-site/4c171289/campaign-a/",
            )


if __name__ == "__main__":
    unittest.main()
