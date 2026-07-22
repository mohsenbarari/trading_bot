from __future__ import annotations

from datetime import datetime, timezone
import unittest

from scripts.verify_three_site_staging_image_inventory import (
    ImageInventoryError,
    _canonical_sha256,
    image_content_descriptor,
    verify_image_document,
)


def _content(seed: str):
    descriptor = {
        "architecture": "amd64",
        "os": "linux",
        "created": "2026-07-22T00:00:00Z",
        "config_sha256": "sha256:" + seed * 64,
        "rootfs_type": "layers",
        "rootfs_layers": ["sha256:" + seed * 64],
    }
    return {
        "content_descriptor": descriptor,
        "content_identity": _canonical_sha256(descriptor),
    }


class ThreeSiteStagingImageInventoryTests(unittest.TestCase):
    def _document(self):
        release = "a" * 40
        return {
            "schema": "three-site-staging-image-inventory-v2",
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
                    **_content("1"),
                },
                {
                    "reference": f"trading_bot_postgres_boottime:15-{release}",
                    "image_id": "sha256:" + "2" * 64,
                    "repo_digests": [],
                    "release_label": release,
                    **_content("2"),
                },
                {
                    "reference": "redis:7-alpine",
                    "image_id": "sha256:" + "3" * 64,
                    "repo_digests": ["redis@sha256:" + "4" * 64],
                    "release_label": None,
                    **_content("3"),
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

    def test_forged_content_identity_fails(self):
        document = self._document()
        document["images"][0]["content_identity"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ImageInventoryError, "content_identity"):
            verify_image_document(
                document,
                role="webapp-fi",
                campaign_id=document["campaign_id"],
                release_sha=document["release_sha"],
                role_compose_sha256=document["role_compose_sha256"],
                role_env_sha256=document["role_env_sha256"],
            )

    def test_content_identity_is_independent_of_docker_image_store_id(self):
        common = {
            "Architecture": "amd64",
            "Os": "linux",
            "Created": "2026-07-22T00:00:00Z",
            "Config": {"Env": ["PATH=/usr/bin"], "Labels": {"release": "abc"}},
            "RootFS": {"Type": "layers", "Layers": ["sha256:" + "a" * 64]},
        }
        legacy = {
            **common,
            "Id": "sha256:" + "1" * 64,
            "RepoDigests": [],
        }
        containerd = {
            **common,
            "Id": "sha256:" + "2" * 64,
            "RepoDigests": ["image@sha256:" + "2" * 64],
        }
        legacy_descriptor, legacy_identity = image_content_descriptor(legacy)
        containerd_descriptor, containerd_identity = image_content_descriptor(containerd)
        self.assertEqual(legacy_descriptor, containerd_descriptor)
        self.assertEqual(legacy_identity, containerd_identity)


if __name__ == "__main__":
    unittest.main()
