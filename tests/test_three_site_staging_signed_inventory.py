from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from core.human_approval import approval_subject
from core.human_approval_issuer import (
    authenticate_and_issue,
    create_enrollment,
    totp_code,
)

from scripts.verify_three_site_staging_inventory import (
    DEDICATED_HOST_DESTRUCTIVE,
    InventoryError,
    ROLE_COMPOSE_PROJECT,
    ROLE_VOLUME_LOGICAL_NAMES,
    verify_approved_inventory,
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
        "volume_ids", "audit_root_ids", "storage_mount_uuids",
    ):
        payload["production_boundaries"][field] = [f"production-{field}-1"]
    for number, role in enumerate(payload["roles"], 1):
        role["host_ip"] = f"10.30.0.{number}"
        role["release_sha"] = payload["release_sha"]
        role["deployment_id"] = payload["deployment_id"]
        role["machine_id"] = f"{number:032x}"
        role["docker_daemon_id"] = f"staging-docker-{role['role']}"
        role["postgres_system_id"] = str(9000000000000000000 + number)
        role["storage_mount_uuid"] = f"00000000-0000-4000-8000-{number:012d}"
        for field, logical in ROLE_VOLUME_LOGICAL_NAMES[role["role"]].items():
            role[field] = f"{ROLE_COMPOSE_PROJECT[role['role']]}_{logical}"
    return payload


def _signed_documents(payload: dict, now: datetime):
    enrollment = create_enrollment(
        operator="person-1",
        password="test approval passphrase value",
        now=now,
        scrypt_n=2**14,
    )
    subject = approval_subject(
        artifact_type="three-site-staging-inventory-v3",
        artifact_sha256=__import__("hashlib").sha256(_canonical(payload)).hexdigest(),
        release_sha=payload["release_sha"],
        bindings={
            "campaign_id": payload["campaign_id"],
            "deployment_id": payload["deployment_id"],
            "host_safety_mode": payload["host_safety_mode"],
            "inventory_stage": payload["inventory_stage"],
        },
    )
    approval, _state, _audit = authenticate_and_issue(
        secrets_payload=enrollment.secrets_payload,
        state_payload=enrollment.state_payload,
        policy_payload=enrollment.policy_payload,
        private_key_envelope=enrollment.private_key_envelope,
        password="test approval passphrase value",
        totp=totp_code(enrollment.totp_secret, at=now)[1],
        recovery_code=None,
        action="approve_inventory",
        environment="staging",
        subject=subject,
        ttl_seconds=3600,
        now=now,
    )
    return enrollment.policy_payload, approval


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

        payload = _inventory()
        payload["host_safety_mode"] = SHARED_HOST_SAFE
        payload["production_boundaries"]["storage_mount_uuids"] = [
            payload["roles"][0]["storage_mount_uuid"]
        ]
        with self.assertRaisesRegex(InventoryError, "production storage"):
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
        payload["schema"] = "three-site-staging-inventory-v2"
        with self.assertRaisesRegex(InventoryError, "fields/schema"):
            verify_inventory(payload)

    def test_legacy_two_device_policy_and_approval_are_rejected(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy = {
            "schema": "three-site-staging-inventory-signers-v1",
            "policy_id": "22222222-2222-4222-8222-222222222222",
            "release_sha": payload["release_sha"],
            "signers": [],
        }
        approval = {
            "schema": "three-site-staging-inventory-approval-v1",
            "inventory_sha256": "0" * 64,
            "release_sha": payload["release_sha"],
            "policy_hash": "0" * 64,
            "signed_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "approvals": [],
        }
        with self.assertRaisesRegex(InventoryError, "human approval is invalid"):
            verify_approved_inventory(
                payload,
                approval=approval,
                approval_policy=policy,
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

        result = verify_approved_inventory(
            payload,
            approval=approval,
            approval_policy=policy,
            host_destructive=True,
            now=now,
        )

        self.assertEqual(result["inventory_stage"], "planned")

    def test_exact_inventory_requires_valid_password_totp_approval(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now)

        result = verify_approved_inventory(
            payload,
            approval=approval,
            approval_policy=policy,
            host_destructive=True,
            now=now,
        )

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["approved_by"], ["person-1"])
        self.assertTrue(result["approval_id"])

    def test_inventory_mutation_after_approval_is_rejected(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now)
        payload["roles"][0]["host_ip"] = "10.30.0.99"

        with self.assertRaisesRegex(InventoryError, "human approval is invalid"):
            verify_approved_inventory(
                payload,
                approval=approval,
                approval_policy=policy,
                host_destructive=True,
                now=now,
            )

    def test_expired_or_policy_tampered_approval_is_rejected(self):
        now = datetime.now(timezone.utc)
        payload = _inventory()
        policy, approval = _signed_documents(payload, now)
        policy["issuer"]["operator"] = "other-person"
        with self.assertRaises(InventoryError):
            verify_approved_inventory(
                payload,
                approval=approval,
                approval_policy=policy,
                host_destructive=True,
                now=now,
            )

        policy, approval = _signed_documents(payload, now - timedelta(hours=2))
        with self.assertRaisesRegex(InventoryError, "human approval is invalid"):
            verify_approved_inventory(
                payload,
                approval=approval,
                approval_policy=policy,
                host_destructive=True,
                now=now,
            )


if __name__ == "__main__":
    unittest.main()
