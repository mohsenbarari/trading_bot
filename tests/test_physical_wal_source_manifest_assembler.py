from __future__ import annotations

import ast
from collections.abc import Sequence
import copy
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    verify_physical_wal_base_backup_manifest,
    verify_physical_wal_segment_manifest,
)
from core.physical_wal_source_manifest_assembler import (
    MAX_PHYSICAL_WAL_SOURCE_RECORD_BYTES,
    PHYSICAL_WAL_SOURCE_BASE_MANIFEST_BOOTSTRAP_SCHEMA,
    PHYSICAL_WAL_SOURCE_MANIFEST_APPEND_ASSEMBLY_SCHEMA,
    PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLER_DEFAULT_ENABLED,
    PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED,
    PhysicalWalSourceBaseManifestBootstrap,
    PhysicalWalSourceBaseManifestBootstrapBinding,
    PhysicalWalSourceManifestAppendBinding,
    PhysicalWalSourceManifestAssemblerBinding,
    PhysicalWalSourceManifestAssemblerError,
    PhysicalWalSourceManifestBaseline,
    PhysicalWalSourceManifestExpectedTerm,
    append_physical_wal_source_manifest_chain,
    assemble_physical_wal_source_manifest_chain,
    bootstrap_physical_wal_base_backup_manifest,
)


CAMPAIGN = "physical-source-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
BASE_GENERATION = "source-base-generation-20260731"
STREAM_GENERATION = "source-wal-stream-20260731"
SYSTEM_IDENTIFIER = "7234567890123456789"
WAL_SEGMENT_SIZE = 16 * 1024 * 1024
RECIPIENTS = {
    "webapp_fi": "age1" + "c" * 30,
    "webapp_ir": "age1" + "a" * 30,
}


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def lsn_value(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) | int(low, 16)


def wal_name(*, timeline_id: int, ordinal: int) -> str:
    segments_per_log = (1 << 32) // WAL_SEGMENT_SIZE
    log, segment = divmod(ordinal, segments_per_log)
    return f"{timeline_id:08X}{log:08X}{segment:08X}"


def json_object(raw: bytes) -> dict[str, object]:
    import json

    return json.loads(raw.decode("ascii"))


class PhysicalWalSourceManifestAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fi_signer = Ed25519PrivateKey.generate()
        self.ir_signer = Ed25519PrivateKey.generate()

    def provisional_binding(
        self,
        *,
        source_site: str = "webapp_fi",
        destination_site: str = "webapp_ir",
        baseline_wal_lsn: str = "0/1800000",
        wal_chain_start_lsn: str = "0/1000000",
        base_backup_end_lsn: str = "0/2800000",
        object_storage_namespace: str | None = None,
    ) -> PhysicalWalSourceManifestAssemblerBinding:
        signer = self.fi_signer if source_site == "webapp_fi" else self.ir_signer
        route = f"{source_site}-{destination_site}"
        return PhysicalWalSourceManifestAssemblerBinding(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            expected_term=PhysicalWalSourceManifestExpectedTerm(
                holder_site=source_site,
                writer_epoch=7,
                writer_lease_id="writer-lease-7",
                witnessed_term_proof_sha256="a" * 64,
                witness_transition_id="witness-transition-7",
            ),
            baseline=PhysicalWalSourceManifestBaseline(
                baseline_generation_id=BASE_GENERATION,
                database_system_identifier=SYSTEM_IDENTIFIER,
                timeline_id=1,
                wal_segment_size_bytes=WAL_SEGMENT_SIZE,
                baseline_wal_lsn=baseline_wal_lsn,
                wal_chain_start_lsn=wal_chain_start_lsn,
                base_backup_end_lsn=base_backup_end_lsn,
            ),
            destination_age_recipient=RECIPIENTS[destination_site],
            # This placeholder is replaced exclusively with bootstrap output
            # before any WAL upload record is built.
            base_backup_manifest_sha256="b" * 64,
            wal_stream_generation_id=STREAM_GENERATION,
            wal_archive_manifest_sha256=sha("archive-manifest-" + route),
            wal_route_binding_sha256=sha("wal-route-" + route),
            wal_upload_manifest_sha256es=("c" * 64,),
            source_public_key=public_key(signer),
            source_signer=signer,
            object_storage_namespace=(
                object_storage_namespace
                if object_storage_namespace is not None
                else (
                    "physical-wal"
                    if (source_site, destination_site) == ("webapp_fi", "webapp_ir")
                    else "physical-failback"
                )
            ),
        )

    @staticmethod
    def object_key_prefix(binding: PhysicalWalSourceManifestAssemblerBinding) -> str:
        return "/".join(
            (
                binding.object_storage_namespace,
                binding.campaign_id,
                binding.release_sha,
                binding.baseline.baseline_generation_id,
                f"{binding.source_site}-to-{binding.destination_site}",
                f"timeline-{binding.baseline.timeline_id:08X}",
            )
        )

    @staticmethod
    def descriptor(
        kind: str,
        object_key: str,
        *,
        version_id: str,
        recipient: str,
        marker: str,
    ) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-wal-object-descriptor-v1",
            "version": 1,
            "object_kind": kind,
            "object_key": object_key,
            "version_id": version_id,
            "ciphertext_sha256": marker * 64,
            "ciphertext_bytes": 4096,
            "encryption": "age-v1",
            "age_recipient": recipient,
            "immutability": "versioned_create_only_readback_v1",
        }

    def base_record(self, binding: PhysicalWalSourceManifestAssemblerBinding) -> dict[str, object]:
        route = f"{binding.source_site}-{binding.destination_site}"
        snapshot = sha("base-snapshot-" + route)
        return {
            "schema": "gold-trade-physical-wal-base-backup-spool-completed-v1",
            "kind": "physical_postgresql_base_backup_uploaded_archive_recovery_only",
            "handoff_descriptor_sha256": sha("base-handoff-" + route),
            "source_site": binding.source_site,
            "destination_site": binding.destination_site,
            "campaign_id": binding.campaign_id,
            "release_sha": binding.release_sha,
            "baseline_generation_id": binding.baseline.baseline_generation_id,
            "route_binding_sha256": sha("base-route-" + route),
            "object_storage_namespace": binding.object_storage_namespace,
            "database_system_identifier": binding.baseline.database_system_identifier,
            "timeline_id": binding.baseline.timeline_id,
            "wal_segment_size_bytes": binding.baseline.wal_segment_size_bytes,
            "baseline_wal_lsn": binding.baseline.baseline_wal_lsn,
            "wal_chain_start_lsn": binding.baseline.wal_chain_start_lsn,
            "base_backup_end_lsn": binding.baseline.base_backup_end_lsn,
            "destination_age_recipient": binding.destination_age_recipient,
            "writer_term": {
                "holder_site": binding.expected_term.holder_site,
                "epoch": binding.expected_term.writer_epoch,
                "lease_id": binding.expected_term.writer_lease_id,
                "witness_transition_id": binding.expected_term.witness_transition_id,
                "witnessed_term_proof_sha256": binding.expected_term.witnessed_term_proof_sha256,
            },
            "completed_source_artifact": {
                "artifact_name": "physical-base-backup-0001.tar",
                "plaintext_sha256": snapshot,
                "plaintext_bytes": 262144,
                "completion_attestation_sha256": sha("base-completion-" + route),
            },
            "snapshot_sha256": snapshot,
            "snapshot_bytes": 262144,
            "object": self.descriptor(
                "physical_postgresql_base_backup",
                "/".join(
                    (
                        self.object_key_prefix(binding),
                        "base-backup",
                        f"{snapshot}.age",
                    )
                ),
                version_id=f"base-version-{route}",
                recipient=binding.destination_age_recipient,
                marker="b",
            ),
            "not_a_remote_apply_proof": True,
            "not_a_strict_acknowledgement_proof": True,
        }

    def base_bootstrap_binding(
        self,
        binding: PhysicalWalSourceManifestAssemblerBinding,
        completion_raw: bytes,
        *,
        source_public_key_value: bytes | None = None,
        source_signer: object | None = None,
    ) -> PhysicalWalSourceBaseManifestBootstrapBinding:
        route = f"{binding.source_site}-{binding.destination_site}"
        return PhysicalWalSourceBaseManifestBootstrapBinding(
            source_site=binding.source_site,
            destination_site=binding.destination_site,
            campaign_id=binding.campaign_id,
            release_sha=binding.release_sha,
            expected_term=binding.expected_term,
            baseline=binding.baseline,
            destination_age_recipient=binding.destination_age_recipient,
            base_route_binding_sha256=sha("base-route-" + route),
            base_completion_record_sha256=sha(completion_raw),
            source_public_key=(
                binding.source_public_key
                if source_public_key_value is None
                else source_public_key_value
            ),
            source_signer=binding.source_signer if source_signer is None else source_signer,
            object_storage_namespace=binding.object_storage_namespace,
        )

    def bootstrap(
        self,
        binding: PhysicalWalSourceManifestAssemblerBinding,
        completion_raw: bytes,
        **overrides: object,
    ) -> PhysicalWalSourceBaseManifestBootstrap:
        return bootstrap_physical_wal_base_backup_manifest(
            base_backup_completion_record=completion_raw,
            binding=self.base_bootstrap_binding(binding, completion_raw, **overrides),
        )

    def wal_record(
        self,
        binding: PhysicalWalSourceManifestAssemblerBinding,
        *,
        base_manifest_sha256: str,
        ordinal: int,
        marker: str,
    ) -> dict[str, object]:
        route = f"{binding.source_site}-{binding.destination_site}"
        start = ordinal * WAL_SEGMENT_SIZE
        end = start + WAL_SEGMENT_SIZE
        segment_name = wal_name(timeline_id=binding.baseline.timeline_id, ordinal=ordinal)
        snapshot = sha(f"wal-snapshot-{route}-{ordinal}")
        return {
            "schema": "gold-trade-physical-wal-archive-spool-manifest-v1",
            "kind": "physical_wal_segment_uploaded",
            "handoff_descriptor_sha256": sha(f"wal-handoff-{route}-{ordinal}"),
            "source_site": binding.source_site,
            "destination_site": binding.destination_site,
            "campaign_id": binding.campaign_id,
            "release_sha": binding.release_sha,
            "stream_generation_id": binding.wal_stream_generation_id,
            "baseline_generation_id": binding.baseline.baseline_generation_id,
            "baseline_manifest_sha256": base_manifest_sha256,
            "baseline_wal_lsn": binding.baseline.baseline_wal_lsn,
            "wal_chain_start_lsn": binding.baseline.wal_chain_start_lsn,
            "archive_manifest_sha256": binding.wal_archive_manifest_sha256,
            "route_binding_sha256": binding.wal_route_binding_sha256,
            "object_storage_namespace": binding.object_storage_namespace,
            "database_system_identifier": binding.baseline.database_system_identifier,
            "timeline_id": binding.baseline.timeline_id,
            "wal_segment_size_bytes": binding.baseline.wal_segment_size_bytes,
            "destination_age_recipient": binding.destination_age_recipient,
            "writer_term": {
                "holder_site": binding.expected_term.holder_site,
                "writer_epoch": binding.expected_term.writer_epoch,
                "writer_lease_id": binding.expected_term.writer_lease_id,
                "witnessed_term_proof_sha256": binding.expected_term.witnessed_term_proof_sha256,
            },
            "wal_segment_name": segment_name,
            "segment_ordinal": ordinal,
            "start_lsn": f"0/{start:X}",
            "end_lsn": f"0/{end:X}",
            "snapshot_sha256": snapshot,
            "snapshot_bytes": WAL_SEGMENT_SIZE,
            "object": self.descriptor(
                "postgresql_wal_segment",
                "/".join(
                    (
                        self.object_key_prefix(binding),
                        segment_name,
                        f"{snapshot}.age",
                    )
                ),
                version_id=f"wal-version-{route}-{ordinal:04d}",
                recipient=binding.destination_age_recipient,
                marker=marker,
            ),
        }

    def inputs(
        self,
        *,
        source_site: str = "webapp_fi",
        destination_site: str = "webapp_ir",
        baseline_wal_lsn: str = "0/1800000",
        wal_chain_start_lsn: str = "0/1000000",
        base_backup_end_lsn: str = "0/2800000",
    ) -> tuple[
        PhysicalWalSourceManifestAssemblerBinding,
        PhysicalWalSourceBaseManifestBootstrap,
        tuple[bytes, ...],
    ]:
        provisional = self.provisional_binding(
            source_site=source_site,
            destination_site=destination_site,
            baseline_wal_lsn=baseline_wal_lsn,
            wal_chain_start_lsn=wal_chain_start_lsn,
            base_backup_end_lsn=base_backup_end_lsn,
        )
        bootstrap = self.bootstrap(provisional, canonical_json_bytes(self.base_record(provisional)))
        first_ordinal = lsn_value(provisional.baseline.wal_chain_start_lsn) // WAL_SEGMENT_SIZE
        wal_raws = tuple(
            canonical_json_bytes(
                self.wal_record(
                    provisional,
                    base_manifest_sha256=bootstrap.base_backup_manifest_sha256,
                    ordinal=ordinal,
                    marker=marker,
                )
            )
            for ordinal, marker in ((first_ordinal, "c"), (first_ordinal + 1, "d"))
        )
        binding = replace(
            provisional,
            base_backup_manifest_sha256=bootstrap.base_backup_manifest_sha256,
            wal_upload_manifest_sha256es=tuple(sha(raw) for raw in wal_raws),
        )
        return binding, bootstrap, wal_raws

    @staticmethod
    def assemble(
        binding: PhysicalWalSourceManifestAssemblerBinding,
        base_manifest_raw: bytes,
        wal_raws: Sequence[bytes],
    ):
        return assemble_physical_wal_source_manifest_chain(
            base_backup_manifest=base_manifest_raw,
            wal_upload_manifests=wal_raws,
            binding=binding,
        )

    @staticmethod
    def append(
        binding: PhysicalWalSourceManifestAppendBinding,
        base_manifest: bytes,
        previous_wal_manifest: bytes,
        wal_raws: Sequence[bytes],
    ):
        return append_physical_wal_source_manifest_chain(
            base_backup_manifest=base_manifest,
            previous_wal_segment_manifest=previous_wal_manifest,
            wal_upload_manifests=wal_raws,
            binding=binding,
        )

    def verify_base(
        self,
        binding: PhysicalWalSourceManifestAssemblerBinding,
        raw: bytes,
    ):
        return verify_physical_wal_base_backup_manifest(
            raw,
            expected_source_public_key=binding.source_public_key,
            expected_source_site=binding.source_site,
            expected_destination_site=binding.destination_site,
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=7,
            expected_writer_lease_id="writer-lease-7",
            expected_witnessed_term_proof_sha256="a" * 64,
            expected_baseline_generation_id=BASE_GENERATION,
            expected_wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            expected_destination_age_recipient=binding.destination_age_recipient,
        )

    def test_bootstrap_then_receipts_then_initial_genesis_chain(self) -> None:
        binding, bootstrap, wal_raws = self.inputs()

        result = self.assemble(binding, bootstrap.base_backup_manifest, wal_raws)

        self.assertFalse(PHYSICAL_WAL_SOURCE_MANIFEST_ASSEMBLER_DEFAULT_ENABLED)
        self.assertEqual(PHYSICAL_WAL_SOURCE_BASE_MANIFEST_BOOTSTRAP_SCHEMA, bootstrap.schema)
        self.assertEqual(sha(bootstrap.base_backup_manifest), bootstrap.base_backup_manifest_sha256)
        self.assertEqual(bootstrap.base_backup_manifest, result.base_backup_manifest)
        self.assertEqual(bootstrap.base_backup_manifest_sha256, result.base_backup_manifest_sha256)
        self.assertEqual("0/3000000", result.terminal_wal_lsn)
        self.assertEqual(
            PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED,
            result.blob_frontier_requirement,
        )
        self.assertEqual(1, len(result.wal_segment_manifests))
        verified_base = self.verify_base(binding, result.base_backup_manifest)
        verified_wal = verify_physical_wal_segment_manifest(
            result.wal_segment_manifests[0],
            expected_source_public_key=binding.source_public_key,
            expected_baseline=verified_base,
            expected_previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            expected_previous_end_lsn="0/1000000",
            expected_previous_segment_ordinal=0,
            expected_destination_age_recipient=binding.destination_age_recipient,
        )
        self.assertEqual("0/3000000", verified_wal.end_lsn)

    def test_bootstrap_is_route_symmetric_for_ir_to_fi(self) -> None:
        binding, bootstrap, wal_raws = self.inputs(
            source_site="webapp_ir",
            destination_site="webapp_fi",
        )

        result = self.assemble(binding, bootstrap.base_backup_manifest, wal_raws)

        self.assertEqual("0/3000000", result.terminal_wal_lsn)
        self.assertEqual(RECIPIENTS["webapp_fi"], binding.destination_age_recipient)
        self.assertEqual(1, len(result.wal_segment_manifest_sha256es))

    def test_reverse_bootstrap_rejects_the_normal_object_storage_namespace(self) -> None:
        reverse = self.provisional_binding(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            object_storage_namespace="physical-wal",
        )
        raw = canonical_json_bytes(self.base_record(reverse))

        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "BASE_BOOTSTRAP_BINDING_INVALID",
        ):
            self.bootstrap(reverse, raw)

    def test_initial_rejects_mismatched_rebuilt_and_foreign_signed_base(self) -> None:
        binding, bootstrap, wal_raws = self.inputs()

        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "INITIAL_BASE_MANIFEST_TAMPERED",
        ):
            self.assemble(binding, bootstrap.base_backup_manifest + b"\n", wal_raws)

        # A separately minted base has a valid source signature but a different
        # immutable base object and hash.  It cannot replace the recorded
        # bootstrap output underneath already-bound WAL receipts.
        rebuilt_record = self.base_record(binding)
        rebuilt_snapshot = sha("rebuilt-predicted-base")
        rebuilt_record["completed_source_artifact"]["plaintext_sha256"] = rebuilt_snapshot
        rebuilt_record["snapshot_sha256"] = rebuilt_snapshot
        rebuilt_record["object"]["object_key"] = "/".join(
            (self.object_key_prefix(binding), "base-backup", f"{rebuilt_snapshot}.age")
        )
        rebuilt = self.bootstrap(binding, canonical_json_bytes(rebuilt_record))
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "INITIAL_BASE_MANIFEST_TAMPERED",
        ):
            self.assemble(binding, rebuilt.base_backup_manifest, wal_raws)

        # Even if a caller tries to re-pin a base signed by a different key,
        # initial assembly re-verifies the route's root-pinned source key.
        foreign_signer = Ed25519PrivateKey.generate()
        foreign = self.bootstrap(
            binding,
            canonical_json_bytes(self.base_record(binding)),
            source_public_key_value=public_key(foreign_signer),
            source_signer=foreign_signer,
        )
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "INITIAL_BASE_MANIFEST_INVALID",
        ):
            self.assemble(
                replace(binding, base_backup_manifest_sha256=foreign.base_backup_manifest_sha256),
                foreign.base_backup_manifest,
                wal_raws,
            )

    def test_bootstrap_rejects_tampered_noncanonical_and_foreign_completion_records(self) -> None:
        provisional = self.provisional_binding()
        record = self.base_record(provisional)
        raw = canonical_json_bytes(record)
        bootstrap_binding = self.base_bootstrap_binding(provisional, raw)

        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "BASE_COMPLETION_RECORD_TAMPERED",
        ):
            bootstrap_physical_wal_base_backup_manifest(
                base_backup_completion_record=raw + b"\n",
                binding=bootstrap_binding,
            )

        duplicate = raw[:-1] + b',"campaign_id":"duplicated"}'
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "SOURCE_RECORD_DUPLICATE_JSON_FIELD",
        ):
            self.bootstrap(provisional, duplicate)

        foreign = copy.deepcopy(record)
        foreign["object"]["object_key"] = "physical-wal/foreign/base-backup/" + "f" * 64 + ".age"
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "BASE_COMPLETION_RECORD_OBJECT_KEY_MISMATCH",
        ):
            self.bootstrap(provisional, canonical_json_bytes(foreign))

    def test_initial_rejects_receipt_base_pin_gaps_geometry_and_foreign_object_key(self) -> None:
        binding, bootstrap, wal_raws = self.inputs()

        one_binding = replace(binding, wal_upload_manifest_sha256es=(sha(wal_raws[0]),))
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_CHAIN_DOES_NOT_COVER_BASE_BACKUP_END",
        ):
            self.assemble(one_binding, bootstrap.base_backup_manifest, (wal_raws[0],))

        reordered = (wal_raws[1], wal_raws[0])
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_CHAIN_INVALID",
        ):
            self.assemble(
                replace(binding, wal_upload_manifest_sha256es=tuple(sha(raw) for raw in reordered)),
                bootstrap.base_backup_manifest,
                reordered,
            )

        wrong_base = json_object(wal_raws[0])
        wrong_base["baseline_manifest_sha256"] = "f" * 64
        wrong_base_raw = canonical_json_bytes(wrong_base)
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_INVALID",
        ):
            self.assemble(
                replace(
                    binding,
                    wal_upload_manifest_sha256es=(sha(wrong_base_raw), sha(wal_raws[1])),
                ),
                bootstrap.base_backup_manifest,
                (wrong_base_raw, wal_raws[1]),
            )

        wrong_key = json_object(wal_raws[1])
        wrong_key["object"]["object_key"] = (
            "physical-wal/foreign/wal/000000010000000000000002/" + "f" * 64 + ".age"
        )
        wrong_key_raw = canonical_json_bytes(wrong_key)
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_OBJECT_KEY_MISMATCH",
        ):
            self.assemble(
                replace(
                    binding,
                    wal_upload_manifest_sha256es=(sha(wal_raws[0]), sha(wrong_key_raw)),
                ),
                bootstrap.base_backup_manifest,
                (wal_raws[0], wrong_key_raw),
            )

    def test_initial_rejects_type_confusion_oversized_records_and_bad_sequence(self) -> None:
        binding, bootstrap, wal_raws = self.inputs()
        wrong_type = json_object(wal_raws[0])
        wrong_type["timeline_id"] = 1.0
        wrong_type_raw = canonical_json_bytes(wrong_type)
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_FOREIGN",
        ):
            self.assemble(
                replace(
                    binding,
                    wal_upload_manifest_sha256es=(sha(wrong_type_raw), sha(wal_raws[1])),
                ),
                bootstrap.base_backup_manifest,
                (wrong_type_raw, wal_raws[1]),
            )

        oversized = b"x" * (MAX_PHYSICAL_WAL_SOURCE_RECORD_BYTES + 1)
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_INVALID",
        ):
            self.assemble(
                replace(binding, wal_upload_manifest_sha256es=(sha(oversized),)),
                bootstrap.base_backup_manifest,
                (oversized,),
            )

        class OversizedSequence(Sequence[bytes]):
            def __len__(self) -> int:
                return 4097

            def __getitem__(self, index: int) -> bytes:
                raise AssertionError(f"oversized sequence was read at {index}")

        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_SEQUENCE_INVALID",
        ):
            self.assemble(binding, bootstrap.base_backup_manifest, OversizedSequence())

    def test_absolute_nonzero_and_zero_genesis_ordinals_are_preserved(self) -> None:
        nonzero_binding, nonzero_base, nonzero_uploads = self.inputs(
            baseline_wal_lsn="0/11800000",
            wal_chain_start_lsn="0/11000000",
            base_backup_end_lsn="0/12800000",
        )
        nonzero = self.assemble(
            nonzero_binding,
            nonzero_base.base_backup_manifest,
            nonzero_uploads,
        )
        verified_nonzero = verify_physical_wal_segment_manifest(
            nonzero.wal_segment_manifests[0],
            expected_source_public_key=nonzero_binding.source_public_key,
            expected_baseline=self.verify_base(nonzero_binding, nonzero.base_backup_manifest),
            expected_previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            expected_previous_end_lsn="0/11000000",
            expected_previous_segment_ordinal=16,
            expected_destination_age_recipient=nonzero_binding.destination_age_recipient,
        )
        self.assertEqual(17, verified_nonzero.segments[0].ordinal)

        zero_binding, zero_base, zero_uploads = self.inputs(
            baseline_wal_lsn="0/800000",
            wal_chain_start_lsn="0/0",
            base_backup_end_lsn="0/1800000",
        )
        zero = self.assemble(zero_binding, zero_base.base_backup_manifest, zero_uploads)
        verified_zero = verify_physical_wal_segment_manifest(
            zero.wal_segment_manifests[0],
            expected_source_public_key=zero_binding.source_public_key,
            expected_baseline=self.verify_base(zero_binding, zero.base_backup_manifest),
            expected_previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            expected_previous_end_lsn="0/0",
            expected_previous_segment_ordinal=-1,
            expected_destination_age_recipient=zero_binding.destination_age_recipient,
        )
        self.assertEqual(0, verified_zero.segments[0].ordinal)

    def test_append_only_builds_after_exact_signed_frontier(self) -> None:
        binding, bootstrap, initial_uploads = self.inputs()
        initial = self.assemble(binding, bootstrap.base_backup_manifest, initial_uploads)
        next_upload_raw = canonical_json_bytes(
            self.wal_record(
                binding,
                base_manifest_sha256=initial.base_backup_manifest_sha256,
                ordinal=3,
                marker="e",
            )
        )
        next_binding = replace(binding, wal_upload_manifest_sha256es=(sha(next_upload_raw),))
        append_binding = PhysicalWalSourceManifestAppendBinding(
            source_manifest_binding=next_binding,
            base_backup_manifest_sha256=initial.base_backup_manifest_sha256,
            previous_wal_segment_manifest_sha256=initial.wal_segment_manifest_sha256es[-1],
        )

        result = self.append(
            append_binding,
            initial.base_backup_manifest,
            initial.wal_segment_manifests[-1],
            (next_upload_raw,),
        )

        self.assertEqual(PHYSICAL_WAL_SOURCE_MANIFEST_APPEND_ASSEMBLY_SCHEMA, result.schema)
        self.assertEqual("0/4000000", result.terminal_wal_lsn)
        self.assertEqual(
            PHYSICAL_WAL_SOURCE_MANIFEST_BLOB_FRONTIER_REQUIRED,
            result.blob_frontier_requirement,
        )
        verified = verify_physical_wal_segment_manifest(
            result.wal_segment_manifests[0],
            expected_source_public_key=binding.source_public_key,
            expected_baseline=self.verify_base(binding, initial.base_backup_manifest),
            expected_previous_manifest_sha256=initial.wal_segment_manifest_sha256es[-1],
            expected_previous_end_lsn="0/3000000",
            expected_previous_segment_ordinal=2,
            expected_destination_age_recipient=binding.destination_age_recipient,
        )
        self.assertEqual(3, verified.segments[0].ordinal)

    def test_append_rejects_gap_tampered_base_and_inconsistent_base_pin(self) -> None:
        binding, bootstrap, initial_uploads = self.inputs()
        initial = self.assemble(binding, bootstrap.base_backup_manifest, initial_uploads)
        gap_raw = canonical_json_bytes(
            self.wal_record(
                binding,
                base_manifest_sha256=initial.base_backup_manifest_sha256,
                ordinal=4,
                marker="e",
            )
        )
        append_binding = PhysicalWalSourceManifestAppendBinding(
            source_manifest_binding=replace(
                binding,
                wal_upload_manifest_sha256es=(sha(gap_raw),),
            ),
            base_backup_manifest_sha256=initial.base_backup_manifest_sha256,
            previous_wal_segment_manifest_sha256=initial.wal_segment_manifest_sha256es[-1],
        )
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "WAL_UPLOAD_MANIFEST_CHAIN_INVALID",
        ):
            self.append(
                append_binding,
                initial.base_backup_manifest,
                initial.wal_segment_manifests[-1],
                (gap_raw,),
            )
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "APPEND_BASE_MANIFEST_TAMPERED",
        ):
            self.append(
                append_binding,
                initial.base_backup_manifest + b"\n",
                initial.wal_segment_manifests[-1],
                (gap_raw,),
            )
        with self.assertRaisesRegex(
            PhysicalWalSourceManifestAssemblerError,
            "APPEND_BINDING_INVALID",
        ):
            self.append(
                replace(append_binding, base_backup_manifest_sha256="f" * 64),
                initial.base_backup_manifest,
                initial.wal_segment_manifests[-1],
                (gap_raw,),
            )

    def test_module_is_pure_and_does_not_import_spool_implementations(self) -> None:
        path = Path(__file__).resolve().parents[1] / "core/physical_wal_source_manifest_assembler.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            imports
            & {
                "os",
                "subprocess",
                "socket",
                "requests",
                "httpx",
                "aiohttp",
                "boto3",
                "psycopg",
                "sqlalchemy",
            }
        )
        self.assertNotIn("core.physical_wal_archive_spool", source)
        self.assertNotIn("core.physical_wal_base_backup_spool", source)


if __name__ == "__main__":
    unittest.main()
