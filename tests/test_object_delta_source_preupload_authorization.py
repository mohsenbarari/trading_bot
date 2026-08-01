from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import GENESIS_PRIOR_CHAIN_SHA256, WriterTermBinding, sha256_bytes
from core.object_delta_baseline_manifest import build_object_delta_baseline_manifest
from core.object_delta_batch_assembler import PreparedObjectDeltaPayload
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
    canonical_object_delta_source_cutover_attestation_bytes,
)
from core.object_delta_source_cutover_publication_gate import ObjectDeltaSourceCutoverPublicationPin
from core.object_delta_source_preupload_authorization import (
    AuthorizedObjectDeltaSourcePreupload,
    ObjectDeltaSourcePreuploadAuthorizationError,
    authorize_object_delta_source_preupload,
    project_authorized_object_delta_source_preupload_attempt,
    project_authorized_object_delta_source_preupload_intent,
    require_authorized_object_delta_source_preupload,
)
from core.object_delta_source_publication_snapshot import (
    ObjectDeltaLockedSourcePublicationSnapshot,
    snapshot_locked_object_delta_source_publication,
)
import core.object_delta_source_preupload_authorization as preupload_authorization
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    derive_object_delta_object_key,
)
from models.object_delta import ObjectDeltaStream
from tests.test_object_delta_source_publication_snapshot import (
    _LockedSnapshotSession,
    outbox_row,
    terminal_row,
)
from tests.test_object_delta_batch_assembler import FINGERPRINT


CAMPAIGN = "wa-ir-preupload-authorization-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-preupload-authorization-20260731"
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30


class ForgedPreparedPayload(PreparedObjectDeltaPayload):
    pass


