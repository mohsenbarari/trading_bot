from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

from core.object_delta_source_batch_ledger import SourceBatchLedgerEntry, SourceStreamIdentity
from core.object_delta_source_publication_attempt import (
    OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_ID_PREFIX,
    REQUIRED_OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_PERSISTENCE,
    SOURCE_PUBLICATION_ATTESTATION_ACTION_RECORD,
    SOURCE_PUBLICATION_ATTESTATION_ACTION_REPLAY,
    SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY,
    SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE,
    SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL,
    SOURCE_PUBLICATION_LEDGER_ACTION_APPEND,
    SOURCE_PUBLICATION_LEDGER_ACTION_REPLAY,
    SOURCE_PUBLICATION_RECONCILIATION_ACTION_ADOPT,
    SOURCE_PUBLICATION_RECONCILIATION_ACTION_EXACT_PUT_REPLAY,
    SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD,
    SOURCE_PUBLICATION_UPLOAD_ACTION_REPLAY,
    ObjectDeltaSourcePublicationAttempt,
    ObjectDeltaSourcePublicationAttemptError,
    ObjectDeltaSourcePublicationAttestationArtifact,
    ObjectDeltaSourcePublicationCiphertextSpool,
    ObjectDeltaSourcePublicationExactReceipt,
    ObjectDeltaSourcePublicationIntent,
    ObjectDeltaSourcePublicationLedgeredAttempt,
    ObjectDeltaSourcePublicationObjectHistory,
    build_object_delta_source_publication_attempt,
    canonical_object_delta_source_transport_policy_bytes,
    derive_object_delta_source_publication_attempt_id,
    derive_object_delta_source_transport_policy_sha256,
    plan_object_delta_source_publication_attempt,
    plan_object_delta_source_publication_attestation,
    plan_object_delta_source_publication_exact_upload,
    plan_object_delta_source_publication_ledger,
    plan_object_delta_source_publication_reconciliation,
    plan_object_delta_source_publication_seal,
)
from core.object_delta_transport_binding import ObjectDeltaTransportPolicy


CAMPAIGN = "wa-ir-source-attempt-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-source-attempt-20260731"
OBJECT_KEY = (
    "campaigns/three-site/object-delta/v1/wa-ir-source-attempt-20260731/"
    "00000000000000000001-00000000000000000002-payload.age"
)
RECIPIENT = "age1" + "a" * 30


def transport_policy(
    *,
    bucket: str = "private-delta-bucket",
    ir_recipient: str = RECIPIENT,
) -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket=bucket,
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "c" * 30,
        webapp_ir_age_recipient=ir_recipient,
    )


def stream() -> SourceStreamIdentity:
    return SourceStreamIdentity(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
    )


