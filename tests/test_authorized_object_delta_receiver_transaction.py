from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import (
    GENESIS_PRIOR_CHAIN_SHA256,
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    sha256_bytes,
    validate_delta_batch,
)
from core.authorized_object_delta_receiver_transaction import (
    AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT,
    AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY,
    AUTHORIZED_RECEIVER_TRANSACTION_ACTION_NONCE_ONLY,
    AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY,
    AuthorizedObjectDeltaReceiverTransactionError,
    LockedAuthorizedObjectDeltaReceiverPlans,
    coordinate_authorized_object_delta_receiver_transaction,
)
from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    build_unsigned_object_delta_delivery_control_packet,
    controller_key_id_from_public_key,
    sign_object_delta_delivery_control_packet,
    verify_object_delta_delivery_control_packet,
)
from core.object_delta_baseline_manifest import (
    ObjectDeltaReceiverRestoreAttestation,
    build_object_delta_baseline_manifest,
)
from core.object_delta_receiver_genesis_admission import (
    admit_object_delta_receiver_genesis,
    build_object_delta_receiver_restore_evidence,
    verify_object_delta_receiver_genesis_baseline,
    verify_object_delta_receiver_genesis_cutover,
    verify_object_delta_receiver_restore_evidence,
)
from core.object_delta_import_plan import (
    IMPORT_ACTION_APPLY,
    IMPORT_ACTION_REPLAY,
    AtomicObjectDeltaImportPlan,
    PlannedObjectDeltaChange,
    ReceiverStreamCursor,
    expected_import_receipt,
)
from core.object_delta_mvp_canonical import INSERT, validate_canonical_mvp_object_delta
from core.object_delta_receiver_mvp_handlers import (
    compile_object_delta_mvp_receiver_planned_change,
)
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverRecord,
    build_object_delta_source_cutover_attestation,
)
from core.object_delta_receiver_apply_scope import (
    AuthorizedObjectDeltaReceiverDelivery,
    ObjectDeltaReceiverApplyScopeError,
    authorize_object_delta_receiver_delivery,
)
from core.object_delta_receiver_delivery_binding import ObjectDeltaReceiverDeliveryBinding
from core.object_delta_source_batch_attestation import (
    build_object_delta_source_batch_attestation,
    source_key_id_from_public_key,
)
from core.object_delta_receiver_delivery_nonce import (
    ObjectDeltaReceiverDeliveryNoncePlan,
    expected_object_delta_receiver_delivery_nonce_receipt,
    plan_object_delta_receiver_delivery_nonce_consumption,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)


CAMPAIGN = "wa-ir-authorized-receiver-transaction-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-authorized-receiver-stream-20260731"
PAYLOAD = b'{"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1","items":[]}'
ISSUED_AT = datetime(2026, 7, 31, 13, 0, 0, tzinfo=timezone.utc)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=4)
OBSERVED_AT = ISSUED_AT + timedelta(seconds=1)
CONTROLLER_PRIVATE_KEY = bytes(range(1, 33))
SOURCE_PRIVATE_KEY = bytes(range(33, 65))


def _controller_public_key() -> bytes:
    return Ed25519PrivateKey.from_private_bytes(CONTROLLER_PRIVATE_KEY).public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _source_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(SOURCE_PRIVATE_KEY)


def _source_public_key() -> bytes:
    return _source_private_key().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _policy() -> ObjectDeltaTransportPolicy:
    return ObjectDeltaTransportPolicy(
        bucket="private-delta-bucket",
        prefix="campaigns/three-site",
        webapp_fi_age_recipient="age1" + "a" * 30,
        webapp_ir_age_recipient="age1" + "c" * 30,
    )


