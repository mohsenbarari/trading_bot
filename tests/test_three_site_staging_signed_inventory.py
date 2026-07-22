from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts.verify_three_site_staging_inventory import (
    DEDICATED_HOST_DESTRUCTIVE,
    InventoryError,
    ROLE_COMPOSE_PROJECT,
    ROLE_VOLUME_LOGICAL_NAMES,
    verify_signed_inventory,
    verify_inventory,
)
from core.three_site_execution_safety import SHARED_HOST_SAFE


def _canonical(value) -> bytes:  # noqa: ANN001
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _inventory() -> dict:
    payload = json.loads(
        Path("deploy/staging/three-site-inventory.example.json").read_text(
            encoding="utf-8"
        )
    )
    payload.update(
        inventory_stage="provisioned",
        host_safety_mode=DEDICATED_HOST_DESTRUCTIVE,
        campaign_id="11111111-1111-4111-8111-111111111111",
        release_sha="a" * 40,
        deployment_id="staging-deployment-signed-test",
    )
    payload["object_storage"].update(
        bucket="staging-three-site-dr",
        prefix="staging/signed-inventory-test/",
        credential_id="staging-s3-credential-v1",
    )
    for field in (
        "machine_ids", "docker_daemon_ids", "postgres_system_ids",
        "volume_ids", "audit_root_ids",
    ):
        payload["production_boundaries"][field] = [f"production-{field}-1"]
    for number, role in enumerate(payload["roles"], 1):
        role["host_ip"] = f"10.30.0.{number}"
        role["release_sha"] = payload["release_sha"]
        role["deployment_id"] = payload["deployment_id"]
        role["machine_id"] = f"{number:032x}"
        role["docker_daemon_id"] = f"staging-docker-{role['role']}"
        role["postgres_system_id"] = str(9000000000000000000 + number)
        for field, logical in ROLE_VOLUME_LOGICAL_NAMES[role["role"]].items():
            role[field] = f"{ROLE_COMPOSE_PROJECT[role['role']]}_{logical}"
    return payload


def _signed_documents(payload: dict, now: datetime, *, reuse_public_key: bool = False):
    private_keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    signing_keys = [private_keys[0], private_keys[0] if reuse_public_key else private_keys[1]]
    policy = {
        "schema": "three-site-staging-inventory-signers-v1",
        "policy_id": "22222222-2222-4222-8222-222222222222",
        "release_sha": payload["release_sha"],
        "signers": [
            {
                "key_id": f"inventory-operator-{number}",
                "operator": f"person-{number}",
                "custody_domain": f"device-{number}",
                "public_key": base64.b64encode(
                    private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                ).decode(),
            }
            for number, private in enumerate(signing_keys, 1)
        ],
    }
    unsigned = {
        "schema": "three-site-staging-inventory-approval-v1",
        "inventory_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
        "release_sha": payload["release_sha"],
        "policy_hash": hashlib.sha256(_canonical(policy)).hexdigest(),
        "signed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    approval = {
        **unsigned,
        "approvals": [
            {
                "operator": f"person-{number}",
                "key_id": f"inventory-operator-{number}",
                "signature": base64.b64encode(private.sign(_canonical(unsigned))).decode(),
            }
            for number, private in enumerate(signing_keys, 1)
        ],
    }
    return policy, approval


class ThreeSiteStagingSignedInventoryTests(unittest.TestCase):
    def test_shared_host_mode_allows_host_identity_reuse_but_not_mutable_data_reuse(self):
        payload = _inventory()
        payload["host_safety_mode"] = SHARED_HOST_SAFE
        first = payload["roles"][0]
        first["host_ip"] = "65.109.220.59"
        first["machine_id"] = "f" * 32
        first["docker_daemon_id"] = "production-docker-daemon"
        payload["production_boundaries"]["host_ips"] = [first["host_ip"]]
        payload["production_boundaries"]["machine_ids"] = [first["machine_id"]]
        payload["production_boundaries"]["docker_daemon_ids"] = [
            first["docker_daemon_id"]
        ]

        approved = verify_inventory(payload, host_destructive=False)

        self.assertEqual(approved["host_safety_mode"], SHARED_HOST_SAFE)
        self.assertFalse(approved["host_destructive"])

        payload["production_boundaries"]["volume_ids"] = [
            first["postgres_volume_id"]
        ]
        with self.assertRaisesRegex(InventoryError, "production boundary"):
            verify_inventory(payload, host_destructive=False)

    def test_execution_class_mismatch_and_destructive_production_host_are_rejected(self):
        payload = _inventory()
        payload["host_safety_mode"] = SHARED_HOST_SAFE
        with self.assertRaisesRegex(InventoryError, "differs"):
            verify_inventory(payload, host_destructive=True)

        payload["host_safety_mode"] = DEDICATED_HOST_DESTRUCTIVE
        payload["roles"][0]["host_ip"] = "65.109.220.59"
        with self.assertRaisesRegex(InventoryError, "production host"):
            verify_inventory(payload, host_destructive=True)

    def test_legacy_inventory_schema_is_rejected(self):
        payload = _inventory()
        payload["schema"] = "three-site-staging-inventory-v1"
        with self.assertRaisesRegex(InventoryError, "fields/schema"):
            verify_inventory(payload)

    def test_signer_policy_cannot_alias_one_key_as_two_people(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now, reuse_public_key=True)
        with self.assertRaisesRegex(InventoryError, "public keys are not independent"):
            verify_signed_inventory(
                payload,
                approval=approval,
                signer_policy=policy,
                host_destructive=True,
                now=now,
            )

    def test_planned_inventory_can_be_signed_before_postgres_identity_exists(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        payload["inventory_stage"] = "planned"
        for role in payload["roles"]:
            role["postgres_system_id"] = None
        policy, approval = _signed_documents(payload, now)

        result = verify_signed_inventory(
            payload,
            approval=approval,
            signer_policy=policy,
            host_destructive=True,
            now=now,
        )

        self.assertEqual(result["inventory_stage"], "planned")

    def test_exact_inventory_requires_two_independent_valid_signatures(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now)

        result = verify_signed_inventory(
            payload,
            approval=approval,
            signer_policy=policy,
            host_destructive=True,
            now=now,
        )

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["approved_by"], ["person-1", "person-2"])

    def test_inventory_mutation_after_approval_is_rejected(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now)
        payload["roles"][0]["host_ip"] = "10.30.0.99"

        with self.assertRaisesRegex(InventoryError, "not bound"):
            verify_signed_inventory(
                payload,
                approval=approval,
                signer_policy=policy,
                host_destructive=True,
                now=now,
            )

    def test_expired_or_same_custody_approval_is_rejected(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now)
        policy["signers"][1]["custody_domain"] = "device-1"
        approval["policy_hash"] = hashlib.sha256(_canonical(policy)).hexdigest()
        with self.assertRaises(InventoryError):
            verify_signed_inventory(
                payload,
                approval=approval,
                signer_policy=policy,
                host_destructive=True,
                now=now,
            )

        policy, approval = _signed_documents(payload, now - timedelta(hours=2))
        with self.assertRaisesRegex(InventoryError, "expired"):
            verify_signed_inventory(
                payload,
                approval=approval,
                signer_policy=policy,
                host_destructive=True,
                now=now,
            )


if __name__ == "__main__":
    unittest.main()