def intent(**overrides: object) -> ObjectDeltaSourcePublicationIntent:
    values: dict[str, object] = {
        "stream": stream(),
        "writer_epoch": 7,
        "writer_lease_id": "lease-7",
        "first_sequence": 1,
        "last_sequence": 2,
        "prior_chain_sha256": "0" * 64,
        "payload_sha256": "a" * 64,
        "payload_bytes": 512,
        "object_key": OBJECT_KEY,
        "destination_age_recipient": RECIPIENT,
        "transport_policy_sha256": derive_object_delta_source_transport_policy_sha256(
            transport_policy()
        ),
        "source_cutover_artifact_sha256": "d" * 64,
        "source_cutover_artifact_bytes": 2048,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationIntent(**values)


def attempt(**overrides: object) -> ObjectDeltaSourcePublicationAttempt:
    source_intent = intent(**overrides)
    return build_object_delta_source_publication_attempt(source_intent)


def ciphertext(value: ObjectDeltaSourcePublicationAttempt, **overrides: object) -> ObjectDeltaSourcePublicationCiphertextSpool:
    values: dict[str, object] = {
        "attempt_id": value.attempt_id,
        "ciphertext_sha256": "b" * 64,
        "ciphertext_bytes": 768,
        "spool_sha256": "b" * 64,
        "spool_bytes": 768,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationCiphertextSpool(**values)


def sealed(value: ObjectDeltaSourcePublicationAttempt):
    reservation = plan_object_delta_source_publication_attempt(
        intent=value.intent,
        existing_state=None,
        existing_object_key_state=None,
    )
    plan = plan_object_delta_source_publication_seal(
        attempt=value,
        ciphertext=ciphertext(value),
        existing_state=reservation.attempt_to_insert,
    )
    return plan.sealed_attempt_to_write


def receipt(value, **overrides: object) -> ObjectDeltaSourcePublicationExactReceipt:
    values: dict[str, object] = {
        "attempt_id": value.attempt.attempt_id,
        "object_key": value.attempt.intent.object_key,
        "object_version_id": "version-20260731-01",
        "ciphertext_sha256": value.ciphertext.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext.ciphertext_bytes,
        "transport_receipt_artifact_sha256": "c" * 64,
        "transport_receipt_artifact_bytes": 512,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationExactReceipt(**values)


def uploaded(value):
    plan = plan_object_delta_source_publication_exact_upload(
        sealed_attempt=value,
        receipt=receipt(value),
        existing_state=value,
    )
    return plan.uploaded_attempt_to_write


def artifact(value, **overrides: object) -> ObjectDeltaSourcePublicationAttestationArtifact:
    values: dict[str, object] = {
        "attempt_id": value.sealed.attempt.attempt_id,
        "source_key_id": "ed25519-sha256:" + "d" * 64,
        "batch_sha256": "e" * 64,
        "source_attestation_artifact_sha256": "f" * 64,
        "source_attestation_artifact_bytes": 1024,
    }
    values.update(overrides)
    return ObjectDeltaSourcePublicationAttestationArtifact(**values)


def attested(value):
    plan = plan_object_delta_source_publication_attestation(
        uploaded_attempt=value,
        attestation=artifact(value),
        existing_state=value,
    )
    return plan.attested_attempt_to_write


def ledger_entry(value) -> SourceBatchLedgerEntry:
    source_intent = value.uploaded.sealed.attempt.intent
    source_receipt = value.uploaded.receipt
    return SourceBatchLedgerEntry(
        stream=source_intent.stream,
        first_sequence=source_intent.first_sequence,
        last_sequence=source_intent.last_sequence,
        writer_epoch=source_intent.writer_epoch,
        writer_lease_id=source_intent.writer_lease_id,
        prior_chain_sha256=source_intent.prior_chain_sha256,
        batch_sha256=value.attestation.batch_sha256,
        payload_sha256=source_intent.payload_sha256,
        payload_bytes=source_intent.payload_bytes,
        object_key=source_intent.object_key,
        object_version_id=source_receipt.object_version_id,
        ciphertext_sha256=source_receipt.ciphertext_sha256,
        ciphertext_bytes=source_receipt.ciphertext_bytes,
    )


class ObjectDeltaSourcePublicationAttemptTests(unittest.TestCase):
    def test_attempt_identity_is_deterministic_and_binds_full_logical_intent(self) -> None:
        first = intent()
        second = intent()
        first_id = derive_object_delta_source_publication_attempt_id(first)

        self.assertEqual(first_id, derive_object_delta_source_publication_attempt_id(second))
        self.assertTrue(first_id.startswith(OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_ID_PREFIX))
        self.assertNotEqual(
            first_id,
            derive_object_delta_source_publication_attempt_id(intent(payload_sha256="b" * 64)),
        )
        self.assertNotEqual(
            first_id,
            derive_object_delta_source_publication_attempt_id(intent(writer_epoch=8)),
        )
        self.assertNotEqual(
            first_id,
            derive_object_delta_source_publication_attempt_id(intent(object_key=OBJECT_KEY + ".other")),
        )
        self.assertNotEqual(
            first_id,
            derive_object_delta_source_publication_attempt_id(
                intent(transport_policy_sha256="e" * 64)
            ),
        )
        self.assertNotEqual(
            first_id,
            derive_object_delta_source_publication_attempt_id(
                intent(source_cutover_artifact_sha256="f" * 64)
            ),
        )
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "does not match"):
            ObjectDeltaSourcePublicationAttempt(intent=first, attempt_id="odsp-v1:" + "0" * 64)

    def test_reservation_is_create_only_and_only_exact_identity_replays(self) -> None:
        candidate = attempt()
        reserved = plan_object_delta_source_publication_attempt(
            intent=candidate.intent,
            existing_state=None,
            existing_object_key_state=None,
        )
        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE, reserved.action)
        self.assertEqual(candidate, reserved.attempt_to_insert)

        replay = plan_object_delta_source_publication_attempt(
            intent=candidate.intent,
            existing_state=candidate,
            existing_object_key_state=candidate,
        )
        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY, replay.action)
        self.assertIsNone(replay.attempt_to_insert)

        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "conflicts"):
            plan_object_delta_source_publication_attempt(
                intent=intent(payload_sha256="b" * 64),
                existing_state=candidate,
                existing_object_key_state=candidate,
            )

    def test_reservation_requires_consistent_dual_lookup_and_claims_object_key_across_controls(self) -> None:
        candidate = attempt()

        # A real adapter must independently lock/query both unique keys.  An
        # absent attempt-ID result combined with a present Object-key result
        # is corrupt/incomplete persistence evidence, not a replay shortcut.
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "lookups disagree"):
            plan_object_delta_source_publication_attempt(
                intent=candidate.intent,
                existing_state=None,
                existing_object_key_state=candidate,
            )
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "lookups disagree"):
            plan_object_delta_source_publication_attempt(
                intent=candidate.intent,
                existing_state=candidate,
                existing_object_key_state=None,
            )

        # The transport key does not itself include every control-plane fact.
        # No changed term, lease, chain, recipient, or payload may reserve a
        # second ciphertext against the already claimed immutable Object key.
        changed_intents = (
            intent(writer_epoch=8),
            intent(writer_lease_id="lease-8"),
            intent(prior_chain_sha256="b" * 64),
            intent(payload_sha256="c" * 64),
            intent(destination_age_recipient="age1" + "c" * 30),
            intent(
                transport_policy_sha256=derive_object_delta_source_transport_policy_sha256(
                    transport_policy(bucket="private-delta-bucket-2")
                )
            ),
            intent(source_cutover_artifact_sha256="e" * 64),
        )
        for changed in changed_intents:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "conflicts"):
                    plan_object_delta_source_publication_attempt(
                        intent=changed,
                        existing_state=None,
                        existing_object_key_state=candidate,
                    )

    def test_transport_policy_identity_is_canonical_and_binds_bucket_and_recipients(self) -> None:
        baseline = transport_policy()
        baseline_hash = derive_object_delta_source_transport_policy_sha256(baseline)
        self.assertEqual(
            baseline_hash,
            derive_object_delta_source_transport_policy_sha256(transport_policy()),
        )
        self.assertNotEqual(
            baseline_hash,
            derive_object_delta_source_transport_policy_sha256(
                transport_policy(bucket="private-delta-bucket-2")
            ),
        )
        self.assertNotEqual(
            baseline_hash,
            derive_object_delta_source_transport_policy_sha256(
                transport_policy(ir_recipient="age1" + "d" * 30)
            ),
        )
        canonical = canonical_object_delta_source_transport_policy_bytes(baseline)
        self.assertEqual(baseline_hash, hashlib.sha256(canonical).hexdigest())
        self.assertNotIn(b"presigned", canonical)

    def test_ciphertext_must_be_sealed_after_reservation_and_can_never_be_reencrypted(self) -> None:
        candidate = attempt()
        candidate_ciphertext = ciphertext(candidate)
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "before its durable reservation"):
            plan_object_delta_source_publication_seal(
                attempt=candidate,
                ciphertext=candidate_ciphertext,
                existing_state=None,
            )

        seal = plan_object_delta_source_publication_seal(
            attempt=candidate,
            ciphertext=candidate_ciphertext,
            existing_state=candidate,
        )
        self.assertEqual(SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL, seal.action)
        source_sealed = seal.sealed_attempt_to_write

        replay = plan_object_delta_source_publication_seal(
            attempt=candidate,
            ciphertext=candidate_ciphertext,
            existing_state=source_sealed,
        )
        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY, replay.action)
        self.assertIsNone(replay.sealed_attempt_to_write)

        changed = ciphertext(candidate, ciphertext_sha256="c" * 64, spool_sha256="c" * 64)
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "cannot be re-encrypted"):
            plan_object_delta_source_publication_seal(
                attempt=candidate,
                ciphertext=changed,
                existing_state=source_sealed,
            )

    def test_reconciliation_allows_only_exact_byte_replay_or_safe_singleton_adoption(self) -> None:
        source_sealed = sealed(attempt())
        empty = ObjectDeltaSourcePublicationObjectHistory(
            object_key=source_sealed.attempt.intent.object_key,
            version_ids=(),
            delete_marker_version_ids=(),
            latest_version_id=None,
            listing_complete=True,
        )
        replay = plan_object_delta_source_publication_reconciliation(
            sealed_attempt=source_sealed,
            history=empty,
            singleton_receipt=None,
            existing_state=source_sealed,
        )
        self.assertEqual(SOURCE_PUBLICATION_RECONCILIATION_ACTION_EXACT_PUT_REPLAY, replay.action)
        self.assertIsNone(replay.receipt_to_record)

        source_receipt = receipt(source_sealed)
        singleton = ObjectDeltaSourcePublicationObjectHistory(
            object_key=source_sealed.attempt.intent.object_key,
            version_ids=(source_receipt.object_version_id,),
            delete_marker_version_ids=(),
            latest_version_id=source_receipt.object_version_id,
            listing_complete=True,
        )
        adopt = plan_object_delta_source_publication_reconciliation(
            sealed_attempt=source_sealed,
            history=singleton,
            singleton_receipt=source_receipt,
            existing_state=source_sealed,
        )
        self.assertEqual(SOURCE_PUBLICATION_RECONCILIATION_ACTION_ADOPT, adopt.action)
        self.assertEqual(source_receipt, adopt.receipt_to_record)

        unsafe_histories = (
            ObjectDeltaSourcePublicationObjectHistory(
                object_key=source_sealed.attempt.intent.object_key,
                version_ids=(source_receipt.object_version_id, "version-other"),
                delete_marker_version_ids=(),
                latest_version_id="version-other",
                listing_complete=True,
            ),
            ObjectDeltaSourcePublicationObjectHistory(
                object_key=source_sealed.attempt.intent.object_key,
                version_ids=(source_receipt.object_version_id,),
                delete_marker_version_ids=("delete-marker-1",),
                latest_version_id=source_receipt.object_version_id,
                listing_complete=True,
            ),
        )
        for history in unsafe_histories:
            with self.subTest(history=history), self.assertRaisesRegex(
                ObjectDeltaSourcePublicationAttemptError,
                "safe immutable singleton",
            ):
                plan_object_delta_source_publication_reconciliation(
                    sealed_attempt=source_sealed,
                    history=history,
                    singleton_receipt=source_receipt,
                    existing_state=source_sealed,
                )

        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "does not match"):
            plan_object_delta_source_publication_reconciliation(
                sealed_attempt=source_sealed,
                history=singleton,
                singleton_receipt=replace(source_receipt, ciphertext_sha256="d" * 64),
                existing_state=source_sealed,
            )

    def test_exact_upload_receipt_and_source_attestation_are_immutable(self) -> None:
        source_sealed = sealed(attempt())
        source_receipt = receipt(source_sealed)
        upload = plan_object_delta_source_publication_exact_upload(
            sealed_attempt=source_sealed,
            receipt=source_receipt,
            existing_state=source_sealed,
        )
        self.assertEqual(SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD, upload.action)
        source_uploaded = upload.uploaded_attempt_to_write

        upload_replay = plan_object_delta_source_publication_exact_upload(
            sealed_attempt=source_sealed,
            receipt=source_receipt,
            existing_state=source_uploaded,
        )
        self.assertEqual(SOURCE_PUBLICATION_UPLOAD_ACTION_REPLAY, upload_replay.action)

        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "conflicts with replay"):
            plan_object_delta_source_publication_exact_upload(
                sealed_attempt=source_sealed,
                receipt=replace(source_receipt, object_version_id="version-20260731-02"),
                existing_state=source_uploaded,
            )

        source_artifact = artifact(source_uploaded)
        attestation = plan_object_delta_source_publication_attestation(
            uploaded_attempt=source_uploaded,
            attestation=source_artifact,
            existing_state=source_uploaded,
        )
        self.assertEqual(SOURCE_PUBLICATION_ATTESTATION_ACTION_RECORD, attestation.action)
        source_attested = attestation.attested_attempt_to_write

        attestation_replay = plan_object_delta_source_publication_attestation(
            uploaded_attempt=source_uploaded,
            attestation=source_artifact,
            existing_state=source_attested,
        )
        self.assertEqual(SOURCE_PUBLICATION_ATTESTATION_ACTION_REPLAY, attestation_replay.action)
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "conflicts with replay"):
            plan_object_delta_source_publication_attestation(
                uploaded_attempt=source_uploaded,
                attestation=artifact(source_uploaded, source_attestation_artifact_sha256="a" * 64),
                existing_state=source_attested,
            )

    def test_resume_cannot_reconcile_or_mutate_after_receipt_or_terminal_ledger(self) -> None:
        source_sealed = sealed(attempt())
        source_receipt = receipt(source_sealed)
        source_uploaded = uploaded(source_sealed)
        empty = ObjectDeltaSourcePublicationObjectHistory(
            object_key=source_sealed.attempt.intent.object_key,
            version_ids=(),
            delete_marker_version_ids=(),
            latest_version_id=None,
            listing_complete=True,
        )
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "sealed unresolved"):
            plan_object_delta_source_publication_reconciliation(
                sealed_attempt=source_sealed,
                history=empty,
                singleton_receipt=None,
                existing_state=source_uploaded,
            )

        source_attested = attested(source_uploaded)
        candidate = ledger_entry(source_attested)
        appended = plan_object_delta_source_publication_ledger(
            attested_attempt=source_attested,
            candidate_ledger_entry=candidate,
            existing_state=source_attested,
            existing_ledger_entry=None,
        )
        terminal = appended.ledgered_attempt_to_write
        self.assertIsNotNone(terminal)

        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "cannot be re-encrypted"):
            plan_object_delta_source_publication_seal(
                attempt=source_sealed.attempt,
                ciphertext=ciphertext(
                    source_sealed.attempt,
                    ciphertext_sha256="f" * 64,
                    spool_sha256="f" * 64,
                ),
                existing_state=terminal,
            )
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "missing its source ledger"):
            plan_object_delta_source_publication_ledger(
                attested_attempt=source_attested,
                candidate_ledger_entry=candidate,
                existing_state=terminal,
                existing_ledger_entry=None,
            )
        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "exists without terminal"):
            plan_object_delta_source_publication_ledger(
                attested_attempt=source_attested,
                candidate_ledger_entry=candidate,
                existing_state=source_attested,
                existing_ledger_entry=candidate,
            )

    def test_source_ledger_requires_exact_attested_batch_and_only_replays_exact_entry(self) -> None:
        source_attested = attested(uploaded(sealed(attempt())))
        candidate = ledger_entry(source_attested)
        append = plan_object_delta_source_publication_ledger(
            attested_attempt=source_attested,
            candidate_ledger_entry=candidate,
            existing_state=source_attested,
            existing_ledger_entry=None,
        )
        self.assertEqual(SOURCE_PUBLICATION_LEDGER_ACTION_APPEND, append.action)
        self.assertEqual(candidate, append.ledger_entry)
        self.assertIsInstance(
            append.ledgered_attempt_to_write,
            ObjectDeltaSourcePublicationLedgeredAttempt,
        )

        replay = plan_object_delta_source_publication_ledger(
            attested_attempt=source_attested,
            candidate_ledger_entry=candidate,
            existing_state=append.ledgered_attempt_to_write,
            existing_ledger_entry=candidate,
        )
        self.assertEqual(SOURCE_PUBLICATION_LEDGER_ACTION_REPLAY, replay.action)

        with self.assertRaisesRegex(ObjectDeltaSourcePublicationAttemptError, "does not match"):
            plan_object_delta_source_publication_ledger(
                attested_attempt=source_attested,
                candidate_ledger_entry=replace(candidate, batch_sha256="a" * 64),
                existing_state=source_attested,
                existing_ledger_entry=None,
            )

    def test_persistence_obligations_cover_the_cross_resource_crash_boundary(self) -> None:
        obligations = "\n".join(REQUIRED_OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_PERSISTENCE)
        self.assertIn("before every PUT", obligations)
        self.assertIn("list all versions and delete markers", obligations)
        self.assertIn("never encrypt replacement bytes", obligations)
        self.assertIn("source-cutover", obligations)
        self.assertIn("source-attestation", obligations)
        self.assertIn("atomically", obligations)


class ObjectDeltaSourcePublicationAttemptStaticTests(unittest.TestCase):
    def test_contract_has_no_io_database_or_runtime_enablement_capability(self) -> None:
        path = Path(__file__).resolve().parents[1] / "core" / "object_delta_source_publication_attempt.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        forbidden = {
            "age",
            "aiohttp",
            "boto3",
            "botocore",
            "http",
            "httpx",
            "os",
            "pathlib",
            "requests",
            "socket",
            "sqlalchemy",
            "subprocess",
            "urllib",
        }
        self.assertFalse(
            [
                value
                for value in imports
                if value in forbidden or value.startswith(("boto.", "urllib."))
            ]
        )
        forbidden_calls = {
            "add",
            "begin",
            "commit",
            "connect",
            "delete",
            "execute",
            "flush",
            "open",
            "put_object",
            "rollback",
            "send",
            "write_bytes",
        }
        self.assertFalse(
            [
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
            ]
        )


if __name__ == "__main__":
    unittest.main()
