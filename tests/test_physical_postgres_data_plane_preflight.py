from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from core.physical_postgres_data_plane_preflight import (
    ARCHIVE_BOUNDED_RPO,
    ARCHIVE_ONLY_DELIVERY,
    PREFLIGHT_STATUS_BLOCKED,
    PREFLIGHT_STATUS_OBSERVED,
    STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY,
    STRICT_ZERO_LOSS,
    PhysicalPostgresCommandDescriptor,
    PhysicalPostgresDataPlanePreflightBinding,
    PhysicalPostgresDataPlanePreflightError,
    PhysicalPostgresEvidenceProvenance,
    PhysicalPostgresReadbackEvidence,
    PhysicalPostgresWriterTermBinding,
    assess_physical_postgres_data_plane_preflight,
    canonical_physical_postgres_readback_bytes,
    require_observed_physical_postgres_data_plane_preflight,
    verify_physical_postgres_readback_evidence,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
EVIDENCE_SCHEMA = "gold-trade-physical-postgres-readback-evidence-v1"
COLLECTOR = "read-only-postgres-settings-agent-v1"


def digest(character: str) -> str:
    return character * 64


def canonical(payload: dict[str, object]) -> bytes:
    return canonical_physical_postgres_readback_bytes(payload)


def evidence(payload: dict[str, object]) -> PhysicalPostgresReadbackEvidence:
    raw = canonical(payload)
    sha256 = hashlib.sha256(raw).hexdigest()
    site = str(payload["site"])
    return PhysicalPostgresReadbackEvidence(
        raw_evidence=raw,
        evidence_sha256=sha256,
        provenance=PhysicalPostgresEvidenceProvenance(
            site=site,
            collector_identity=COLLECTOR,
            logical_path=f"physical-postgres-preflight/{site}/readback-v1.json",
            evidence_sha256=sha256,
        ),
    )


def command(identity: str, hash_character: str) -> dict[str, str]:
    return {"identity": identity, "sha256": digest(hash_character)}


def disabled_synchronous_standby() -> dict[str, object]:
    return {
        "mode": "disabled",
        "receiver_site": None,
        "receiver_application_name": None,
        "receiver_slot_name": None,
        "receiver_evidence_sha256": None,
        "acknowledgement": "none",
        "transport": "none",
    }


def writer_term() -> dict[str, object]:
    return {
        "holder_site": "webapp_fi",
        "writer_epoch": 41,
        "writer_lease_id": "writer-lease-41",
        "witness_transition_id": "witness-transition-41",
        "proof_sha256": digest("1"),
    }


def readback_payload(
    *,
    site: str,
    role: str,
    claim: str,
    delivery_mode: str,
    synchronous_commit: str,
    synchronous_standby: dict[str, object],
    source_wal_frontier: str | None,
    archived_wal_frontier: str,
    replay_frontier: str | None,
    acknowledged_wal_frontier: str | None,
    observed_at: datetime = NOW,
) -> dict[str, object]:
    if site == "webapp_fi":
        archive_command = command("fi-wal-archive-v1", "a")
        restore_command = command("fi-wal-restore-v1", "b")
        archive_mode = "on"
        hot_standby = "off"
    else:
        archive_command = command("ir-wal-archive-v1", "c")
        restore_command = command("ir-wal-restore-v1", "d")
        archive_mode = "always"
        hot_standby = "on"
    return {
        "schema": EVIDENCE_SCHEMA,
        "site": site,
        "observed_role": role,
        "durability_claim": claim,
        "release_id": "3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        "schema_revision": "g7a8b9c0d1e2",
        "writer_term": writer_term(),
        "postgres": {
            "wal_level": "replica",
            "archive_mode": archive_mode,
            "archive_command": archive_command,
            "restore_command": restore_command,
            "max_wal_senders": 4,
            "max_replication_slots": 4,
            "hot_standby": hot_standby,
            "synchronous_commit": synchronous_commit,
            "synchronous_standby": synchronous_standby,
        },
        "physical": {
            "timeline": 7,
            "base_generation_id": "baseline-20260731-01",
            "base_backup_sha256": digest("2"),
            "source_wal_frontier": source_wal_frontier,
            "archived_wal_frontier": archived_wal_frontier,
            "replay_frontier": replay_frontier,
            "acknowledged_wal_frontier": acknowledged_wal_frontier,
        },
        "transport": {
            "archive_transport": "private-versioned-object-storage",
            "wa_ir_object_ingest": "pull-only",
            "direct_fi_to_ir_control": "forbidden",
            "wal_delivery_mode": delivery_mode,
        },
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
    }


def expected_binding() -> PhysicalPostgresDataPlanePreflightBinding:
    return PhysicalPostgresDataPlanePreflightBinding(
        release_id="3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        schema_revision="g7a8b9c0d1e2",
        active_term=PhysicalPostgresWriterTermBinding(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witness_transition_id="witness-transition-41",
            proof_sha256=digest("1"),
        ),
        base_generation_id="baseline-20260731-01",
        base_backup_sha256=digest("2"),
        fi_archive_command=PhysicalPostgresCommandDescriptor(
            identity="fi-wal-archive-v1", sha256=digest("a")
        ),
        fi_restore_command=PhysicalPostgresCommandDescriptor(
            identity="fi-wal-restore-v1", sha256=digest("b")
        ),
        ir_archive_command=PhysicalPostgresCommandDescriptor(
            identity="ir-wal-archive-v1", sha256=digest("c")
        ),
        ir_restore_command=PhysicalPostgresCommandDescriptor(
            identity="ir-wal-restore-v1", sha256=digest("d")
        ),
    )


def archive_pair(
    *,
    claim: str = ARCHIVE_BOUNDED_RPO,
    observed_at: datetime = NOW,
) -> tuple[PhysicalPostgresReadbackEvidence, PhysicalPostgresReadbackEvidence]:
    fi = readback_payload(
        site="webapp_fi",
        role="primary",
        claim=claim,
        delivery_mode=ARCHIVE_ONLY_DELIVERY,
        synchronous_commit="on",
        synchronous_standby=disabled_synchronous_standby(),
        source_wal_frontier="0/300",
        archived_wal_frontier="0/200",
        replay_frontier=None,
        acknowledged_wal_frontier="0/300",
        observed_at=observed_at,
    )
    ir = readback_payload(
        site="webapp_ir",
        role="standby",
        claim=claim,
        delivery_mode=ARCHIVE_ONLY_DELIVERY,
        synchronous_commit="on",
        synchronous_standby=disabled_synchronous_standby(),
        source_wal_frontier=None,
        archived_wal_frontier="0/200",
        replay_frontier="0/200",
        acknowledged_wal_frontier=None,
        observed_at=observed_at,
    )
    return evidence(fi), evidence(ir)


def strict_pair(
    *,
    fi_synchronous_commit: str = "on",
    replay_frontier: str = "0/280",
) -> tuple[PhysicalPostgresReadbackEvidence, PhysicalPostgresReadbackEvidence]:
    ir_payload = readback_payload(
        site="webapp_ir",
        role="standby",
        claim=STRICT_ZERO_LOSS,
        delivery_mode=STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY,
        synchronous_commit="on",
        synchronous_standby=disabled_synchronous_standby(),
        source_wal_frontier=None,
        archived_wal_frontier="0/200",
        replay_frontier=replay_frontier,
        acknowledged_wal_frontier=None,
    )
    fi_payload = readback_payload(
        site="webapp_fi",
        role="primary",
        claim=STRICT_ZERO_LOSS,
        delivery_mode=STRICT_OBJECT_STORAGE_REMOTE_DURABLE_REPLAY_DELIVERY,
        synchronous_commit=fi_synchronous_commit,
        synchronous_standby=disabled_synchronous_standby(),
        source_wal_frontier="0/300",
        archived_wal_frontier="0/200",
        replay_frontier=None,
        acknowledged_wal_frontier="0/280",
    )
    return evidence(fi_payload), evidence(ir_payload)


class PhysicalPostgresDataPlanePreflightTests(unittest.TestCase):
    def assess(
        self,
        fi: PhysicalPostgresReadbackEvidence,
        ir: PhysicalPostgresReadbackEvidence,
        profile: str,
        **kwargs: object,
    ):
        return assess_physical_postgres_data_plane_preflight(
            fi_readback=fi,
            ir_readback=ir,
            expected_binding=expected_binding(),
            requested_durability_profile=profile,
            now=NOW,
            **kwargs,
        )

    def test_archive_only_is_observed_only_as_explicit_bounded_rpo(self) -> None:
        fi, ir = archive_pair()

        result = self.assess(fi, ir, ARCHIVE_BOUNDED_RPO)

        self.assertEqual(PREFLIGHT_STATUS_OBSERVED, result.status)
        self.assertEqual(ARCHIVE_BOUNDED_RPO, result.observed_durability_profile)
        self.assertEqual((), result.reasons)
        self.assertEqual("0/200", result.ir_replay_frontier)
        self.assertNotIn("fi-wal-archive", repr(result))

    def test_strict_profile_uses_the_object_storage_route_but_stays_blocked_until_runtime_exists(self) -> None:
        fi, ir = strict_pair()

        result = self.assess(fi, ir, STRICT_ZERO_LOSS)

        self.assertEqual(PREFLIGHT_STATUS_BLOCKED, result.status)
        self.assertIn("strict-remote-durable-replay-runtime-not-implemented", result.reasons)
        self.assertEqual("0/280", result.fi_acknowledged_wal_frontier)
        self.assertEqual("0/280", result.ir_replay_frontier)
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "blocked"):
            require_observed_physical_postgres_data_plane_preflight(result, now=NOW)

    def test_zero_loss_claim_with_archive_only_delivery_is_blocked(self) -> None:
        fi, ir = archive_pair(claim=STRICT_ZERO_LOSS)

        result = self.assess(fi, ir, STRICT_ZERO_LOSS)

        self.assertEqual(PREFLIGHT_STATUS_BLOCKED, result.status)
        self.assertIn("strict-zero-loss-is-blocked-for-archive-only-wal-delivery", result.reasons)
        self.assertIn(
            "strict-zero-loss-requires-object-storage-remote-durable-replay-delivery",
            result.reasons,
        )

    def test_native_remote_apply_and_synchronous_receiver_are_rejected_for_the_pull_route(self) -> None:
        fi, _ = strict_pair(fi_synchronous_commit="remote_apply")

        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "synchronous_commit"):
            verify_physical_postgres_readback_evidence(fi)

        fi, _ = strict_pair()
        payload = json.loads(fi.raw_evidence)
        payload["postgres"]["synchronous_standby"] = {
            "mode": "required",
            "receiver_site": "webapp_ir",
            "receiver_application_name": "wa-ir-native-standby",
            "receiver_slot_name": "wa_ir_native_slot",
            "receiver_evidence_sha256": digest("e"),
            "acknowledgement": "remote-apply",
            "transport": "reviewed-physical-standby",
        }

        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "native synchronous receiver"):
            verify_physical_postgres_readback_evidence(evidence(payload))

    def test_strict_profile_is_blocked_when_ir_has_not_replayed_acknowledged_frontier(self) -> None:
        fi, ir = strict_pair(replay_frontier="0/270")

        result = self.assess(fi, ir, STRICT_ZERO_LOSS)

        self.assertEqual(PREFLIGHT_STATUS_BLOCKED, result.status)
        self.assertIn("ir-replay-frontier-is-behind-fi-acknowledged-frontier", result.reasons)

    def test_command_hash_must_match_independently_trusted_binding(self) -> None:
        fi, ir = archive_pair()
        payload = json.loads(fi.raw_evidence)
        payload["postgres"]["archive_command"]["sha256"] = digest("e")
        changed_fi = evidence(payload)

        result = self.assess(changed_fi, ir, ARCHIVE_BOUNDED_RPO)

        self.assertEqual(PREFLIGHT_STATUS_BLOCKED, result.status)
        self.assertIn("webapp_fi-archive-command-binding-mismatch", result.reasons)

    def test_release_schema_term_and_baseline_must_bind_both_sides(self) -> None:
        fi, ir = archive_pair()
        payload = json.loads(ir.raw_evidence)
        payload["release_id"] = "different-release-3138"
        payload["schema_revision"] = "different-schema"
        payload["writer_term"]["writer_epoch"] = 42
        payload["physical"]["base_generation_id"] = "different-baseline"
        changed_ir = evidence(payload)

        result = self.assess(fi, changed_ir, ARCHIVE_BOUNDED_RPO)

        self.assertEqual(PREFLIGHT_STATUS_BLOCKED, result.status)
        self.assertIn("webapp_ir-release-binding-mismatch", result.reasons)
        self.assertIn("webapp_ir-schema-binding-mismatch", result.reasons)
        self.assertIn("webapp_ir-writer-term-binding-mismatch", result.reasons)
        self.assertIn("webapp_ir-base-generation-binding-mismatch", result.reasons)

    def test_ir_standby_requires_hot_standby_and_replay_frontier(self) -> None:
        fi, ir = archive_pair()
        payload = json.loads(ir.raw_evidence)
        payload["postgres"]["hot_standby"] = "off"
        payload["physical"]["replay_frontier"] = None
        changed_ir = evidence(payload)

        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "replay frontier"):
            self.assess(fi, changed_ir, ARCHIVE_BOUNDED_RPO)

    def test_preflight_blocks_stale_readback_and_rechecks_freshness(self) -> None:
        fi, ir = archive_pair(observed_at=NOW - timedelta(seconds=301))

        stale = self.assess(fi, ir, ARCHIVE_BOUNDED_RPO)

        self.assertEqual(PREFLIGHT_STATUS_BLOCKED, stale.status)
        self.assertIn("webapp_fi-evidence-is-stale", stale.reasons)
        fresh_fi, fresh_ir = archive_pair()
        fresh = self.assess(fresh_fi, fresh_ir, ARCHIVE_BOUNDED_RPO)
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "blocked"):
            require_observed_physical_postgres_data_plane_preflight(
                fresh, now=NOW + timedelta(seconds=301)
            )

    def test_noncanonical_duplicate_and_hash_mismatched_raw_evidence_fail_closed(self) -> None:
        fi, _ = archive_pair()
        noncanonical = replace(fi, raw_evidence=fi.raw_evidence + b"\n")
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "canonical"):
            verify_physical_postgres_readback_evidence(noncanonical)

        duplicate_raw = (
            b'{"schema":"gold-trade-physical-postgres-readback-evidence-v1",'
            b'"schema":"gold-trade-physical-postgres-readback-evidence-v1"}'
        )
        duplicate = replace(
            fi,
            raw_evidence=duplicate_raw,
            evidence_sha256=hashlib.sha256(duplicate_raw).hexdigest(),
        )
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "duplicate"):
            verify_physical_postgres_readback_evidence(duplicate)

        hash_mismatch = replace(fi, evidence_sha256=digest("f"))
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "SHA-256"):
            verify_physical_postgres_readback_evidence(hash_mismatch)

        lsn_alias = json.loads(fi.raw_evidence)
        lsn_alias["physical"]["source_wal_frontier"] = "0/00000300"
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "source WAL frontier"):
            verify_physical_postgres_readback_evidence(evidence(lsn_alias))

    def test_raw_urls_secrets_and_direct_fi_ir_control_are_rejected(self) -> None:
        fi, _ = archive_pair()
        payload = json.loads(fi.raw_evidence)
        payload["postgres"]["archive_command"]["identity"] = "https://archive.example/path"
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "URL"):
            verify_physical_postgres_readback_evidence(evidence(payload))

        payload = json.loads(fi.raw_evidence)
        payload["transport"]["direct_fi_to_ir_control"] = "allowed"
        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "direct FI-to-IR"):
            verify_physical_postgres_readback_evidence(evidence(payload))

    def test_directly_constructed_or_replaced_result_is_not_an_observed_capability(self) -> None:
        fi, ir = archive_pair()
        result = self.assess(fi, ir, ARCHIVE_BOUNDED_RPO)

        forged = replace(result)

        with self.assertRaisesRegex(PhysicalPostgresDataPlanePreflightError, "capability"):
            require_observed_physical_postgres_data_plane_preflight(forged, now=NOW)


if __name__ == "__main__":
    unittest.main()
