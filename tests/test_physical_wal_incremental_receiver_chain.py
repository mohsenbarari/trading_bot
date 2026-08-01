from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.physical_wal_incremental_receiver_chain import (
    PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS,
    PhysicalWalIncrementalReceiverConfig,
    PhysicalWalIncrementalReceiverError,
    bootstrap_physical_wal_incremental_receiver_chain,
    build_physical_wal_incremental_receiver_pin,
    load_physical_wal_incremental_receiver_cursor,
    stage_physical_wal_incremental_receiver_append,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_object_storage_bundle,
)


CAMPAIGN = "physical-incremental-receiver-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
BASE_GENERATION = "physical-receiver-base-20260731"
SYSTEM_IDENTIFIER = "7234567890123456789"
TERM_PROOF = "a" * 64
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
WAL_SEGMENT_SIZE = 16 * 1024 * 1024


def _manifest_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _wal_name(*, timeline_id: int, ordinal: int) -> str:
    segments_per_log = (1 << 32) // WAL_SEGMENT_SIZE
    log, segment = divmod(ordinal, segments_per_log)
    return f"{timeline_id:08X}{log:08X}{segment:08X}"


class PhysicalWalIncrementalReceiverChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.public_key = self.signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @staticmethod
    def descriptor(kind: str, key: str, *, version: str, marker: str) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-wal-object-descriptor-v1",
            "version": 1,
            "object_kind": kind,
            "object_key": key,
            "version_id": version,
            "ciphertext_sha256": marker * 64,
            "ciphertext_bytes": 4096,
            "encryption": "age-v1",
            "age_recipient": RECIPIENT,
            "immutability": "versioned_create_only_readback_v1",
        }

    def base(self) -> dict[str, object]:
        return build_physical_wal_base_backup_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            base_backup_object=self.descriptor(
                "physical_postgresql_base_backup",
                "physical/incremental/base/backup-001.age",
                version="base-version-001",
                marker="b",
            ),
            source_signer=self.signer,
        )

    def wal(
        self,
        base: dict[str, object],
        *,
        previous_manifest_sha256: str,
        previous_end_lsn: str,
        previous_segment_ordinal: int,
        ordinal: int,
        marker: str,
        writer_epoch: int = 7,
        writer_lease_id: str = "writer-lease-seven",
        witnessed_term_proof_sha256: str = TERM_PROOF,
        baseline_manifest_sha256: str | None = None,
    ) -> dict[str, object]:
        start = ordinal * WAL_SEGMENT_SIZE
        end = start + WAL_SEGMENT_SIZE
        return build_physical_wal_segment_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            witnessed_term_proof_sha256=witnessed_term_proof_sha256,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=(
                _manifest_hash(base)
                if baseline_manifest_sha256 is None
                else baseline_manifest_sha256
            ),
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=previous_manifest_sha256,
            previous_end_lsn=previous_end_lsn,
            previous_segment_ordinal=previous_segment_ordinal,
            segments=(
                {
                    "ordinal": ordinal,
                    "wal_segment_name": _wal_name(timeline_id=1, ordinal=ordinal),
                    "timeline_id": 1,
                    "start_lsn": f"0/{start:X}",
                    "end_lsn": f"0/{end:X}",
                    "object": self.descriptor(
                        "postgresql_wal_segment",
                        f"physical/incremental/wal/{ordinal:04d}-{marker}.age",
                        version=f"wal-version-{ordinal:04d}-{marker}",
                        marker=marker,
                    ),
                },
            ),
            source_signer=self.signer,
        )

    def blob(
        self,
        base: dict[str, object],
        *,
        previous_manifest_sha256: str,
        previous_frontier_wal_lsn: str,
        frontier_wal_lsn: str,
        marker: str,
        writer_epoch: int = 7,
        writer_lease_id: str = "writer-lease-seven",
        witnessed_term_proof_sha256: str = TERM_PROOF,
        baseline_manifest_sha256: str | None = None,
    ) -> dict[str, object]:
        return build_physical_wal_blob_frontier_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            witnessed_term_proof_sha256=witnessed_term_proof_sha256,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=(
                _manifest_hash(base)
                if baseline_manifest_sha256 is None
                else baseline_manifest_sha256
            ),
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=previous_manifest_sha256,
            previous_frontier_wal_lsn=previous_frontier_wal_lsn,
            blob_object_frontier_wal_lsn=frontier_wal_lsn,
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": marker * 64,
                    "plaintext_bytes": 4096,
                    "entry_count": 7,
                    "object": self.descriptor(
                        "blob_inventory_shard",
                        f"physical/incremental/blob/{marker}.age",
                        version=f"blob-version-{marker}",
                        marker=marker,
                    ),
                },
            ),
            source_signer=self.signer,
        )

    def bootstrap_inputs(self):
        base = self.base()
        first = self.wal(
            base,
            previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            previous_end_lsn="0/1000000",
            previous_segment_ordinal=0,
            ordinal=1,
            marker="c",
        )
        first_hash = _manifest_hash(first)
        second = self.wal(
            base,
            previous_manifest_sha256=first_hash,
            previous_end_lsn="0/2000000",
            previous_segment_ordinal=1,
            ordinal=2,
            marker="d",
        )
        initial_blob = self.blob(
            base,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn="0/1000000",
            frontier_wal_lsn="0/3000000",
            marker="e",
        )
        bundle = verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base,
            wal_segment_manifests=(first, second),
            blob_frontier_manifest=initial_blob,
            expected_source_public_key=self.public_key,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=7,
            expected_writer_lease_id="writer-lease-seven",
            expected_witnessed_term_proof_sha256=TERM_PROOF,
            expected_baseline_generation_id=BASE_GENERATION,
            expected_wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            expected_destination_age_recipient=RECIPIENT,
        )
        pin = build_physical_wal_incremental_receiver_pin(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            source_public_key=self.public_key,
            destination_age_recipient=RECIPIENT,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=bundle.baseline.manifest_sha256,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
        )
        return base, bundle, pin

    def append_inputs(self, base: dict[str, object], bundle):
        wal = self.wal(
            base,
            previous_manifest_sha256=bundle.wal_manifests[-1].manifest_sha256,
            previous_end_lsn=bundle.terminal_wal_lsn,
            previous_segment_ordinal=bundle.wal_manifests[-1].last_segment_ordinal,
            ordinal=3,
            marker="f",
        )
        blob = self.blob(
            base,
            previous_manifest_sha256=bundle.blob_frontier.manifest_sha256,
            previous_frontier_wal_lsn=bundle.blob_frontier.blob_object_frontier_wal_lsn,
            frontier_wal_lsn="0/4000000",
            marker="a",
        )
        return wal, blob

    @staticmethod
    def config(root: Path) -> PhysicalWalIncrementalReceiverConfig:
        return PhysicalWalIncrementalReceiverConfig(state_root=root, enabled=True)

    def test_bootstraps_then_appends_exact_metadata_chain_idempotently(self):
        base, bundle, pin = self.bootstrap_inputs()
        wal, blob = self.append_inputs(base, bundle)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            bootstrap = bootstrap_physical_wal_incremental_receiver_chain(
                bootstrap_bundle=bundle,
                pin=pin,
                config=config,
            )
            retry_bootstrap = bootstrap_physical_wal_incremental_receiver_chain(
                bootstrap_bundle=bundle,
                pin=pin,
                config=config,
            )
            staged = stage_physical_wal_incremental_receiver_append(
                wal_segment_manifest=wal,
                blob_frontier_manifest=blob,
                pin=pin,
                config=config,
            )
            retry_append = stage_physical_wal_incremental_receiver_append(
                wal_segment_manifest=wal,
                blob_frontier_manifest=blob,
                pin=pin,
                config=config,
            )
            loaded = load_physical_wal_incremental_receiver_cursor(pin=pin, config=config)

            self.assertEqual(PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS, bootstrap.status)
            self.assertFalse(bootstrap.idempotent)
            self.assertTrue(retry_bootstrap.idempotent)
            self.assertFalse(staged.idempotent)
            self.assertTrue(retry_append.idempotent)
            self.assertEqual(2, loaded.sequence)
            self.assertEqual("0/4000000", loaded.wal_end_lsn)
            self.assertEqual("0/4000000", loaded.blob_frontier_wal_lsn)
            self.assertEqual(3, loaded.wal_last_segment_ordinal)
            self.assertEqual(0o400, stat.S_IMODE(staged.record_path.stat().st_mode))

    def test_rejects_gap_replay_and_competing_blob_fork_after_progress(self):
        base, bundle, pin = self.bootstrap_inputs()
        valid_wal, valid_blob = self.append_inputs(base, bundle)
        gap_wal = self.wal(
            base,
            previous_manifest_sha256="f" * 64,
            previous_end_lsn="0/3000000",
            previous_segment_ordinal=2,
            ordinal=3,
            marker="b",
        )
        fork_blob = self.blob(
            base,
            previous_manifest_sha256=bundle.blob_frontier.manifest_sha256,
            previous_frontier_wal_lsn="0/3000000",
            frontier_wal_lsn="0/4000000",
            marker="c",
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            bootstrap_physical_wal_incremental_receiver_chain(
                bootstrap_bundle=bundle,
                pin=pin,
                config=config,
            )
            with self.assertRaisesRegex(
                PhysicalWalIncrementalReceiverError,
                "WAL_PREDECESSOR_OR_ROUTE_TERM_REJECTED",
            ):
                stage_physical_wal_incremental_receiver_append(
                    wal_segment_manifest=gap_wal,
                    blob_frontier_manifest=valid_blob,
                    pin=pin,
                    config=config,
                )
            stage_physical_wal_incremental_receiver_append(
                wal_segment_manifest=valid_wal,
                blob_frontier_manifest=valid_blob,
                pin=pin,
                config=config,
            )
            with self.assertRaisesRegex(
                PhysicalWalIncrementalReceiverError,
                "APPEND_REPLAY_OR_FORK",
            ):
                stage_physical_wal_incremental_receiver_append(
                    wal_segment_manifest=valid_wal,
                    blob_frontier_manifest=fork_blob,
                    pin=pin,
                    config=config,
                )
            with self.assertRaisesRegex(
                PhysicalWalIncrementalReceiverError,
                "WAL_PREDECESSOR_OR_ROUTE_TERM_REJECTED",
            ):
                stage_physical_wal_incremental_receiver_append(
                    wal_segment_manifest=bundle.wal_manifests[-1].canonical_manifest,
                    blob_frontier_manifest=bundle.blob_frontier.canonical_manifest,
                    pin=pin,
                    config=config,
                )

    def test_rejects_route_term_and_baseline_pin_drift(self):
        _base, bundle, pin = self.bootstrap_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            bootstrap_physical_wal_incremental_receiver_chain(
                bootstrap_bundle=bundle,
                pin=pin,
                config=config,
            )
            changed_term = build_physical_wal_incremental_receiver_pin(
                source_site=pin.source_site,
                destination_site=pin.destination_site,
                source_public_key=pin.source_public_key,
                destination_age_recipient=pin.destination_age_recipient,
                campaign_id=pin.campaign_id,
                release_sha=pin.release_sha,
                writer_epoch=8,
                writer_lease_id="writer-lease-eight",
                witnessed_term_proof_sha256="b" * 64,
                baseline_generation_id=pin.baseline_generation_id,
                baseline_manifest_sha256=pin.baseline_manifest_sha256,
                database_system_identifier=pin.database_system_identifier,
                timeline_id=pin.timeline_id,
                wal_segment_size_bytes=pin.wal_segment_size_bytes,
                baseline_wal_lsn=pin.baseline_wal_lsn,
                wal_chain_start_lsn=pin.wal_chain_start_lsn,
                base_backup_end_lsn=pin.base_backup_end_lsn,
            )
            changed_baseline = build_physical_wal_incremental_receiver_pin(
                source_site=pin.source_site,
                destination_site=pin.destination_site,
                source_public_key=pin.source_public_key,
                destination_age_recipient=pin.destination_age_recipient,
                campaign_id=pin.campaign_id,
                release_sha=pin.release_sha,
                writer_epoch=pin.writer_epoch,
                writer_lease_id=pin.writer_lease_id,
                witnessed_term_proof_sha256=pin.witnessed_term_proof_sha256,
                baseline_generation_id=pin.baseline_generation_id,
                baseline_manifest_sha256="f" * 64,
                database_system_identifier=pin.database_system_identifier,
                timeline_id=pin.timeline_id,
                wal_segment_size_bytes=pin.wal_segment_size_bytes,
                baseline_wal_lsn=pin.baseline_wal_lsn,
                wal_chain_start_lsn=pin.wal_chain_start_lsn,
                base_backup_end_lsn=pin.base_backup_end_lsn,
            )
            changed_route = build_physical_wal_incremental_receiver_pin(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                source_public_key=pin.source_public_key,
                destination_age_recipient=pin.destination_age_recipient,
                campaign_id=pin.campaign_id,
                release_sha=pin.release_sha,
                writer_epoch=pin.writer_epoch,
                writer_lease_id=pin.writer_lease_id,
                witnessed_term_proof_sha256=pin.witnessed_term_proof_sha256,
                baseline_generation_id=pin.baseline_generation_id,
                baseline_manifest_sha256=pin.baseline_manifest_sha256,
                database_system_identifier=pin.database_system_identifier,
                timeline_id=pin.timeline_id,
                wal_segment_size_bytes=pin.wal_segment_size_bytes,
                baseline_wal_lsn=pin.baseline_wal_lsn,
                wal_chain_start_lsn=pin.wal_chain_start_lsn,
                base_backup_end_lsn=pin.base_backup_end_lsn,
            )
            with self.assertRaisesRegex(PhysicalWalIncrementalReceiverError, "ROUTE_OR_TERM_DRIFT"):
                load_physical_wal_incremental_receiver_cursor(pin=changed_term, config=config)
            with self.assertRaisesRegex(PhysicalWalIncrementalReceiverError, "ROUTE_OR_TERM_DRIFT"):
                load_physical_wal_incremental_receiver_cursor(pin=changed_baseline, config=config)
            with self.assertRaisesRegex(PhysicalWalIncrementalReceiverError, "ROUTE_OR_TERM_DRIFT"):
                load_physical_wal_incremental_receiver_cursor(pin=changed_route, config=config)

    def test_rejects_incoming_term_and_base_lineage_drift_after_bootstrap(self):
        base, bundle, pin = self.bootstrap_inputs()
        term_wal = self.wal(
            base,
            previous_manifest_sha256=bundle.wal_manifests[-1].manifest_sha256,
            previous_end_lsn=bundle.terminal_wal_lsn,
            previous_segment_ordinal=bundle.wal_manifests[-1].last_segment_ordinal,
            ordinal=3,
            marker="b",
            writer_epoch=8,
            writer_lease_id="writer-lease-eight",
            witnessed_term_proof_sha256="b" * 64,
        )
        term_blob = self.blob(
            base,
            previous_manifest_sha256=bundle.blob_frontier.manifest_sha256,
            previous_frontier_wal_lsn=bundle.blob_frontier.blob_object_frontier_wal_lsn,
            frontier_wal_lsn="0/4000000",
            marker="c",
            writer_epoch=8,
            writer_lease_id="writer-lease-eight",
            witnessed_term_proof_sha256="b" * 64,
        )
        base_drift_wal = self.wal(
            base,
            previous_manifest_sha256=bundle.wal_manifests[-1].manifest_sha256,
            previous_end_lsn=bundle.terminal_wal_lsn,
            previous_segment_ordinal=bundle.wal_manifests[-1].last_segment_ordinal,
            ordinal=3,
            marker="d",
            baseline_manifest_sha256="f" * 64,
        )
        base_drift_blob = self.blob(
            base,
            previous_manifest_sha256=bundle.blob_frontier.manifest_sha256,
            previous_frontier_wal_lsn=bundle.blob_frontier.blob_object_frontier_wal_lsn,
            frontier_wal_lsn="0/4000000",
            marker="e",
            baseline_manifest_sha256="f" * 64,
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            bootstrap_physical_wal_incremental_receiver_chain(
                bootstrap_bundle=bundle,
                pin=pin,
                config=config,
            )
            with self.assertRaisesRegex(
                PhysicalWalIncrementalReceiverError,
                "WAL_PREDECESSOR_OR_ROUTE_TERM_REJECTED",
            ):
                stage_physical_wal_incremental_receiver_append(
                    wal_segment_manifest=term_wal,
                    blob_frontier_manifest=term_blob,
                    pin=pin,
                    config=config,
                )
            with self.assertRaisesRegex(
                PhysicalWalIncrementalReceiverError,
                "WAL_PREDECESSOR_OR_ROUTE_TERM_REJECTED",
            ):
                stage_physical_wal_incremental_receiver_append(
                    wal_segment_manifest=base_drift_wal,
                    blob_frontier_manifest=base_drift_blob,
                    pin=pin,
                    config=config,
                )

    def test_tampered_or_unfrozen_record_fails_closed(self):
        _base, bundle, pin = self.bootstrap_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            result = bootstrap_physical_wal_incremental_receiver_chain(
                bootstrap_bundle=bundle,
                pin=pin,
                config=config,
            )
            os.chmod(result.record_path, 0o600)
            result.record_path.write_bytes(b"{}")
            os.chmod(result.record_path, 0o400)
            with self.assertRaisesRegex(
                PhysicalWalIncrementalReceiverError,
                "RECORD_KIND_INVALID|RECORD_FIELDS_INVALID|RECORD_HASH_INVALID",
            ):
                load_physical_wal_incremental_receiver_cursor(pin=pin, config=config)

    def test_default_off_config_and_route_hash_tamper_are_rejected(self):
        _base, bundle, pin = self.bootstrap_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(PhysicalWalIncrementalReceiverError, "CURSOR_DISABLED"):
                bootstrap_physical_wal_incremental_receiver_chain(
                    bootstrap_bundle=bundle,
                    pin=pin,
                    config=PhysicalWalIncrementalReceiverConfig(state_root=root),
                )
            with self.assertRaisesRegex(PhysicalWalIncrementalReceiverError, "ROUTE_HASH_INVALID"):
                bootstrap_physical_wal_incremental_receiver_chain(
                    bootstrap_bundle=bundle,
                    pin=replace(pin, route_binding_sha256="0" * 64),
                    config=self.config(root),
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
