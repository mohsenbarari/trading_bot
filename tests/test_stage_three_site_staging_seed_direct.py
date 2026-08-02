from __future__ import annotations

import argparse
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.stage_three_site_staging_seed_direct import (
    build_plan,
    confirmation_phrase,
    execute,
)


class StageThreeSiteStagingSeedDirectTests(unittest.TestCase):
    def test_plan_binds_transport_and_exact_campaign(self):
        plan = build_plan(
            campaign_id="11111111-1111-4111-8111-111111111111",
            target_role="bot_fi",
            plan_hash="a" * 64,
            transport_route="bot-fl-controller-to-bot-fi",
        )
        self.assertEqual(plan["transport"], "ssh-host-key-pinned")
        self.assertEqual(
            plan["required_confirmation"],
            confirmation_phrase(plan["campaign_id"], "bot_fi", "a" * 64),
        )

    def test_execute_copies_only_manifest_bound_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {}
            objects = []
            import hashlib

            for kind in ("postgres", "uploads", "audit"):
                source = root / f"source-{kind}"
                if kind == "postgres":
                    source.write_bytes(b"postgres-seed")
                else:
                    with tarfile.open(source, "w:gz") as archive:
                        item = root / f"{kind}.txt"
                        item.write_text(kind, encoding="utf-8")
                        archive.add(item, arcname=f"./{kind}.txt")
                source.chmod(0o600)
                payload = source.read_bytes()
                sources[kind] = source
                objects.append(
                    {
                        "kind": kind,
                        "object_key": f"seed/{kind}",
                        "version_id": f"version-{kind}",
                        "plaintext_sha256": hashlib.sha256(payload).hexdigest(),
                        "plaintext_bytes": len(payload),
                        "ciphertext_sha256": "c" * 64,
                        "ciphertext_bytes": len(payload) + 100,
                    }
                )
            output = root / "output"
            args = argparse.Namespace(
                target_role="bot_fi",
                confirm=confirmation_phrase(
                    "11111111-1111-4111-8111-111111111111", "bot_fi", "a" * 64
                ),
                source_host_key_sha256="SHA256:" + "A" * 43,
                transport_route="bot-fl-controller-to-bot-fi",
                artifact=[f"{kind}={path}" for kind, path in sources.items()],
                output_dir=output,
                repo=root / "repo",
            )
            verified = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "release_sha": "b" * 40,
                "plan_sha256": "a" * 64,
            }
            result = execute(
                args,
                verified_plan=verified,
                seed_manifests={"bot_fi": {"objects": objects}},
            )
            self.assertEqual(result["status"], "target-seed-verified")
            self.assertTrue((output / "target-seed.json").is_file())
            self.assertTrue((output / "direct-transfer.json").is_file())


if __name__ == "__main__":
    unittest.main()
