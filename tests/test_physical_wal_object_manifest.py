from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    PhysicalWalObjectManifestError,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    parse_physical_wal_object_manifest_json,
    require_verified_physical_wal_base_backup_manifest,
    verify_physical_wal_base_backup_manifest,
    verify_physical_wal_object_storage_bundle,
)


CAMPAIGN = "physical-wal-fi-ir-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
BASE_GENERATION = "fi-ir-physical-base-20260731"
SYSTEM_IDENTIFIER = "7234567890123456789"
TERM_PROOF = "a" * 64
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
OTHER_RECIPIENT = "age1pppppppppppppppppppppppppppppppppppppppppppppppp"
WAL_SEGMENT_SIZE = 16 * 1024 * 1024


def descriptor(kind: str, key: str, *, version: str, recipient: str = RECIPIENT, marker: str = "a"):
    return {
        "schema": "gold-trade-physical-wal-object-descriptor-v1",
        "version": 1,
        "object_kind": kind,
        "object_key": key,
        "version_id": version,
        "ciphertext_sha256": marker * 64,
        "ciphertext_bytes": 4096,
        "encryption": "age-v1",
        "age_recipient": recipient,
        "immutability": "versioned_create_only_readback_v1",
    }


class PhysicalWalObjectManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.public_key = self.signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def base(self, **overrides):
        values = {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "campaign_id": CAMPAIGN,
            "release_sha": RELEASE,
            "writer_epoch": 7,
            "writer_lease_id": "writer-lease-seven",
            "witnessed_term_proof_sha256": TERM_PROOF,
            "baseline_generation_id": BASE_GENERATION,
            "database_system_identifier": SYSTEM_IDENTIFIER,
            "timeline_id": 1,
            "wal_segment_size_bytes": WAL_SEGMENT_SIZE,
            "baseline_wal_lsn": "0/1000000",
            "wal_chain_start_lsn": "0/1000000",
            "base_backup_end_lsn": "0/1800000",
            "base_backup_object": descriptor(
                "physical_postgresql_base_backup",
                "physical/fi-ir/base/backup-001.age",
                version="base-version-001",
                marker="b",
            ),
            "source_signer": self.signer,
        }
        values.update(overrides)
        return build_physical_wal_base_backup_manifest(**values)

    def verify_base(self, manifest, **overrides):
        values = {
            "expected_source_public_key": self.public_key,
            "expected_source_site": "webapp_fi",
            "expected_destination_site": "webapp_ir",
            "expected_campaign_id": CAMPAIGN,
            "expected_release_sha": RELEASE,
            "expected_writer_epoch": 7,
            "expected_writer_lease_id": "writer-lease-seven",
            "expected_witnessed_term_proof_sha256": TERM_PROOF,
            "expected_baseline_generation_id": BASE_GENERATION,
            "expected_wal_segment_size_bytes": WAL_SEGMENT_SIZE,
            "expected_destination_age_recipient": RECIPIENT,
        }
        values.update(overrides)
        return verify_physical_wal_base_backup_manifest(manifest, **values)

    def wal(
        self,
        baseline,
        *,
        prior_hash=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
        prior_end="0/1000000",
        prior_ordinal=0,
        ranges=None,
        timeline=1,
    ):
        ranges = ranges or (
            (1, "0/1000000", "0/2000000", "c"),
            (2, "0/2000000", "0/3000000", "d"),
        )
        return build_physical_wal_segment_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=hashlib.sha256(
                json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode("ascii")
            ).hexdigest(),
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=timeline,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=prior_hash,
            previous_end_lsn=prior_end,
            previous_segment_ordinal=prior_ordinal,
            segments=tuple(
                {
                    "ordinal": ordinal,
                    "wal_segment_name": (
                        supplied_name[0]
                        if supplied_name
                        else f"{timeline:08X}000000000000{ordinal:04X}"
                    ),
                    "timeline_id": timeline,
                    "start_lsn": start,
                    "end_lsn": end,
                    "object": descriptor(
                        "postgresql_wal_segment",
                        f"physical/fi-ir/wal/{ordinal:04d}-{range_marker}.age",
                        version=f"wal-version-{ordinal:04d}",
                        marker=range_marker,
                    ),
                }
                for ordinal, start, end, range_marker, *supplied_name in ranges
            ),
            source_signer=self.signer,
        )

    def blob(self, baseline, *, recipient=RECIPIENT, frontier="0/3000000", previous="0/1000000"):
        return build_physical_wal_blob_frontier_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=hashlib.sha256(
                json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode("ascii")
            ).hexdigest(),
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn=previous,
            blob_object_frontier_wal_lsn=frontier,
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": "d" * 64,
                    "plaintext_bytes": 1234,
                    "entry_count": 12,
                    "object": descriptor(
                        "blob_inventory_shard",
                        "physical/fi-ir/blobs/inventory-001.age",
                        version="blob-version-001",
                        recipient=recipient,
                        marker="e",
                    ),
                },
            ),
            source_signer=self.signer,
        )

    def bundle(self, base, wal, blob, **overrides):
        values = {
            "base_backup_manifest": base,
            "wal_segment_manifests": (wal,),
            "blob_frontier_manifest": blob,
            "expected_source_public_key": self.public_key,
            "expected_source_site": "webapp_fi",
            "expected_destination_site": "webapp_ir",
            "expected_campaign_id": CAMPAIGN,
            "expected_release_sha": RELEASE,
            "expected_writer_epoch": 7,
            "expected_writer_lease_id": "writer-lease-seven",
            "expected_witnessed_term_proof_sha256": TERM_PROOF,
            "expected_baseline_generation_id": BASE_GENERATION,
            "expected_wal_segment_size_bytes": WAL_SEGMENT_SIZE,
            "expected_destination_age_recipient": RECIPIENT,
        }
        values.update(overrides)
        return verify_physical_wal_object_storage_bundle(**values)

    def test_builds_and_verifies_exact_base_wal_blob_bundle(self):
        base = self.base()
        wal = self.wal(base)
        blob = self.blob(base)

        verified = self.bundle(base, wal, blob)

        self.assertEqual("0/3000000", verified.terminal_wal_lsn)
        self.assertEqual(3, len(verified.manifest_sha256es))
        self.assertEqual(BASE_GENERATION, verified.baseline.baseline_generation_id)
        self.assertEqual("0/3000000", verified.blob_frontier.blob_object_frontier_wal_lsn)

    def test_raw_duplicate_and_noncanonical_json_are_rejected_before_authentication(self):
        base = self.base()
        raw = json.dumps(base, sort_keys=True, separators=(",", ":")).encode("ascii")

        duplicate = raw[:-1] + b',"campaign_id":"duplicate"}'
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "duplicate"):
            parse_physical_wal_object_manifest_json(duplicate)
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "canonical"):
            parse_physical_wal_object_manifest_json(json.dumps(base, indent=2).encode("ascii"))

    def test_tamper_and_foreign_route_binding_are_rejected(self):
        base = self.base()
        changed = copy.deepcopy(base)
        changed["base_backup_object"]["ciphertext_sha256"] = "f" * 64
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "signature"):
            self.verify_base(changed)

        foreign = self.base(campaign_id="physical-wal-foreign-20260731")
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "campaign"):
            self.verify_base(foreign)

    def test_mutable_alias_and_age_recipient_mismatch_are_fail_closed(self):
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "mutable alias"):
            self.base(
                base_backup_object=descriptor(
                    "physical_postgresql_base_backup",
                    "physical/fi-ir/base/latest.age",
                    version="base-version-001",
                )
            )

        base = self.base()
        wal = self.wal(base)
        blob = self.blob(base, recipient=OTHER_RECIPIENT)
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "age recipient"):
            self.bundle(base, wal, blob)

    def test_wal_hole_reorder_timeline_regression_and_replay_are_rejected(self):
        base = self.base()
        first = self.wal(base)
        first_hash = hashlib.sha256(
            json.dumps(first, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        second = self.wal(
            base,
            prior_hash="f" * 64,
            prior_end="0/3000000",
            prior_ordinal=2,
            ranges=((3, "0/3000000", "0/4000000", "f"),),
        )
        blob = self.blob(base, frontier="0/4000000")
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "previous WAL manifest"):
            self.bundle(base, first, blob, wal_segment_manifests=(first, second))
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "frontier hole|previous WAL manifest"):
            self.bundle(base, first, blob, wal_segment_manifests=(second, first))

        timeline_two = self.wal(base, timeline=2)
        blob_one = self.blob(base)
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "base-backup lineage"):
            self.bundle(base, timeline_two, blob_one)

        normal_blob = self.blob(base)
        base_hash = hashlib.sha256(
            json.dumps(base, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "previously consumed"):
            self.bundle(base, first, normal_blob, accepted_manifest_sha256es=(base_hash,))
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "consumed Object version"):
            self.bundle(
                base,
                first,
                normal_blob,
                accepted_object_versions=((
                    "physical/fi-ir/base/backup-001.age",
                    "base-version-001",
                ),),
            )
        self.assertNotEqual(first_hash, base_hash)

    def test_wal_filename_geometry_and_duplicate_filenames_are_fail_closed(self):
        base = self.base()
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "WAL geometry"):
            self.wal(
                base,
                ranges=(
                    (
                        1,
                        "0/1000000",
                        "0/2000000",
                        "e",
                        "000000010000000000000002",
                    ),
                ),
            )
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "segment size"):
            self.wal(base, ranges=((1, "0/1000000", "0/3000000", "f"),))
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "duplicate PostgreSQL WAL filename"):
            self.wal(
                base,
                ranges=(
                    (1, "0/1000000", "0/2000000", "f"),
                    (
                        1,
                        "0/1000000",
                        "0/2000000",
                        "e",
                        "000000010000000000000001",
                    ),
                ),
            )
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "supported PostgreSQL WAL segment size"):
            self.base(wal_segment_size_bytes=8 * 1024 * 1024)

    def test_absolute_wal_ordinals_accept_nonzero_and_segment_zero_genesis(self):
        nonzero_base = self.base(
            baseline_wal_lsn="0/21800000",
            wal_chain_start_lsn="0/21000000",
            base_backup_end_lsn="0/22800000",
        )
        nonzero_wal = self.wal(
            nonzero_base,
            prior_end="0/21000000",
            prior_ordinal=32,
            ranges=(
                (33, "0/21000000", "0/22000000", "c"),
                (34, "0/22000000", "0/23000000", "d"),
            ),
        )
        nonzero_blob = self.blob(
            nonzero_base,
            previous="0/21800000",
            frontier="0/23000000",
        )
        verified = self.bundle(nonzero_base, nonzero_wal, nonzero_blob)
        self.assertEqual(32, verified.wal_manifests[0].previous_segment_ordinal)
        self.assertEqual((33, 34), tuple(segment.ordinal for segment in verified.wal_manifests[0].segments))

        zero_base = self.base(
            baseline_wal_lsn="0/0",
            wal_chain_start_lsn="0/0",
            base_backup_end_lsn="0/1000000",
        )
        zero_wal = self.wal(
            zero_base,
            prior_end="0/0",
            prior_ordinal=-1,
            ranges=((0, "0/0", "0/1000000", "c"),),
        )
        zero_blob = self.blob(zero_base, previous="0/0", frontier="0/1000000")
        zero_verified = self.bundle(zero_base, zero_wal, zero_blob)
        self.assertEqual(-1, zero_verified.wal_manifests[0].previous_segment_ordinal)
        self.assertEqual(0, zero_verified.wal_manifests[0].segments[0].ordinal)

        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "ordinal"):
            self.wal(
                nonzero_base,
                prior_end="0/21000000",
                prior_ordinal=0,
                ranges=(
                    (
                        1,
                        "0/21000000",
                        "0/22000000",
                        "c",
                        "000000010000000000000021",
                    ),
                ),
            )

    def test_base_backup_can_start_inside_its_first_wal_segment_but_not_outside_it(self):
        base = self.base(
            baseline_wal_lsn="0/1800000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/2800000",
        )
        self.assertEqual("0/1800000", self.verify_base(base).baseline_wal_lsn)
        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "does not cover"):
            self.base(
                baseline_wal_lsn="0/1800000",
                wal_chain_start_lsn="0/2000000",
                base_backup_end_lsn="0/2800000",
            )

    def test_verified_capability_is_reparsed_before_reuse(self):
        base = self.base()
        verified = self.verify_base(base)
        forged = replace(verified, baseline_generation_id="forged-base-generation")
        object.__setattr__(forged, "_capability", verified._capability)

        with self.assertRaisesRegex(PhysicalWalObjectManifestError, "not normalized"):
            require_verified_physical_wal_base_backup_manifest(forged)

    def test_module_has_no_runtime_or_transport_dependencies(self):
        source = (
            Path(__file__).resolve().parents[1] / "core/physical_wal_object_manifest.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "sqlalchemy",
            "models.",
            "boto",
            "httpx",
            "aiohttp",
            "subprocess",
            "socket",
            "requests",
        )
        self.assertFalse([name for name in forbidden if name in source])


if __name__ == "__main__":
    unittest.main()
