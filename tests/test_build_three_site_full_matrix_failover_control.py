from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import uuid

from scripts.build_three_site_full_matrix_failover_control import (
    FullMatrixFailoverControlBuildError,
    SCHEMA,
    build,
)


class BuildFullMatrixFailoverControlTests(unittest.TestCase):
    def test_builds_secret_free_control_with_owner_only_references(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            campaign = str(uuid.uuid4())
            group = str(uuid.uuid4())
            release = "a" * 40
            backend = root / "backend.json"
            relay = root / "relay.env"
            witness_key = root / "witness.pub"
            backend.write_text(
                json.dumps(
                    {
                        "schema": "three-site-staging-failover-backend-v1",
                        "campaign_id": campaign,
                        "release_sha": release,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            relay.write_text("secret-reference-not-emitted\n", encoding="utf-8")
            witness_key.write_text("public-key\n", encoding="utf-8")
            for path in (backend, relay, witness_key):
                path.chmod(0o600)
            result = build(
                campaign_id=campaign,
                gate_group_id=group,
                execution_class="shared-host-safe",
                release_sha=release,
                backend_config=backend,
                relay_credentials=relay,
                witness_relay_public_key_file=witness_key,
                journal_root=root / "journal",
            )
            self.assertEqual(result["schema"], SCHEMA)
            self.assertEqual(result["campaign_id"], campaign)
            self.assertNotIn("secret-reference-not-emitted", json.dumps(result))
            self.assertEqual((root / "journal").stat().st_mode & 0o777, 0o700)

    def test_rejects_backend_identity_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            backend = root / "backend.json"
            relay = root / "relay.env"
            witness_key = root / "witness.pub"
            backend.write_text(
                json.dumps(
                    {
                        "schema": "three-site-staging-failover-backend-v1",
                        "campaign_id": str(uuid.uuid4()),
                        "release_sha": "a" * 40,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            relay.write_text("relay\n", encoding="utf-8")
            witness_key.write_text("key\n", encoding="utf-8")
            for path in (backend, relay, witness_key):
                path.chmod(0o600)
            with self.assertRaises(FullMatrixFailoverControlBuildError):
                build(
                    campaign_id=str(uuid.uuid4()),
                    gate_group_id=str(uuid.uuid4()),
                    execution_class="shared-host-safe",
                    release_sha="a" * 40,
                    backend_config=backend,
                    relay_credentials=relay,
                    witness_relay_public_key_file=witness_key,
                    journal_root=root / "journal",
                )

    def test_rejects_duplicate_backend_json_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            campaign = str(uuid.uuid4())
            backend = root / "backend.json"
            relay = root / "relay.env"
            witness_key = root / "witness.pub"
            backend.write_text(
                "{"
                f"\"schema\":\"three-site-staging-failover-backend-v1\","
                f"\"campaign_id\":\"{campaign}\","
                f"\"campaign_id\":\"{campaign}\","
                "\"release_sha\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\""
                "}\n",
                encoding="utf-8",
            )
            relay.write_text("relay\n", encoding="utf-8")
            witness_key.write_text("key\n", encoding="utf-8")
            for path in (backend, relay, witness_key):
                path.chmod(0o600)
            with self.assertRaises(FullMatrixFailoverControlBuildError):
                build(
                    campaign_id=campaign,
                    gate_group_id=str(uuid.uuid4()),
                    execution_class="shared-host-safe",
                    release_sha="a" * 40,
                    backend_config=backend,
                    relay_credentials=relay,
                    witness_relay_public_key_file=witness_key,
                    journal_root=root / "journal",
                )


if __name__ == "__main__":
    unittest.main()
