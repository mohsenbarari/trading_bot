from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.fresh_three_site_staging_preflight_agent import (
    FreshPreflightAgentError,
    PACKAGE_SCHEMA,
    SCHEMA,
    _verify_installed_role_closure,
    _install_role_package,
    load_manifest,
)


class FreshThreeSiteStagingPreflightAgentTests(unittest.TestCase):
    def _manifest(self, root: Path) -> tuple[Path, dict]:
        campaign = str(uuid4())
        deployment = "three-site-preflight-test"
        secure = root / campaign / deployment / "bot-fi"
        secure.mkdir(mode=0o700, parents=True)
        identity = secure / "bootstrap.agekey"
        identity.write_text("AGE-SECRET-KEY-1TEST\n", encoding="utf-8")
        identity.chmod(0o600)
        artifact = {
            "url": "https://bucket.s3.ir-thr-at1.arvanstorage.ir/fresh.age",
            "plaintext_sha256": "a" * 64,
            "plaintext_bytes": 1,
            "ciphertext_sha256": "b" * 64,
            "ciphertext_bytes": 1,
            "age_recipient": "age1testrecipient",
        }
        manifest = {
            "schema": SCHEMA, "role": "bot-fi", "campaign_id": campaign,
            "deployment_id": deployment, "release_sha": "c" * 40,
            "age_identity": str(identity), "secure_dir": str(secure),
            "release_bundle": artifact, "role_package": artifact,
            "evidence_output": str(secure / "evidence/fresh-preflight.json"),
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        path.chmod(0o600)
        return path, manifest

    def test_load_manifest_requires_canonical_private_bootstrap_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with patch("scripts.fresh_three_site_staging_preflight_agent.SECURE_ROOT", root):
                path, manifest = self._manifest(root)
                self.assertEqual(load_manifest(path)["campaign_id"], manifest["campaign_id"])
                path.chmod(0o640)
                with self.assertRaises(FreshPreflightAgentError):
                    load_manifest(path)

    def test_role_package_is_no_replace_and_role_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with patch("scripts.fresh_three_site_staging_preflight_agent.SECURE_ROOT", root), patch(
                "scripts.fresh_three_site_staging_preflight_agent._require_root_private_ancestors"
            ):
                _path, manifest = self._manifest(root)
                entries = {
                    "planned-inventory.json": b"{}\n",
                    "planned-inventory-approval.json": b"{}\n",
                    "human-approval-policy.json": b"{}\n",
                    "roles/bot-fi.compose.yml": b"services: {}\n",
                    "roles/bot-fi.env": b"STAGING_SOURCE_ROOT=/srv/release\n",
                }
                metadata = {
                    name: {"sha256": hashlib.sha256(value).hexdigest(), "mode": 0o600, "bytes": len(value)}
                    for name, value in entries.items()
                }
                metadata["roles/bot-fi.compose.yml"]["mode"] = 0o640
                package_manifest = {
                    "schema": PACKAGE_SCHEMA, "role": "bot-fi",
                    "campaign_id": manifest["campaign_id"], "deployment_id": manifest["deployment_id"],
                    "release_sha": manifest["release_sha"], "files": metadata,
                }
                package = root / "package.tar"
                with tarfile.open(package, "w") as archive:
                    for name, value in {**entries, "role-package-manifest.json": json.dumps(package_manifest).encode()}.items():
                        info = tarfile.TarInfo(name)
                        info.size = len(value)
                        archive.addfile(info, io.BytesIO(value))
                secure, result = _install_role_package(package, manifest=manifest)
                self.assertEqual(result["schema"], PACKAGE_SCHEMA)
                self.assertEqual((secure / "roles/bot-fi.env").read_bytes(), entries["roles/bot-fi.env"])
                with self.assertRaises(FreshPreflightAgentError):
                    _install_role_package(package, manifest=manifest)

    def test_bot_role_rejects_blob_authority_even_when_env_references_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            secure = Path(raw)
            (secure / "roles").mkdir(mode=0o700)
            (secure / "roles/bot-fi.env").write_text(
                "STAGING_DR_BLOB_CREDENTIALS_FILE="
                "/etc/trading-bot-three-site/campaigns/campaign/deployment/secrets/"
                "staging-dr-blob-s3.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FreshPreflightAgentError, "forbidden"):
                _verify_installed_role_closure(
                    secure=secure,
                    role="bot-fi",
                    campaign="campaign",
                    deployment="deployment",
                    files={"secrets/staging-dr-blob-s3.json"},
                )


if __name__ == "__main__":
    unittest.main()
