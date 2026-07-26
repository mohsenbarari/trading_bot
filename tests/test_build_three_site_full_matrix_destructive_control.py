from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from scripts.build_three_site_full_matrix_destructive_control import (
    DestructiveControlBuildError,
    build_payload,
)


class BuildThreeSiteFullMatrixDestructiveControlTests(unittest.TestCase):
    def test_shared_host_binding_carries_no_provider_capability(self):
        payload = build_payload(
            campaign_id=str(uuid4()),
            gate_group_id=str(uuid4()),
            execution_class="shared-host-safe",
            release_sha="a" * 40,
        )
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["provider_state_file"], "")
        self.assertEqual(payload["provider_token_file"], "")
        self.assertEqual(payload["audit_root"], "")

    def test_dedicated_binding_requires_owner_only_existing_pointers(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            state = root / "hosts.json"
            token = root / "token"
            audit = root / "audit"
            state.write_text("{}\n", encoding="utf-8")
            token.write_text("secret\n", encoding="utf-8")
            state.chmod(0o600)
            token.chmod(0o600)
            audit.mkdir(mode=0o700)
            payload = build_payload(
                campaign_id=str(uuid4()),
                gate_group_id=str(uuid4()),
                execution_class="dedicated-host-destructive",
                release_sha="a" * 40,
                provider_state_file=state,
                provider_token_file=token,
                audit_root=audit,
            )
            self.assertTrue(payload["enabled"])
            self.assertEqual(payload["audit_root"], str(audit))
            with self.assertRaises(DestructiveControlBuildError):
                build_payload(
                    campaign_id=str(uuid4()),
                    gate_group_id=str(uuid4()),
                    execution_class="dedicated-host-destructive",
                    release_sha="a" * 40,
                    provider_state_file=state,
                    provider_token_file=None,
                    audit_root=audit,
                )


if __name__ == "__main__":
    unittest.main()
