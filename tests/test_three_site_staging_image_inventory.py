from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from scripts.verify_three_site_staging_image_inventory import (
    ImageInventoryError,
    _canonical_sha256,
    _sealed_memfd,
    collect_image_document,
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

    def test_third_party_repository_digest_requires_exact_sha256_grammar(self):
        invalid = (
            "redis@sha256:x",
            "redis@sha256:" + "A" * 64,
            "@sha256:" + "4" * 64,
            "Redis@sha256:" + "4" * 64,
            "registry.example.com:5000//redis@sha256:" + "4" * 64,
        )
        for digest in invalid:
            with self.subTest(digest=digest):
                document = self._document()
                document["images"][-1]["repo_digests"] = [digest]
                with self.assertRaisesRegex(ImageInventoryError, "digest"):
                    verify_image_document(
                        document,
                        role="webapp-fi",
                        campaign_id=document["campaign_id"],
                        release_sha=document["release_sha"],
                        role_compose_sha256=document["role_compose_sha256"],
                        role_env_sha256=document["role_env_sha256"],
                    )

    def test_registry_path_and_port_repository_digest_passes(self):
        document = self._document()
        document["images"][-1]["repo_digests"] = [
            "registry.example.com:5000/library/redis@sha256:" + "4" * 64
        ]
        result = verify_image_document(
            document,
            role="webapp-fi",
            campaign_id=document["campaign_id"],
            release_sha=document["release_sha"],
            role_compose_sha256=document["role_compose_sha256"],
            role_env_sha256=document["role_env_sha256"],
        )
        self.assertEqual(result["status"], "verified")

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

    def test_verified_compose_and_env_bytes_are_passed_through_sealed_memfds(self):
        release = "a" * 40
        compose_bytes = b"services: {}\n"
        env_bytes = b"STAGING_RELEASE_SHA=" + release.encode() + b"\n"
        references = (
            f"trading_bot_three_site_staging:{release}",
            f"trading_bot_postgres_boottime:15-{release}",
            "redis:7-alpine",
        )
        observed_inputs = []

        def run(arguments, *, timeout=60, pass_fds=()):  # noqa: ARG001
            if "config" in arguments:
                self.assertEqual(len(pass_fds), 2)
                observed_inputs.extend(os.pread(fd, 4096, 0) for fd in pass_fds)
                return "\n".join(references)
            reference = arguments[-1]
            number = references.index(reference) + 1
            return json.dumps(
                [
                    {
                        "Id": "sha256:" + str(number) * 64,
                        "Architecture": "amd64",
                        "Os": "linux",
                        "Created": "2026-07-22T00:00:00Z",
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": (
                                    release if number < 3 else None
                                )
                            }
                        },
                        "RootFS": {
                            "Type": "layers",
                            "Layers": ["sha256:" + str(number) * 64],
                        },
                        "RepoDigests": (
                            []
                            if number < 3
                            else ["redis@sha256:" + "4" * 64]
                        ),
                    }
                ]
            )

        with patch(
            "scripts.verify_three_site_staging_image_inventory._run",
            side_effect=run,
        ):
            document = collect_image_document(
                role="webapp-fi",
                campaign_id="11111111-1111-4111-8111-111111111111",
                release_sha=release,
                role_compose_bytes=compose_bytes,
                env_bytes=env_bytes,
            )
        self.assertEqual(observed_inputs, [compose_bytes, env_bytes])
        self.assertEqual(
            document["role_compose_sha256"],
            hashlib.sha256(compose_bytes).hexdigest(),
        )
        self.assertEqual(
            document["role_env_sha256"],
            hashlib.sha256(env_bytes).hexdigest(),
        )

    def test_memfd_is_write_sealed_before_subprocess_use(self):
        descriptor = _sealed_memfd(b"exact\n", label="test")
        try:
            self.assertEqual(os.pread(descriptor, 6, 0), b"exact\n")
            with self.assertRaises(OSError):
                os.pwrite(descriptor, b"X", 0)
        finally:
            os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
