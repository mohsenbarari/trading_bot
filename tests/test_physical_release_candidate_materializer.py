"""Focused no-I/O tests for the frozen release-candidate materializer boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.physical_release_candidate_inventory as inventory
import core.physical_release_candidate_materializer as materializer
import core.physical_release_candidate_writer_quiescence_receipt as quiescence_receipt


NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
SOURCE_ROOT = Path("/srv/trading-bot-three-site/review-source")
TARGET_ROOT = Path("/srv/trading-bot-three-site/release-candidate")


def _source_object(root: Path) -> inventory.PhysicalReleaseCandidateSourceObject:
    return inventory.PhysicalReleaseCandidateSourceObject(
        path=root,
        owner_uid=0,
        mode=0o750,
        directory=True,
        symlink=False,
        ancestors_root_controlled=True,
    )


class _GitInspector:
    def __init__(self, *, root: Path, clean: bool) -> None:
        self.value = inventory.PhysicalReleaseCandidateSourceInspection(
            source_root=_source_object(root),
            release_sha=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
            git_tree_id=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
            clean=clean,
            stable=True,
        )
        self.calls: list[Path] = []

    def inspect_source(self, *, source_root: Path):
        self.calls.append(source_root)
        return self.value


class _Reader:
    def __init__(self, *, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[Path, str]] = []
        self.overrides: dict[str, inventory.PhysicalReleaseCandidateFileObservation] = {}

    def read_file(self, *, source_root: Path, relative_path: str):
        self.calls.append((source_root, relative_path))
        if relative_path in self.overrides:
            return self.overrides[relative_path]
        body = ("reviewed:" + relative_path).encode("ascii")
        return inventory.PhysicalReleaseCandidateFileObservation(
            relative_path=relative_path,
            owner_uid=0,
            mode=0o755 if relative_path.endswith(".sh") else 0o644,
            regular_file=True,
            symlink=False,
            stable=True,
            content=body,
        )


class _Quiescence:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []
        self.writers_active = False
        self.source_stable = True
        self.generation = "a" * 64
        self.after_generation: str | None = None

    def observe_quiescence(self, *, source_root: Path, inventory_manifest_sha256: str):
        self.calls.append((source_root, inventory_manifest_sha256))
        generation = self.generation
        if len(self.calls) > 1 and self.after_generation is not None:
            generation = self.after_generation
        return materializer.PhysicalReleaseCandidateQuiescenceObservation(
            schema=materializer.PHYSICAL_RELEASE_CANDIDATE_QUIESCENCE_SCHEMA,
            status="writers-quiesced-source-stable",
            inventory_manifest_sha256=inventory_manifest_sha256,
            baseline_release_sha=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
            baseline_git_tree_id=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
            quiescence_generation_sha256=generation,
            evidence_sha256="b" * 64,
            observed_at=NOW,
            writers_active=self.writers_active,
            source_stable=self.source_stable,
            writer_lease_state="quiesced-no-writers",
        )


class _Transfer:
    def __init__(self) -> None:
        self.requests: list[materializer.PhysicalReleaseCandidateMaterializationRequest] = []
        self.extra_paths: tuple[str, ...] = ()
        self.atomic = True

    def materialize_exact_overlay(self, *, request):
        self.requests.append(request)
        return materializer.PhysicalReleaseCandidateAtomicTransferObservation(
            schema=materializer.PHYSICAL_RELEASE_CANDIDATE_ATOMIC_TRANSFER_SCHEMA,
            status="committed-atomic-exact-overlay",
            inventory_manifest_sha256=request.inventory_manifest_sha256,
            target_baseline_binding_sha256=request.target_baseline_binding_sha256,
            source_quiescence_generation_sha256=request.source_quiescence_generation_sha256,
            source_quiescence_evidence_sha256=request.source_quiescence_evidence_sha256,
            transfer_evidence_sha256="c" * 64,
            materialized_paths=tuple(entry.relative_path for entry in request.entries),
            unexpected_paths=self.extra_paths,
            atomically_committed=self.atomic,
            source_read_no_follow=True,
            target_write_no_follow=True,
            target_git_commit_created=False,
        )


class _Overlay:
    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.changed_paths: tuple[str, ...] = ()
        self.no_symlink_paths = True

    def inspect_overlay(
        self,
        *,
        target_root: Path,
        expected_baseline_sha: str,
        expected_baseline_tree: str,
    ):
        self.calls.append(target_root)
        return materializer.PhysicalReleaseCandidateTargetOverlayInspection(
            schema=materializer.PHYSICAL_RELEASE_CANDIDATE_TARGET_OVERLAY_SCHEMA,
            status="post-materialization-exact-overlay-observed",
            target_root=_source_object(target_root),
            baseline_release_sha=expected_baseline_sha,
            baseline_git_tree_id=expected_baseline_tree,
            stable=True,
            complete_changed_path_observation=True,
            no_symlink_paths=self.no_symlink_paths,
            changed_paths=self.changed_paths,
            evidence_sha256="d" * 64,
            target_git_commit_created=False,
            release_seal_created=False,
        )


class PhysicalReleaseCandidateMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_git = _GitInspector(root=SOURCE_ROOT, clean=False)
        self.target_git = _GitInspector(root=TARGET_ROOT, clean=True)
        self.source_reader = _Reader(root=SOURCE_ROOT)
        self.target_reader = _Reader(root=TARGET_ROOT)
        self.quiescence = _Quiescence()
        self.transfer = _Transfer()
        self.overlay = _Overlay()
        with patch.object(materializer.os, "geteuid", return_value=0):
            self.frozen = inventory.build_physical_release_candidate_inventory(
                config=inventory.PhysicalReleaseCandidateInventoryConfig(
                    source_root=SOURCE_ROOT,
                    expected_baseline_sha=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
                    expected_baseline_tree=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
                    enabled=True,
                    allow_dirty_staging_source=True,
                ),
                source_inspector=self.source_git,
                file_reader=self.source_reader,
            )
        self.quiescence_signer = Ed25519PrivateKey.generate()
        quiescence_public_key = self.quiescence_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.quiescence_policy = (
            quiescence_receipt.PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy(
                source_root=SOURCE_ROOT,
                required_mode=0o750,
            )
        )
        self.quiescence_receipt_verifier_config = (
            quiescence_receipt.RootOwnedPhysicalReleaseCandidateWriterQuiescenceReceiptVerifierConfig(
                source_root_policy=self.quiescence_policy,
                authority=quiescence_receipt.PhysicalReleaseCandidateWriterQuiescenceAuthorityPin(
                    public_key=quiescence_public_key,
                    key_id="ed25519-sha256:" + hashlib.sha256(quiescence_public_key).hexdigest(),
                ),
                enabled=True,
                maximum_receipt_age_seconds=120,
            )
        )
        self.verified_quiescence_receipt = self.make_verified_quiescence_receipt()

    def make_verified_quiescence_receipt(
        self,
        *,
        verifier_config=None,
        source_root: Path = SOURCE_ROOT,
        frozen_generation_sha256: str = "a" * 64,
        issued_at: datetime = NOW,
        expires_at: datetime | None = None,
        verification_now: datetime = NOW,
    ):
        config = (
            self.quiescence_receipt_verifier_config
            if verifier_config is None
            else verifier_config
        )
        raw = quiescence_receipt.build_signed_physical_release_candidate_writer_quiescence_receipt(
            source_root_policy=config.source_root_policy,
            inventory_manifest_sha256=self.frozen.manifest_sha256,
            frozen_generation_sha256=frozen_generation_sha256,
            quiescence_evidence_sha256="b" * 64,
            writer_lease_id="release-candidate-quiesced-lease-20260731",
            issued_at=issued_at,
            expires_at=expires_at or (issued_at + timedelta(seconds=100)),
            authority_signer=self.quiescence_signer,
        )
        return quiescence_receipt.verify_physical_release_candidate_writer_quiescence_receipt(
            raw,
            config=config,
            source_root=source_root,
            inventory_manifest_sha256=self.frozen.manifest_sha256,
            frozen_generation_sha256=frozen_generation_sha256,
            quiescence_evidence_sha256="b" * 64,
            now=verification_now,
        )

    def config(self, **changes: object):
        values: dict[str, object] = {
            "inventory": self.frozen,
            "source_inventory_config": inventory.PhysicalReleaseCandidateInventoryConfig(
                source_root=SOURCE_ROOT,
                expected_baseline_sha=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
                expected_baseline_tree=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
                enabled=True,
                allow_dirty_staging_source=True,
            ),
            "target_baseline_config": inventory.PhysicalReleaseCandidateInventoryConfig(
                source_root=TARGET_ROOT,
                expected_baseline_sha=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
                expected_baseline_tree=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
                enabled=True,
                allow_dirty_staging_source=False,
            ),
            "enabled": True,
            "maximum_quiescence_age_seconds": 120,
            "writer_quiescence_receipt_verifier_config": self.quiescence_receipt_verifier_config,
            "verified_writer_quiescence_receipt": self.verified_quiescence_receipt,
        }
        values.update(changes)
        return materializer.PhysicalReleaseCandidateMaterializationConfig(**values)

    def adapters(self, **changes: object):
        values: dict[str, object] = {
            "source_git_inspector": self.source_git,
            "target_git_inspector": self.target_git,
            "source_file_reader": self.source_reader,
            "target_file_reader": self.target_reader,
            "quiescence_observer": self.quiescence,
            "atomic_file_transfer": self.transfer,
            "target_overlay_inspector": self.overlay,
        }
        values.update(changes)
        return materializer.PhysicalReleaseCandidateMaterializationAdapters(**values)

    def run_materializer(self, **changes: object):
        config_changes = changes.pop("config_changes", {})
        adapter_changes = changes.pop("adapter_changes", {})
        self.assertEqual({}, changes)
        with patch.object(materializer.os, "geteuid", return_value=0):
            return materializer.materialize_verified_physical_release_candidate(
                config=self.config(**config_changes),
                adapters=self.adapters(**adapter_changes),
                now=NOW,
            )

    def test_exact_atomic_overlay_is_rehashed_and_receipt_is_non_authorizing(self) -> None:
        self.overlay.changed_paths = tuple(
            entry.relative_path for entry in self.frozen.entries[:2]
        )
        result = self.run_materializer()
        self.assertEqual("materialized-clean-baseline-overlay-uncommitted", result.status)
        self.assertTrue(result.overlay_materialized)
        self.assertFalse(result.release_authorized)
        self.assertFalse(result.image_build_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertEqual(1, len(self.transfer.requests))
        request = self.transfer.requests[0]
        self.assertEqual(self.frozen.manifest_sha256, request.inventory_manifest_sha256)
        self.assertEqual(
            tuple(entry.relative_path for entry in self.frozen.entries),
            tuple(path for _root, path in self.target_reader.calls),
        )
        self.assertEqual(2, len(self.quiescence.calls))
        self.assertEqual(TARGET_ROOT, self.overlay.calls[0])
        parsed = materializer.parse_physical_release_candidate_materialization_receipt(
            result.receipt.canonical_receipt
        )
        self.assertEqual(result.receipt.receipt_sha256, parsed.receipt_sha256)
        self.assertFalse(parsed.release_authorized)
        self.assertNotIn(str(SOURCE_ROOT).encode("ascii"), parsed.canonical_receipt)
        self.assertNotIn(str(TARGET_ROOT).encode("ascii"), parsed.canonical_receipt)
        self.assertNotIn(b"reviewed:", parsed.canonical_receipt)
        self.assertNotIn(b"ssh", parsed.canonical_receipt.lower())

    def test_default_off_nonroot_and_writer_activity_refuse_before_transfer(self) -> None:
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "MATERIALIZER_DISABLED",
        ):
            self.run_materializer(config_changes={"enabled": False})
        self.assertEqual([], self.transfer.requests)

        with patch.object(materializer.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "ROOT_RUNTIME_REQUIRED",
        ):
            materializer.materialize_verified_physical_release_candidate(
                config=self.config(), adapters=self.adapters(), now=NOW
            )
        self.assertEqual([], self.transfer.requests)

        self.quiescence.writers_active = True
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "QUIESCENCE_REJECTED",
        ):
            self.run_materializer()
        self.assertEqual([], self.transfer.requests)

    def test_signed_quiescence_gate_rejects_stale_wrong_root_wrong_generation_and_forged_token(self) -> None:
        stale = self.make_verified_quiescence_receipt(
            issued_at=NOW - timedelta(seconds=120),
            expires_at=NOW - timedelta(seconds=1),
            verification_now=NOW - timedelta(seconds=2),
        )
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "QUIESCENCE_RECEIPT_REJECTED",
        ):
            self.run_materializer(
                config_changes={"verified_writer_quiescence_receipt": stale}
            )
        self.assertEqual([], self.transfer.requests)

        wrong_policy = quiescence_receipt.PhysicalReleaseCandidateWriterQuiescenceSourceRootPolicy(
            source_root=Path("/srv/trading-bot-three-site/other-source"),
            required_mode=0o750,
        )
        wrong_config = replace(
            self.quiescence_receipt_verifier_config,
            source_root_policy=wrong_policy,
        )
        wrong_root = self.make_verified_quiescence_receipt(
            verifier_config=wrong_config,
            source_root=wrong_policy.source_root,
        )
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "QUIESCENCE_RECEIPT_REJECTED",
        ):
            self.run_materializer(
                config_changes={
                    "writer_quiescence_receipt_verifier_config": wrong_config,
                    "verified_writer_quiescence_receipt": wrong_root,
                }
            )
        self.assertEqual([], self.transfer.requests)

        wrong_generation = self.make_verified_quiescence_receipt(
            frozen_generation_sha256="e" * 64,
        )
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "QUIESCENCE_RECEIPT_REJECTED",
        ):
            self.run_materializer(
                config_changes={"verified_writer_quiescence_receipt": wrong_generation}
            )
        self.assertEqual([], self.transfer.requests)

        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "QUIESCENCE_RECEIPT_REQUIRED",
        ):
            self.run_materializer(
                config_changes={"verified_writer_quiescence_receipt": object()}
            )
        self.assertEqual([], self.transfer.requests)

    def test_unstable_source_dirty_target_or_conflated_target_adapter_refuse(self) -> None:
        self.source_git.value = replace(self.source_git.value, stable=False)
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "SOURCE_UNSTABLE",
        ):
            self.run_materializer()
        self.assertEqual([], self.transfer.requests)

        self.source_git.value = replace(self.source_git.value, stable=True)
        self.target_git.value = replace(self.target_git.value, clean=False)
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "SOURCE_DIRTY",
        ):
            self.run_materializer()
        self.assertEqual([], self.transfer.requests)

        self.target_git.value = replace(self.target_git.value, clean=True)
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "TARGET_NOT_INDEPENDENT",
        ):
            self.run_materializer(adapter_changes={"target_git_inspector": self.source_git})
        self.assertEqual([], self.transfer.requests)

    def test_extra_paths_symlink_and_target_hash_mismatch_fail_closed(self) -> None:
        self.transfer.extra_paths = ("tmp/not-reviewed.py",)
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "TRANSFER_EXTRA_PATHS",
        ):
            self.run_materializer()
        self.assertEqual(1, len(self.transfer.requests))
        self.assertEqual([], self.target_reader.calls)

        self.transfer.extra_paths = ()
        path = self.frozen.entries[0].relative_path
        good = self.target_reader.read_file(source_root=TARGET_ROOT, relative_path=path)
        self.target_reader.calls.clear()
        self.target_reader.overrides[path] = replace(good, symlink=True)
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "TARGET_REHASH_MISMATCH",
        ):
            self.run_materializer()
        self.assertEqual(2, len(self.transfer.requests))

    def test_post_transfer_extra_delta_and_changed_quiescence_fail_closed(self) -> None:
        self.overlay.changed_paths = ("unreviewed/extra.py",)
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "TARGET_OVERLAY_EXTRA_PATHS",
        ):
            self.run_materializer()
        self.assertEqual(1, len(self.transfer.requests))

        self.overlay.changed_paths = ()
        self.quiescence.calls.clear()
        self.quiescence.after_generation = "e" * 64
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "QUIESCENCE_CHANGED_DURING_TRANSFER",
        ):
            self.run_materializer()
        self.assertEqual(2, len(self.transfer.requests))

    def test_receipt_tampering_is_rejected(self) -> None:
        result = self.run_materializer()
        tampered = result.receipt.canonical_receipt.replace(
            b'"release_authorized":false', b'"release_authorized":true'
        )
        with self.assertRaisesRegex(
            materializer.PhysicalReleaseCandidateMaterializerError,
            "RECEIPT_BINDING_INVALID|RECEIPT_DIGEST_MISMATCH",
        ):
            materializer.parse_physical_release_candidate_materialization_receipt(tampered)