def _batch(*, first_sequence: int = 2):
    sequence_ids = (first_sequence, first_sequence + 1)
    object_key = derive_object_delta_object_key(
        _policy(),
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=sequence_ids[0],
        last_sequence=sequence_ids[-1],
        payload_sha256=sha256_bytes(PAYLOAD),
    )
    return validate_delta_batch(
        build_delta_batch(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            stream_generation_id=GENERATION,
            stream_sequence_ids=sequence_ids,
            payload=PAYLOAD,
            prior_chain_sha256=(
                GENESIS_PRIOR_CHAIN_SHA256 if first_sequence == 1 else "e" * 64
            ),
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": "read_back_verified",
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": f"version-20260731-authorized-{first_sequence:02d}",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )


def _authorization(
    *,
    first_sequence: int = 2,
    mutate_source_attestation=None,
    controller_public_key_override: bytes | None = None,
) -> AuthorizedObjectDeltaReceiverDelivery:
    batch = _batch(first_sequence=first_sequence)
    policy = _policy()
    packet_controller_public_key = _controller_public_key()
    controller_public_key = controller_public_key_override or packet_controller_public_key
    source_public_key = _source_public_key()
    controller_key_id = controller_key_id_from_public_key(packet_controller_public_key)
    unsigned_packet = build_unsigned_object_delta_delivery_control_packet(
        policy=policy,
        batch=batch,
        binding=bind_object_delta_batch(policy=policy, batch=batch),
        issued_at=ISSUED_AT,
        expires_at=EXPIRES_AT,
        nonce="b" * 64,
        controller_public_key=packet_controller_public_key,
    )
    packet = verify_object_delta_delivery_control_packet(
        sign_object_delta_delivery_control_packet(
            unsigned_packet,
            controller_signer=Ed25519PrivateKey.from_private_bytes(CONTROLLER_PRIVATE_KEY),
        ),
        policy=policy,
        expected_destination_site="webapp_ir",
        pinned_controller_public_key=packet_controller_public_key,
        observed_at=OBSERVED_AT,
    )
    binding = ObjectDeltaReceiverDeliveryBinding(
        policy=policy,
        permit=ObjectDeltaReceiverDeliveryPermit(
            source_site=batch.source_site,
            destination_site=batch.destination_site,
            campaign_id=batch.campaign_id,
            release_sha=batch.release_sha,
            stream_generation_id=batch.stream.generation_id,
            bucket=policy.bucket,
            destination_age_recipient=packet.destination_age_recipient,
            controller_key_id=controller_key_id,
            writer_epoch=batch.writer_term.epoch,
            writer_lease_id=batch.writer_term.lease_id,
        ),
        source_public_key=source_public_key,
        source_key_id=source_key_id_from_public_key(source_public_key),
        controller_public_key=controller_public_key,
        expected_registry_fingerprint="0123456789abcdef",
    )
    source_attestation = build_object_delta_source_batch_attestation(
        batch=batch,
        transport_policy=policy,
        transport_binding=bind_object_delta_batch(policy=policy, batch=batch),
        source_signer=_source_private_key(),
    )
    if mutate_source_attestation is not None:
        source_attestation = mutate_source_attestation(source_attestation)
    return authorize_object_delta_receiver_delivery(
        binding=binding,
        verified_packet=packet,
        batch=batch,
        source_attestation=source_attestation,
    )


def _apply_import_plan(authorization: AuthorizedObjectDeltaReceiverDelivery) -> AtomicObjectDeltaImportPlan:
    batch = authorization.batch

    def receiver_change(sequence: int, change_log_id: int, name: str):
        return compile_object_delta_mvp_receiver_planned_change(
            logical_sequence=sequence,
            change_log_id=change_log_id,
            descriptor=validate_canonical_mvp_object_delta(
                {
                    "table": "commodities",
                    "operation": INSERT,
                    "identity": {"name": name},
                    "fields": {},
                    "references": {},
                }
            ),
        )

    return AtomicObjectDeltaImportPlan(
        action=IMPORT_ACTION_APPLY,
        receipt_to_insert=expected_import_receipt(batch),
        cursor_to_write=ReceiverStreamCursor(
            source_site=batch.source_site,
            destination_site=batch.destination_site,
            campaign_id=batch.campaign_id,
            release_sha=batch.release_sha,
            stream_generation_id=batch.stream.generation_id,
            last_sequence=batch.stream.last_sequence,
            last_batch_sha256=batch.batch_sha256,
        ),
        changes_to_apply=(
            receiver_change(batch.stream.sequence_ids[0], 701, "Coordinator one"),
            receiver_change(batch.stream.sequence_ids[1], 702, "Coordinator two"),
        ),
    )


def _replay_import_plan() -> AtomicObjectDeltaImportPlan:
    return AtomicObjectDeltaImportPlan(
        action=IMPORT_ACTION_REPLAY,
        receipt_to_insert=None,
        cursor_to_write=None,
        changes_to_apply=(),
    )


def _nonce_plan(
    authorization: AuthorizedObjectDeltaReceiverDelivery,
    *,
    existing: bool,
) -> ObjectDeltaReceiverDeliveryNoncePlan:
    receipt = expected_object_delta_receiver_delivery_nonce_receipt(
        packet=authorization.verified_packet,
        batch=authorization.batch,
        observed_at=OBSERVED_AT,
    )
    return plan_object_delta_receiver_delivery_nonce_consumption(
        expected=receipt,
        existing=receipt if existing else None,
    )


def _genesis_admission(authorization: AuthorizedObjectDeltaReceiverDelivery):
    """Build genuine independent local proof for coordinator-boundary tests."""

    source_signer = _source_private_key()
    source_public_key = _source_public_key()
    write_gate_id = str(uuid4())
    manifest = build_object_delta_baseline_manifest(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        registry_fingerprint="0123456789abcdef",
        writer_epoch=7,
        writer_lease_id="writer-lease-7",
        snapshot={
            "source_generation": "webapp-fi-snapshot-20260731",
            "snapshot_id": "20260731T160000Z-0123456789abcdef",
            "release_sha": RELEASE,
            "alembic_revision": "f2c7d8e9a0b1",
            "manifest_object_key": "campaigns/wa-ir/snapshots/manifest.json.age",
            "manifest_object_version_id": "snapshot-version-20260731-01",
            "manifest_ciphertext_sha256": "a" * 64,
            "manifest_ciphertext_bytes": 1024,
            "database_sha256": "b" * 64,
            "uploads_sha256": "c" * 64,
        },
        write_gate_id=write_gate_id,
        source_signer=source_signer,
    )
    baseline = verify_object_delta_receiver_genesis_baseline(
        manifest,
        expected_source_public_key=source_public_key,
        expected_source_site="webapp_fi",
        expected_destination_site="webapp_ir",
        expected_campaign_id=CAMPAIGN,
        expected_release_sha=RELEASE,
        expected_stream_generation_id=GENERATION,
        expected_registry_fingerprint="0123456789abcdef",
    )
    source = baseline.baseline
    restore = ObjectDeltaReceiverRestoreAttestation(
        source_site=source.source_site,
        destination_site=source.destination_site,
        campaign_id=source.campaign_id,
        release_sha=source.release_sha,
        stream_generation_id=source.stream_generation_id,
        registry_fingerprint=source.registry_fingerprint,
        source_generation=source.source_generation,
        snapshot_id=source.snapshot_id,
        alembic_revision=source.alembic_revision,
        manifest_object_key=source.manifest_object_key,
        manifest_object_version_id=source.manifest_object_version_id,
        manifest_ciphertext_sha256=source.manifest_ciphertext_sha256,
        manifest_ciphertext_bytes=source.manifest_ciphertext_bytes,
        database_sha256=source.database_sha256,
        uploads_sha256=source.uploads_sha256,
    )
    receiver_signer = Ed25519PrivateKey.from_private_bytes(bytes(range(65, 97)))
    receiver_public_key = receiver_signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    restore_evidence = verify_object_delta_receiver_restore_evidence(
        build_object_delta_receiver_restore_evidence(
            restore=restore,
            baseline_manifest_sha256=source.manifest_sha256,
            receiver_verifier_signer=receiver_signer,
        ),
        expected_receiver_verifier_public_key=receiver_public_key,
        baseline=baseline,
    )
    cutover = verify_object_delta_receiver_genesis_cutover(
        build_object_delta_source_cutover_attestation(
            cutover=ObjectDeltaSourceCutoverRecord(
                source_site=source.source_site,
                destination_site=source.destination_site,
                campaign_id=source.campaign_id,
                release_sha=source.release_sha,
                stream_generation_id=source.stream_generation_id,
                state="baseline_published",
                registry_fingerprint=source.registry_fingerprint,
                writer_epoch=source.writer_epoch,
                writer_lease_id=source.writer_lease_id,
                write_gate_id=source.write_gate_id,
                source_generation=source.source_generation,
                snapshot_id=source.snapshot_id,
                alembic_revision=source.alembic_revision,
                snapshot_manifest_object_key=source.manifest_object_key,
                snapshot_manifest_object_version_id=source.manifest_object_version_id,
                snapshot_manifest_ciphertext_sha256=source.manifest_ciphertext_sha256,
                snapshot_manifest_ciphertext_bytes=source.manifest_ciphertext_bytes,
                database_sha256=source.database_sha256,
                uploads_sha256=source.uploads_sha256,
                baseline_manifest_object_key="campaigns/wa-ir/baseline/manifest.json.age",
                baseline_manifest_object_version_id="baseline-version-20260731-01",
                baseline_manifest_ciphertext_sha256="f" * 64,
                baseline_manifest_ciphertext_bytes=2048,
            ),
            baseline_manifest=manifest,
            source_signer=source_signer,
        ),
        expected_source_public_key=source_public_key,
        baseline=baseline,
    )
    return admit_object_delta_receiver_genesis(
        baseline=baseline,
        restore_evidence=restore_evidence,
        cutover=cutover,
        authorization=authorization,
    )


class _Transaction:
    contract_name = AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT

    def __init__(self, *, trace: list[str], plans: LockedAuthorizedObjectDeltaReceiverPlans, fail_on: str | None = None):
        self.trace = trace
        self.plans = plans
        self.fail_on = fail_on
        self.loaded_authorizations: list[AuthorizedObjectDeltaReceiverDelivery] = []

    async def load_locked_authorized_object_delta_receiver_plans(self, *, authorization, observed_at):
        self.trace.append("load")
        self.loaded_authorizations.append(authorization)
        self.assert_observed_at = observed_at
        if self.fail_on == "load":
            raise RuntimeError("load failed")
        return self.plans

    async def apply_db_change(self, change: PlannedObjectDeltaChange) -> None:
        self.trace.append(f"apply:{change.logical_sequence}")
        if self.fail_on == f"apply:{change.logical_sequence}":
            raise RuntimeError("apply failed")

    async def insert_immutable_receipt(self, receipt) -> None:
        self.trace.append("receipt")
        if self.fail_on == "receipt":
            raise RuntimeError("receipt failed")

    async def consume_delivery_nonce(self, receipt) -> None:
        self.trace.append("nonce")
        if self.fail_on == "nonce":
            raise RuntimeError("nonce failed")

    async def write_receiver_cursor(self, cursor) -> None:
        self.trace.append("cursor")
        if self.fail_on == "cursor":
            raise RuntimeError("cursor failed")

    async def commit(self) -> None:
        self.trace.append("commit")
        if self.fail_on == "commit":
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.trace.append("rollback")


class _Adapter:
    contract_name = AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT

    def __init__(self, *, trace: list[str], transaction: _Transaction):
        self.trace = trace
        self.transaction = transaction
        self.begin_count = 0

    async def begin_authorized_object_delta_receiver_transaction(self, *, authorization):
        self.trace.append("begin")
        self.begin_count += 1
        self.begin_authorization = authorization
        return self.transaction


class AuthorizedObjectDeltaReceiverTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_import_consumes_nonce_after_receipt_then_writes_cursor_and_commits(self):
        authorization = _authorization()
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_apply_import_plan(authorization),
            nonce_plan=_nonce_plan(authorization, existing=False),
        )
        trace: list[str] = []
        transaction = _Transaction(trace=trace, plans=plans)
        adapter = _Adapter(trace=trace, transaction=transaction)

        result = await coordinate_authorized_object_delta_receiver_transaction(
            authorization=authorization,
            observed_at=OBSERVED_AT,
            adapter=adapter,
        )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY, result.action)
        self.assertEqual(IMPORT_ACTION_APPLY, result.import_action)
        self.assertEqual("consume", result.nonce_action)
        self.assertEqual(2, result.changes_applied)
        self.assertEqual(
            ["begin", "load", "apply:2", "apply:3", "receipt", "nonce", "cursor", "commit"],
            trace,
        )
        self.assertEqual([authorization], transaction.loaded_authorizations)
        self.assertEqual(OBSERVED_AT, transaction.assert_observed_at)
        self.assertEqual(authorization, adapter.begin_authorization)

    async def test_exact_replay_is_zero_write_and_rolls_back_the_locked_transaction(self):
        authorization = _authorization()
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        trace: list[str] = []
        adapter = _Adapter(
            trace=trace,
            transaction=_Transaction(trace=trace, plans=plans),
        )

        result = await coordinate_authorized_object_delta_receiver_transaction(
            authorization=authorization,
            observed_at=OBSERVED_AT,
            adapter=adapter,
        )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY, result.action)
        self.assertEqual(IMPORT_ACTION_REPLAY, result.import_action)
        self.assertEqual("replay", result.nonce_action)
        self.assertEqual(0, result.changes_applied)
        self.assertEqual(1, adapter.begin_count)
        self.assertEqual(["begin", "load", "rollback"], trace)

    async def test_existing_import_with_new_nonce_consumes_only_nonce_atomically(self):
        authorization = _authorization()
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=False),
        )
        trace: list[str] = []
        transaction = _Transaction(trace=trace, plans=plans)
        adapter = _Adapter(trace=trace, transaction=transaction)

        result = await coordinate_authorized_object_delta_receiver_transaction(
            authorization=authorization,
            observed_at=OBSERVED_AT,
            adapter=adapter,
        )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_NONCE_ONLY, result.action)
        self.assertEqual(IMPORT_ACTION_REPLAY, result.import_action)
        self.assertEqual("consume", result.nonce_action)
        self.assertEqual(0, result.changes_applied)
        self.assertEqual(["begin", "load", "nonce", "commit"], trace)

    async def test_replayed_nonce_with_fresh_import_fails_closed_after_locked_load(self):
        authorization = _authorization()
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_apply_import_plan(authorization),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        trace: list[str] = []
        adapter = _Adapter(trace=trace, transaction=_Transaction(trace=trace, plans=plans))

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "already consumed"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual(["begin", "load", "rollback"], trace)

    async def test_later_failure_rolls_back_nonce_and_receipt_with_all_prior_writes(self):
        authorization = _authorization()
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_apply_import_plan(authorization),
            nonce_plan=_nonce_plan(authorization, existing=False),
        )
        trace: list[str] = []
        adapter = _Adapter(
            trace=trace,
            transaction=_Transaction(trace=trace, plans=plans, fail_on="cursor"),
        )

        with self.assertRaisesRegex(RuntimeError, "cursor failed"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual(
            ["begin", "load", "apply:2", "apply:3", "receipt", "nonce", "cursor", "rollback"],
            trace,
        )

    async def test_direct_or_replaced_receiver_change_cannot_cross_the_locked_plan_boundary(self):
        authorization = _authorization()
        valid_plan = _apply_import_plan(authorization)
        forged_change = replace(valid_plan.changes_to_apply[0])
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=replace(
                valid_plan,
                changes_to_apply=(forged_change, valid_plan.changes_to_apply[1]),
            ),
            nonce_plan=_nonce_plan(authorization, existing=False),
        )
        trace: list[str] = []
        adapter = _Adapter(
            trace=trace,
            transaction=_Transaction(trace=trace, plans=plans),
        )

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "unauthorized receiver handler"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual(["begin", "load", "rollback"], trace)

    async def test_expired_packet_is_rejected_before_the_transaction_opens(self):
        authorization = _authorization()
        trace: list[str] = []
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        adapter = _Adapter(trace=trace, transaction=_Transaction(trace=trace, plans=plans))

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "currently valid"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=EXPIRES_AT,
                adapter=adapter,
            )

        self.assertEqual([], trace)

    async def test_manually_forged_authority_is_rejected_before_the_transaction_opens(self):
        authorization = replace(_authorization(), transport_binding=object())
        trace: list[str] = []
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(_authorization(), existing=True),
        )
        adapter = _Adapter(trace=trace, transaction=_Transaction(trace=trace, plans=plans))

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "currently valid batch"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual([], trace)

    async def test_unsigned_or_wrong_source_attestation_is_rejected_before_authority_exists(self):
        def tamper_signature(value):
            mutated = dict(value)
            signature = dict(mutated["source_signature"])
            encoded = signature["signature_base64"]
            signature["signature_base64"] = (
                ("A" if encoded[0] != "A" else "B") + encoded[1:]
            )
            mutated["source_signature"] = signature
            return mutated

        with self.assertRaisesRegex(ObjectDeltaReceiverApplyScopeError, "local receiver authority"):
            _authorization(mutate_source_attestation=tamper_signature)

        authorization = _authorization()
        forged = replace(
            authorization,
            source_attestation=replace(
                authorization.source_attestation,
                source_key_id="ed25519-sha256:" + "d" * 64,
            ),
        )
        trace: list[str] = []
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        adapter = _Adapter(trace=trace, transaction=_Transaction(trace=trace, plans=plans))
        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "currently valid batch"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=forged,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )
        self.assertEqual([], trace)

    async def test_authorization_rejects_a_controller_key_not_pinned_to_the_packet(self):
        other_public_key = Ed25519PrivateKey.from_private_bytes(
            bytes(range(2, 34))
        ).public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        with self.assertRaisesRegex(ObjectDeltaReceiverApplyScopeError, "local receiver authority"):
            _authorization(controller_public_key_override=other_public_key)

    async def test_authority_with_a_mismatched_controller_public_key_is_rejected_before_begin(self):
        authorization = _authorization()
        other_public_key = Ed25519PrivateKey.from_private_bytes(
            bytes(range(2, 34))
        ).public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        forged = replace(
            authorization,
            binding=replace(authorization.binding, controller_public_key=other_public_key),
        )
        trace: list[str] = []
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        adapter = _Adapter(trace=trace, transaction=_Transaction(trace=trace, plans=plans))

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "currently valid batch"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=forged,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual([], trace)

    async def test_sequence_one_omission_or_forged_admission_never_begins_a_transaction(self):
        authorization = _authorization(first_sequence=1)
        valid_admission = _genesis_admission(authorization)
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        for label, admission in (
            ("omitted", None),
            ("forged", replace(valid_admission)),
        ):
            with self.subTest(label=label):
                trace: list[str] = []
                adapter = _Adapter(
                    trace=trace,
                    transaction=_Transaction(trace=trace, plans=plans),
                )
                with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "genesis admission"):
                    await coordinate_authorized_object_delta_receiver_transaction(
                        authorization=authorization,
                        observed_at=OBSERVED_AT,
                        adapter=adapter,
                        genesis_admission=admission,
                    )
                self.assertEqual(0, adapter.begin_count)
                self.assertEqual([], trace)

    async def test_sequence_one_mismatched_admission_never_begins_a_transaction(self):
        admitted_authorization = _authorization(first_sequence=1)
        other_authorization = _authorization(first_sequence=1)
        admission = _genesis_admission(admitted_authorization)
        trace: list[str] = []
        adapter = _Adapter(
            trace=trace,
            transaction=_Transaction(
                trace=trace,
                plans=LockedAuthorizedObjectDeltaReceiverPlans(
                    import_plan=_replay_import_plan(),
                    nonce_plan=_nonce_plan(other_authorization, existing=True),
                ),
            ),
        )

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "genesis admission"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=other_authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
                genesis_admission=admission,
            )

        self.assertEqual(0, adapter.begin_count)
        self.assertEqual([], trace)

    async def test_valid_sequence_one_admission_enters_the_locked_transaction(self):
        authorization = _authorization(first_sequence=1)
        admission = _genesis_admission(authorization)
        plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=_replay_import_plan(),
            nonce_plan=_nonce_plan(authorization, existing=True),
        )
        trace: list[str] = []
        adapter = _Adapter(
            trace=trace,
            transaction=_Transaction(trace=trace, plans=plans),
        )

        result = await coordinate_authorized_object_delta_receiver_transaction(
            authorization=authorization,
            observed_at=OBSERVED_AT,
            adapter=adapter,
            genesis_admission=admission,
        )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY, result.action)
        self.assertEqual(1, adapter.begin_count)
        self.assertEqual(["begin", "load", "rollback"], trace)

    async def test_non_genesis_delivery_rejects_an_unrelated_genesis_admission_before_begin(self):
        genesis_authorization = _authorization(first_sequence=1)
        admission = _genesis_admission(genesis_authorization)
        authorization = _authorization(first_sequence=2)
        trace: list[str] = []
        adapter = _Adapter(
            trace=trace,
            transaction=_Transaction(
                trace=trace,
                plans=LockedAuthorizedObjectDeltaReceiverPlans(
                    import_plan=_replay_import_plan(),
                    nonce_plan=_nonce_plan(authorization, existing=True),
                ),
            ),
        )

        with self.assertRaisesRegex(AuthorizedObjectDeltaReceiverTransactionError, "non-genesis"):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
                genesis_admission=admission,
            )

        self.assertEqual(0, adapter.begin_count)
        self.assertEqual([], trace)


class AuthorizedObjectDeltaReceiverTransactionStaticTests(unittest.TestCase):
    def test_mutable_plans_cannot_be_supplied_before_the_transaction_opens(self):
        parameters = inspect.signature(
            coordinate_authorized_object_delta_receiver_transaction
        ).parameters
        self.assertEqual(
            {"authorization", "observed_at", "adapter", "genesis_admission"},
            set(parameters),
        )

    def test_module_is_default_off_and_has_no_live_transport_or_database_dependencies(self):
        source = (
            Path(__file__).parents[1] / "core/authorized_object_delta_receiver_transaction.py"
        ).read_text(encoding="utf-8")
        for prohibited_import in (
            "import sqlalchemy",
            "from sqlalchemy",
            "import requests",
            "import httpx",
            "import aiohttp",
            "import boto",
            "import redis",
            "import aiogram",
            "from core import db",
        ):
            self.assertNotIn(prohibited_import, source)
        self.assertIn("accept no caller-supplied mutable import or nonce plan", source)


if __name__ == "__main__":
    unittest.main()
