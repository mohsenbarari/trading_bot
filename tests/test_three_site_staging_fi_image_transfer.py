from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from scripts.transfer_three_site_staging_image_bundle_fi import (
    FinlandImageTransferError,
    SCHEMA,
    confirmation_phrase,
    verify_transfer_evidence,
)


class ThreeSiteStagingFinlandImageTransferTests(unittest.TestCase):
    @staticmethod
    def _evidence() -> dict:
        return {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "loaded-attested-and-returned",
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "release_sha": "a" * 40,
            "source_role": "bot_fi",
            "destination_role": "webapp_fi",
            "source_host_ip": "192.0.2.11",
            "destination_host_ip": "192.0.2.12",
            "bundle": {
                "sha256": "b" * 64,
                "bytes": 1234,
                "remote_retained_until_cleanup": True,
            },
            "transport": {
                "kind": "direct-rsync-over-ssh-finland",
                "encrypted": True,
                "resumable": True,
                "strict_host_key_checking": True,
                "object_storage_used": False,
                "arvan_endpoint_contacted": False,
            },
            "remote_image_inventory_sha256": "c" * 64,
            "database_restarted": False,
            "application_started": False,
        }

    def _verify(self, document: dict):
        return verify_transfer_evidence(
            document,
            campaign_id="11111111-1111-4111-8111-111111111111",
            release_sha="a" * 40,
            source_host_ip="192.0.2.11",
            destination_host_ip="192.0.2.12",
            bundle_sha256="b" * 64,
            bundle_bytes=1234,
            inventory_sha256="c" * 64,
        )

    def test_exact_direct_finland_transport_is_accepted(self):
        self.assertEqual(self._verify(self._evidence())["status"], "verified")
        self.assertEqual(
            confirmation_phrase(
                "11111111-1111-4111-8111-111111111111",
                "a" * 40,
                "b" * 64,
            ),
            "transfer-fi-images:"
            "11111111-1111-4111-8111-111111111111:"
            f"{'a' * 40}:{'b' * 64}",
        )

    def test_object_storage_fallback_is_rejected_for_webapp_fi(self):
        evidence = deepcopy(self._evidence())
        evidence["transport"]["object_storage_used"] = True
        with self.assertRaisesRegex(
            FinlandImageTransferError, "transport contract"
        ):
            self._verify(evidence)

    def test_arvan_contact_is_rejected_for_webapp_fi(self):
        evidence = deepcopy(self._evidence())
        evidence["transport"]["arvan_endpoint_contacted"] = True
        with self.assertRaisesRegex(
            FinlandImageTransferError, "transport contract"
        ):
            self._verify(evidence)

    def test_role_pair_cannot_be_relabelled(self):
        evidence = deepcopy(self._evidence())
        evidence["destination_role"] = "webapp_ir"
        with self.assertRaisesRegex(
            FinlandImageTransferError, "identity is invalid"
        ):
            self._verify(evidence)


if __name__ == "__main__":
    unittest.main()