class ForgedLockedSnapshot(ObjectDeltaLockedSourcePublicationSnapshot):
    pass


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class ObjectDeltaSourcePreuploadAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = Ed25519PrivateKey.generate()
        self.binding = ObjectDeltaSourceRuntimeBinding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            stream_generation_id=GENERATION,
            expected_registry_fingerprint=FINGERPRINT,
        )
        self.policy = ObjectDeltaTransportPolicy(
            bucket="private-delta-bucket",
            prefix="campaigns/three-site",
            webapp_fi_age_recipient=FI_RECIPIENT,
            webapp_ir_age_recipient=IR_RECIPIENT,
        )
        self.pin = ObjectDeltaSourceCutoverPublicationPin(
            binding=self.binding,
            expected_source_public_key=public_key(self.signer),
            transport_policy=self.policy,
        )
        gate_id = str(uuid4())
        snapshot = {
            "source_generation": "webapp-fi-snapshot-20260731",
            "snapshot_id": "20260731T120000Z-0123456789abcdef",
            "release_sha": RELEASE,
            "alembic_revision": "f2c7d8e9a0b1",
            "manifest_object_key": "campaigns/wa-ir/snapshots/manifest.json.age",
            "manifest_object_version_id": "version-20260731-01",
            "manifest_ciphertext_sha256": "a" * 64,
            "manifest_ciphertext_bytes": 1024,
            "database_sha256": "b" * 64,
            "uploads_sha256": "c" * 64,
        }
        baseline = build_object_delta_baseline_manifest(
            source_site=self.binding.source_site,
            destination_site=self.binding.destination_site,
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            stream_generation_id=self.binding.stream_generation_id,
            registry_fingerprint=self.binding.expected_registry_fingerprint,
            writer_epoch=7,
            writer_lease_id="lease-7",
            snapshot=snapshot,
            write_gate_id=gate_id,
            source_signer=self.signer,
        )
        self.cutover = build_object_delta_source_cutover_attestation(
            cutover=ObjectDeltaSourceCutoverRecord(
                source_site=self.binding.source_site,
                destination_site=self.binding.destination_site,
                campaign_id=self.binding.campaign_id,
                release_sha=self.binding.release_sha,
                stream_generation_id=self.binding.stream_generation_id,
                state="baseline_published",
                registry_fingerprint=self.binding.expected_registry_fingerprint,
                writer_epoch=7,
                writer_lease_id="lease-7",
                write_gate_id=gate_id,
                source_generation="webapp-fi-snapshot-20260731",
                snapshot_id="20260731T120000Z-0123456789abcdef",
                alembic_revision="f2c7d8e9a0b1",
                snapshot_manifest_object_key="campaigns/wa-ir/snapshots/manifest.json.age",
                snapshot_manifest_object_version_id="version-20260731-01",
                snapshot_manifest_ciphertext_sha256="a" * 64,
                snapshot_manifest_ciphertext_bytes=1024,
                database_sha256="b" * 64,
                uploads_sha256="c" * 64,
                baseline_manifest_object_key="campaigns/wa-ir/baselines/manifest.json.age",
                baseline_manifest_object_version_id="version-20260731-02",
                baseline_manifest_ciphertext_sha256="d" * 64,
                baseline_manifest_ciphertext_bytes=2048,
            ),
            baseline_manifest=baseline,
            source_signer=self.signer,
        )
        self.raw_cutover = canonical_object_delta_source_cutover_attestation_bytes(self.cutover)

    def locked_snapshot(
        self,
        *,
        no_work: bool = False,
        terminal: bool = False,
    ) -> ObjectDeltaLockedSourcePublicationSnapshot:
        """Mint a real adapter snapshot through its test-only locked-session double."""

        self.assertFalse(no_work and terminal)
        first_sequence = 3 if terminal else 1
        terminal_entry = terminal_row() if terminal else None
        stream = ObjectDeltaStream(
            id=701,
            source_site=self.binding.source_site,
            destination_site=self.binding.destination_site,
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            stream_generation_id=self.binding.stream_generation_id,
            next_sequence=first_sequence if no_work else first_sequence + 1,
        )
        result = asyncio.run(
            snapshot_locked_object_delta_source_publication(
                _LockedSnapshotSession(
                    stream=stream,
                    terminal=terminal_entry,
                    outbox=() if no_work else (outbox_row(sequence=first_sequence),),
                ),
                self.binding,
                max_items=4,
                maximum_payload_bytes=1024 * 1024,
            )
        )
        self.assertIsInstance(result, ObjectDeltaLockedSourcePublicationSnapshot)
        return result

    def authorize(self, snapshot: ObjectDeltaLockedSourcePublicationSnapshot | None = None):
        return authorize_object_delta_source_preupload(
            pin=self.pin,
            locked_snapshot=self.locked_snapshot() if snapshot is None else snapshot,
            source_cutover_attestation=self.raw_cutover,
        )

    def test_valid_opaque_locked_snapshot_derives_exact_immutable_intent_and_attempt(self) -> None:
        locked_snapshot = self.locked_snapshot()
        prepared = locked_snapshot.prepared_payload
        self.assertIsNotNone(prepared)
        authorized = self.authorize(locked_snapshot)
        required = require_authorized_object_delta_source_preupload(authorized)
        intent = project_authorized_object_delta_source_preupload_intent(authorized)
        attempt = project_authorized_object_delta_source_preupload_attempt(authorized)

        self.assertIs(locked_snapshot, authorized.locked_snapshot)
        self.assertIs(authorized, required)
        self.assertEqual((7, "lease-7"), (intent.writer_epoch, intent.writer_lease_id))
        self.assertEqual((1, 1), (intent.first_sequence, intent.last_sequence))
        self.assertEqual(GENESIS_PRIOR_CHAIN_SHA256, intent.prior_chain_sha256)
        self.assertEqual(prepared.payload_sha256, intent.payload_sha256)
        self.assertEqual(len(prepared.payload), intent.payload_bytes)
        self.assertEqual(IR_RECIPIENT, intent.destination_age_recipient)
        self.assertEqual(
            derive_object_delta_object_key(
                self.policy,
                source_site=self.binding.source_site,
                destination_site=self.binding.destination_site,
                campaign_id=self.binding.campaign_id,
                release_sha=self.binding.release_sha,
                stream_generation_id=self.binding.stream_generation_id,
                first_sequence=1,
                last_sequence=1,
                payload_sha256=prepared.payload_sha256,
            ),
            intent.object_key,
        )
        self.assertEqual(sha256_bytes(self.raw_cutover), intent.source_cutover_artifact_sha256)
        self.assertEqual(len(self.raw_cutover), intent.source_cutover_artifact_bytes)
        self.assertEqual(intent, attempt.intent)
        self.assertTrue(attempt.attempt_id.startswith("odsp-v1:"))

    def test_terminal_ledger_frontier_and_prior_chain_are_derived_from_locked_snapshot(self) -> None:
        locked_snapshot = self.locked_snapshot(terminal=True)
        self.assertIsNotNone(locked_snapshot.terminal_ledger_entry)
        authorized = self.authorize(locked_snapshot)
        intent = project_authorized_object_delta_source_preupload_intent(authorized)

        self.assertEqual((3, 3), (intent.first_sequence, intent.last_sequence))
        self.assertEqual("b" * 64, intent.prior_chain_sha256)
        self.assertEqual(
            (7, "lease-7"),
            (intent.writer_epoch, intent.writer_lease_id),
        )

    def test_only_adapter_minted_opaque_snapshot_is_accepted_not_raw_payload_or_dataclass(self) -> None:
        locked_snapshot = self.locked_snapshot()
        prepared = locked_snapshot.prepared_payload
        self.assertIsNotNone(prepared)
        manual = ObjectDeltaLockedSourcePublicationSnapshot(
            binding=locked_snapshot.binding,
            stream=locked_snapshot.stream,
            source_stream_id=locked_snapshot.source_stream_id,
            cutover_writer_term=locked_snapshot.cutover_writer_term,
            terminal_ledger_entry=locked_snapshot.terminal_ledger_entry,
            prior_chain_sha256=locked_snapshot.prior_chain_sha256,
            prepared_payload=prepared,
        )
        subclassed = ForgedLockedSnapshot(
            binding=locked_snapshot.binding,
            stream=locked_snapshot.stream,
            source_stream_id=locked_snapshot.source_stream_id,
            cutover_writer_term=locked_snapshot.cutover_writer_term,
            terminal_ledger_entry=locked_snapshot.terminal_ledger_entry,
            prior_chain_sha256=locked_snapshot.prior_chain_sha256,
            prepared_payload=prepared,
        )

        for forged in (prepared, manual, replace(locked_snapshot), subclassed):
            with self.subTest(forged_type=type(forged).__name__):
                with patch.object(
                    preupload_authorization,
                    "derive_object_delta_object_key",
                    side_effect=AssertionError("derivation must not run"),
                ) as derive:
                    with self.assertRaisesRegex(
                        ObjectDeltaSourcePreuploadAuthorizationError,
                        "locked snapshot",
                    ):
                        self.authorize(forged)  # type: ignore[arg-type]
                derive.assert_not_called()

    def test_payload_provenance_term_prior_chain_and_stream_id_tampering_fail_closed(self) -> None:
        locked_snapshot = self.locked_snapshot()
        prepared = locked_snapshot.prepared_payload
        self.assertIsNotNone(prepared)
        manual_prepared = PreparedObjectDeltaPayload(
            stream=prepared.stream,
            writer_term=prepared.writer_term,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            sequence_ids=prepared.sequence_ids,
            payload=prepared.payload,
            payload_sha256=prepared.payload_sha256,
        )
        replaced_prepared = replace(prepared)
        subclassed_prepared = ForgedPreparedPayload(
            stream=prepared.stream,
            writer_term=prepared.writer_term,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            sequence_ids=prepared.sequence_ids,
            payload=prepared.payload,
            payload_sha256=prepared.payload_sha256,
        )

        for replacement, expected_error in (
            (manual_prepared, "prepared payload provenance"),
            (replaced_prepared, "prepared payload provenance"),
            (subclassed_prepared, "locked snapshot"),
        ):
            with self.subTest(payload_type=type(replacement).__name__):
                candidate = self.locked_snapshot()
                object.__setattr__(candidate, "prepared_payload", replacement)
                with patch.object(
                    preupload_authorization,
                    "derive_object_delta_object_key",
                    side_effect=AssertionError("derivation must not run"),
                ) as derive:
                    with self.assertRaisesRegex(
                        ObjectDeltaSourcePreuploadAuthorizationError,
                        expected_error,
                    ):
                        self.authorize(candidate)
                derive.assert_not_called()

        candidate = self.locked_snapshot()
        object.__setattr__(candidate, "cutover_writer_term", WriterTermBinding(epoch=8, lease_id="lease-8"))
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "locked snapshot"):
            self.authorize(candidate)

        candidate = self.locked_snapshot()
        object.__setattr__(candidate, "prior_chain_sha256", "f" * 64)
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "locked snapshot"):
            self.authorize(candidate)

        candidate = self.locked_snapshot()
        object.__setattr__(candidate, "source_stream_id", 0)
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "locked snapshot"):
            self.authorize(candidate)

    def test_forged_capability_policy_cutover_and_raw_intent_fail_closed(self) -> None:
        authorized = self.authorize()
        raw_intent = project_authorized_object_delta_source_preupload_intent(authorized)
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "capability is required"):
            require_authorized_object_delta_source_preupload(raw_intent)

        forged = AuthorizedObjectDeltaSourcePreupload(
            pin=authorized.pin,
            locked_snapshot=authorized.locked_snapshot,
            source_cutover_attestation=authorized.source_cutover_attestation,
            intent=authorized.intent,
            attempt=authorized.attempt,
        )
        for value in (forged, replace(authorized)):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "not verified"):
                    require_authorized_object_delta_source_preupload(value)

        object.__setattr__(
            authorized.pin,
            "transport_policy",
            ObjectDeltaTransportPolicy(
                bucket="private-delta-bucket-2",
                prefix="campaigns/three-site",
                webapp_fi_age_recipient=FI_RECIPIENT,
                webapp_ir_age_recipient=IR_RECIPIENT,
            ),
        )
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "intent does not match"):
            require_authorized_object_delta_source_preupload(authorized)

        # Use a fresh capability because the prior one was deliberately
        # mutated in place to prove policy revalidation.
        authorized = self.authorize()
        object.__setattr__(authorized, "source_cutover_attestation", b"{}")
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "cutover evidence"):
            require_authorized_object_delta_source_preupload(authorized)

    def test_only_raw_canonical_cutover_and_publishable_locked_prefix_are_accepted(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "raw canonical"):
            authorize_object_delta_source_preupload(
                pin=self.pin,
                locked_snapshot=self.locked_snapshot(),
                source_cutover_attestation=self.cutover,
            )
        with self.assertRaisesRegex(ObjectDeltaSourcePreuploadAuthorizationError, "no publishable"):
            self.authorize(self.locked_snapshot(no_work=True))


