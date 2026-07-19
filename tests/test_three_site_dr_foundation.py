import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO

from sqlalchemy import update

from core.dr_blob_plane import persist_content_addressed_bytes
from core.dr_audit_anchor import build_audit_anchor, store_audit_anchor, verify_audit_anchor
from core.dr_blob_worker import S3Config
from core.dr_delivery_worker import (
    ClaimedDeliveryBatch,
    DrDeliveryError,
    _verify_acknowledgement,
    parse_peer_urls,
)
from core.dr_durability_gate import build_connectivity_state_update, decide_durability
from core.dr_connectivity_classifier import ConnectivityEvidenceError, classify_connectivity
from core.dr_effects import DrEffectError, assert_epoch_bound_effect_execution
from core.dr_event_outbox import DrEventOutboxError, _guard_bulk_or_raw_write, _record_identity, _row_payload
from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import append_hash_chained_jsonl
from core.dr_projection_worker import projection_mode
from core.dr_projection_worker import projection_version_decision
from models.chat_file import ChatFile
from models.user import User
from scripts.verify_three_site_staging_inventory import InventoryError, verify_inventory


class ThreeSiteDrFoundationTests(unittest.TestCase):
    def test_connectivity_classifier_requires_fresh_multi_vantage_hysteresis(self):
        now = datetime.now(timezone.utc)
        rounds = []
        for seconds in (30, 20, 10):
            probes = []
            for vantage in ("iran-a", "iran-b"):
                probes.extend(
                    [
                        {"vantage_id": vantage, "vantage_region": "iran", "target": "webapp_fi", "reachable": False},
                        {"vantage_id": vantage, "vantage_region": "iran", "target": "webapp_ir", "reachable": True},
                        {"vantage_id": vantage, "vantage_region": "iran", "target": "witness", "reachable": True},
                    ]
                )
            probes.append(
                {"vantage_id": "global-a", "vantage_region": "global", "target": "webapp_fi", "reachable": True}
            )
            rounds.append({"observed_at": (now - timedelta(seconds=seconds)).isoformat(), "probes": probes})
        classification = classify_connectivity(rounds, now=now)
        self.assertEqual((classification.mode, classification.confidence), ("isolated", "high"))
        rounds[-1]["observed_at"] = (now - timedelta(hours=1)).isoformat()
        with self.assertRaises(ConnectivityEvidenceError):
            classify_connectivity(rounds, now=now)

        online_rounds = [
            {
                "observed_at": (now - timedelta(seconds=seconds)).isoformat(),
                "probes": [
                    {"vantage_id": vantage, "vantage_region": "iran", "target": target, "reachable": True}
                    for vantage in ("iran-a", "iran-b")
                    for target in ("webapp_fi", "webapp_ir", "witness")
                ],
            }
            for seconds in (15, 10, 5)
        ]
        update = build_connectivity_state_update(
            online_rounds,
            operator="staging-connectivity-controller",
            now=now,
            ttl_seconds=30,
        )
        self.assertEqual(update.classification.mode, "online")
        self.assertEqual(update.evidence_expires_at, now + timedelta(seconds=30))

    def test_versioned_off_host_audit_anchor_round_trip(self):
        class FakeS3:
            def __init__(self):
                self.objects = {}

            def get_bucket_versioning(self, **_kwargs):
                return {"Status": "Enabled"}

            def put_object(self, **kwargs):
                self.objects[kwargs["Key"]] = {
                    "body": bytes(kwargs["Body"]),
                    "metadata": dict(kwargs["Metadata"]),
                    "version": "version-1",
                }
                return {"VersionId": "version-1"}

            def get_object(self, **kwargs):
                item = self.objects[kwargs["Key"]]
                if kwargs.get("VersionId") not in {None, item["version"]}:
                    raise AssertionError("wrong version")
                return {
                    "Body": BytesIO(item["body"]),
                    "Metadata": item["metadata"],
                    "VersionId": item["version"],
                }

        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            append_hash_chained_jsonl(audit, {"event": "promotion-planned"})
            with patch("core.dr_audit_anchor.settings.dr_audit_anchor_object_prefix", "staging/audit"):
                payload, key = build_audit_anchor(
                    audit,
                    site="orchestrator",
                    log_id="campaign-1",
                    release_sha="a" * 40,
                )
            client = FakeS3()
            config = S3Config("https://example.invalid", "test", "bucket", "access-key", "s" * 32)
            anchored = store_audit_anchor(config, payload, key, client=client)
            verified = verify_audit_anchor(
                config,
                expected_payload=payload,
                key=key,
                version_id=anchored["object_version_id"],
                client=client,
            )
            self.assertEqual(verified["status"], "verified")

    def test_content_addressed_blob_is_idempotent_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            digest, path = persist_content_addressed_bytes(b"immutable", root=directory)
            second_digest, second_path = persist_content_addressed_bytes(b"immutable", root=directory)
            self.assertEqual((digest, path), (second_digest, second_path))
            self.assertEqual(Path(path).read_bytes(), b"immutable")
            Path(path).write_bytes(b"corrupt")
            with self.assertRaisesRegex(Exception, "hash/size"):
                persist_content_addressed_bytes(b"immutable", root=directory)

    def test_generic_outbox_payload_is_canonical_and_sensitive_fields_are_dropped(self):
        user = User(id=12, full_name="User", admin_password_hash="secret")
        payload = _row_payload(user)
        self.assertEqual(_record_identity(user), "12")
        self.assertEqual(payload["full_name"], "User")
        self.assertNotIn("admin_password_hash", payload)
        canonical_json_bytes(payload)

    def test_bulk_business_mutation_cannot_bypass_dr_outbox(self):
        state = SimpleNamespace(
            execution_options={},
            statement=update(User).where(User.id == 1).values(full_name="unsafe"),
        )
        with patch("core.dr_event_outbox.settings.dr_event_protocol_enabled", True):
            with self.assertRaisesRegex(DrEventOutboxError, "bypass"):
                _guard_bulk_or_raw_write(state)

        state.execution_options = {"is_sync": True}
        with patch.multiple(
            "core.dr_event_outbox.settings",
            dr_event_protocol_enabled=True,
            dr_event_protocol_strict=True,
        ), patch("core.writer_fencing.current_projection_fence_context", return_value=None):
            with self.assertRaisesRegex(DrEventOutboxError, "cannot bypass"):
                _guard_bulk_or_raw_write(state)

    def test_projection_policy_separates_product_and_webapp_replica_data(self):
        self.assertEqual(
            projection_mode(table_name="offers", origin_site="bot_fi", destination_site="webapp_fi"),
            "legacy",
        )
        self.assertEqual(
            projection_mode(table_name="messages", origin_site="webapp_ir", destination_site="webapp_fi"),
            "generic",
        )
        self.assertEqual(
            projection_mode(table_name="messages", origin_site="webapp_ir", destination_site="bot_fi"),
            "noop",
        )
        self.assertEqual(
            projection_mode(table_name="push_subscriptions", origin_site="webapp_ir", destination_site="webapp_fi"),
            "noop",
        )

    def test_peer_urls_require_complete_https_topology(self):
        raw = json.dumps(
            [
                {"site": "webapp_fi", "base_url": "https://fi.internal.example"},
            ]
        )
        result = parse_peer_urls(raw, local_site="bot_fi")
        self.assertEqual(set(result), {"webapp_fi"})
        with self.assertRaises(DrDeliveryError):
            parse_peer_urls(raw.replace("https://", "http://", 1), local_site="bot_fi")

    def test_acknowledgement_is_bound_to_request_event_and_hash(self):
        envelope = {
            "protocol_version": 1,
            "event_id": "00000000-0000-4000-8000-000000000001",
            "origin_authority": "webapp",
            "origin_physical_site": "webapp_fi",
            "producer_epoch": 2,
            "producer_sequence": 1,
            "aggregate_type": "users",
            "aggregate_id": "1",
            "aggregate_db_id": "1",
            "aggregate_version": 1,
            "operation": "UPDATE",
            "canonical_payload": {"id": 1},
            "canonical_payload_hash": __import__("hashlib").sha256(canonical_json_bytes({"id": 1})).hexdigest(),
            "schema_version": 1,
            "causation_id": None,
            "idempotency_key": None,
            "writer_epoch": 2,
            "tombstone": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        event_hash = __import__("hashlib").sha256(canonical_json_bytes(envelope)).hexdigest()
        batch = ClaimedDeliveryBatch("claim", "webapp_ir", (envelope["event_id"],), (envelope,))
        unsigned = {
            "destination_site": "webapp_ir",
            "request_hash": "a" * 64,
            "results": [{"event_id": envelope["event_id"], "status": "received", "envelope_hash": event_hash}],
        }
        payload = {
            **unsigned,
            "acknowledgement_hash": __import__("hashlib").sha256(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        self.assertIn(envelope["event_id"], _verify_acknowledgement(payload, batch=batch, request_hash="a" * 64))
        payload["results"][0]["envelope_hash"] = "b" * 64
        with self.assertRaises(DrDeliveryError):
            _verify_acknowledgement(payload, batch=batch, request_hash="a" * 64)

    def test_strict_webapp_provider_call_requires_effect_capability(self):
        with patch.multiple(
            "core.dr_effects.settings",
            three_site_dr_enabled=True,
            dr_event_protocol_strict=True,
            topology_schema_version="three-site-dr-v1",
            logical_authority="webapp",
            physical_site="webapp_fi",
            server_mode="iran",
        ):
            with self.assertRaises(DrEffectError):
                assert_epoch_bound_effect_execution(provider="smsir")

    def test_durability_freezes_critical_isolated_writes_without_local_journal(self):
        now = datetime.now(timezone.utc)
        blocked = decide_durability(
            table_names={"trades"},
            connectivity_mode="isolated",
            event_journal_healthy=False,
            blob_journal_healthy=False,
            evidence_expires_at=now + timedelta(minutes=1),
            now=now,
        )
        self.assertFalse(blocked.allowed)
        allowed = decide_durability(
            table_names={"trades"},
            connectivity_mode="isolated",
            event_journal_healthy=True,
            blob_journal_healthy=False,
            evidence_expires_at=now + timedelta(minutes=1),
            now=now,
            isolated_critical_write_policy="same_region_sync",
        )
        self.assertTrue(allowed.allowed)
        owner_unapproved = decide_durability(
            table_names={"trades"},
            connectivity_mode="isolated",
            event_journal_healthy=True,
            blob_journal_healthy=True,
            evidence_expires_at=now + timedelta(minutes=1),
            now=now,
        )
        self.assertFalse(owner_unapproved.allowed)
        self.assertIn("isolated_critical_writes_not_owner_approved", owner_unapproved.reasons)
        session_write = decide_durability(
            table_names={"user_sessions"},
            connectivity_mode="ambiguous",
            event_journal_healthy=False,
            blob_journal_healthy=False,
            evidence_expires_at=None,
            now=now,
        )
        self.assertTrue(session_write.allowed)

    def test_projection_version_rejects_old_term_and_same_epoch_other_site(self):
        self.assertEqual(
            projection_version_decision(
                stored_epoch=8,
                stored_sequence=4,
                stored_origin_site="webapp_ir",
                incoming_epoch=7,
                incoming_sequence=99,
                incoming_origin_site="webapp_fi",
            ),
            "stale",
        )
        self.assertEqual(
            projection_version_decision(
                stored_epoch=8,
                stored_sequence=4,
                stored_origin_site="webapp_ir",
                incoming_epoch=8,
                incoming_sequence=5,
                incoming_origin_site="webapp_fi",
            ),
            "conflict",
        )
        self.assertEqual(
            projection_version_decision(
                stored_epoch=8,
                stored_sequence=4,
                stored_origin_site="webapp_ir",
                incoming_epoch=9,
                incoming_sequence=1,
                incoming_origin_site="webapp_fi",
            ),
            "apply",
        )

    def test_authoritative_inventory_rejects_production_and_shared_hosts(self):
        payload = json.loads(
            Path("deploy/staging/three-site-inventory.example.json").read_text(encoding="utf-8")
        )
        with self.assertRaises(InventoryError):
            verify_inventory(payload, host_destructive=True)
        payload["campaign_id"] = "11111111-1111-4111-8111-111111111111"
        payload["release_sha"] = "a" * 40
        payload["deployment_id"] = "staging-deployment-20260719"
        payload["object_storage"].update(
            bucket="staging-three-site-dr",
            prefix="staging/campaign-20260719/",
            credential_id="staging-s3-credential-v1",
        )
        for field in (
            "machine_ids", "docker_daemon_ids", "postgres_system_ids",
            "volume_ids", "audit_root_ids",
        ):
            payload["production_boundaries"][field] = [f"production-{field}-1"]
        for number, role in enumerate(payload["roles"], 1):
            role["host_ip"] = f"10.20.0.{number}"
            role["release_sha"] = payload["release_sha"]
            role["deployment_id"] = payload["deployment_id"]
            for field in (
                "machine_id", "docker_daemon_id", "postgres_system_id",
                "postgres_volume_id", "redis_volume_id", "uploads_volume_id",
                "audit_root_id",
            ):
                if role[field] is not None:
                    role[field] = f"staging-{role['role']}-{field}"
        approved = verify_inventory(payload, host_destructive=True)
        self.assertEqual(approved["status"], "approved")
        valid_payload = json.loads(json.dumps(payload))
        payload["roles"][0]["host_ip"] = "65.109.216.187"
        with self.assertRaises(InventoryError):
            verify_inventory(payload, host_destructive=True)

        payload = valid_payload
        payload["production_boundaries"]["domains"].append("staging.gold-trade.ir")
        with self.assertRaises(InventoryError):
            verify_inventory(payload, host_destructive=True)


if __name__ == "__main__":
    unittest.main()
