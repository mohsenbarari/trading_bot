from __future__ import annotations

from datetime import datetime, timezone
import unittest

from scripts.verify_three_site_staging_image_inventory import (
    ImageInventoryError,
    verify_image_document,
)


class ThreeSiteStagingImageInventoryTests(unittest.TestCase):
    def _document(self):
        release = "a" * 40
        return {
            "schema": "three-site-staging-image-inventory-v1",
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": release,
            "role": "webapp-fi",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "role_compose_sha256": "b" * 64,
            "role_env_sha256": "c" * 64,
            "images": [
                {
                    "reference": f"trading_bot_three_site_staging:{release}",
                    "image_id": "sha256:" + "1" * 64,
                    "repo_digests": [],
                    "release_label": release,
                },
                {
                    "reference": f"trading_bot_postgres_boottime:15-{release}",
                    "image_id": "sha256:" + "2" * 64,
                    "repo_digests": [],
                    "release_label": release,
                },
                {
                    "reference": "redis:7-alpine",
                    "image_id": "sha256:" + "3" * 64,
                    "repo_digests": ["redis@sha256:" + "4" * 64],
                    "release_label": None,
                },
            ],
        }

    def test_exact_release_labels_and_upstream_digest_pass(self):
        document = self._document()
        result = verify_image_document(
            document,
            role="webapp-fi",
            campaign_id=document["campaign_id"],
            release_sha=document["release_sha"],
            role_compose_sha256=document["role_compose_sha256"],
            role_env_sha256=document["role_env_sha256"],
        )
        self.assertEqual(result["status"], "verified")

    def test_moving_third_party_tag_without_repo_digest_fails(self):
        document = self._document()
        document["images"][-1]["repo_digests"] = []
        with self.assertRaisesRegex(ImageInventoryError, "repository digest"):
            verify_image_document(
                document,
                role="webapp-fi",
                campaign_id=document["campaign_id"],
                release_sha=document["release_sha"],
                role_compose_sha256=document["role_compose_sha256"],
                role_env_sha256=document["role_env_sha256"],
            )

    def test_wrong_local_release_label_fails(self):
        document = self._document()
        document["images"][0]["release_label"] = "d" * 40
        with self.assertRaisesRegex(ImageInventoryError, "release label"):
            verify_image_document(
                document,
                role="webapp-fi",
                campaign_id=document["campaign_id"],
                release_sha=document["release_sha"],
                role_compose_sha256=document["role_compose_sha256"],
                role_env_sha256=document["role_env_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