class ObjectDeltaSourcePreuploadAuthorizationStaticTests(unittest.TestCase):
    def test_module_is_pure_and_exposes_only_the_authorization_actions(self) -> None:
        path = Path(__file__).parents[1] / "core/object_delta_source_preupload_authorization.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            {"boto3", "requests", "httpx", "socket", "subprocess", "sqlalchemy", "os", "pathlib"}
            & imported_roots
        )
        public_functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        }
        self.assertEqual(
            {
                "authorize_object_delta_source_preupload",
                "require_authorized_object_delta_source_preupload",
                "project_authorized_object_delta_source_preupload_intent",
                "project_authorized_object_delta_source_preupload_attempt",
            },
            public_functions,
        )
        parameters = inspect.signature(authorize_object_delta_source_preupload).parameters
        self.assertEqual(
            {"pin", "locked_snapshot", "source_cutover_attestation"},
            set(parameters),
        )
        self.assertNotIn("prepared_payload", parameters)
        self.assertNotIn("prior_chain_sha256", parameters)
        text = path.read_text(encoding="utf-8")
        self.assertIn("same transaction", text)
        self.assertIn("live Writer Witness term", text)
        self.assertIn("caller-supplied raw intent", text)
        self.assertIn("prepared payload", text)


if __name__ == "__main__":
    unittest.main()
