from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.dialects import postgresql

from core.append_only_sync_delta_batch import (
    IMMUTABLE_RECEIPT_SCHEMA,
    build_delta_batch,
    canonical_json_bytes,
    sha256_bytes,
    validate_delta_batch,
)
from core.append_only_sync_delta_payload import build_object_delta_payload
from core.authorized_object_delta_receiver_transaction import (
    AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY,
    AUTHORIZED_RECEIVER_TRANSACTION_ACTION_NONCE_ONLY,
    AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY,
    coordinate_authorized_object_delta_receiver_transaction,
)
from core.object_delta_delivery_control_packet import (
    ObjectDeltaReceiverDeliveryPermit,
    build_unsigned_object_delta_delivery_control_packet,
    controller_key_id_from_public_key,
    sign_object_delta_delivery_control_packet,
    verify_object_delta_delivery_control_packet,
)
from core.object_delta_import_plan import expected_import_receipt
from core.object_delta_receiver_apply_scope import ObjectDeltaReceiverApplyScopeError
from core.object_delta_receiver_delivery_binding import ObjectDeltaReceiverDeliveryBinding
from core.object_delta_receiver_delivery_nonce import (
    expected_object_delta_receiver_delivery_nonce_receipt,
)
from core.object_delta_receiver_payload_admission import (
    AuthorizedObjectDeltaReceiverPayload,
    authorize_object_delta_receiver_payload,
)
from core.object_delta_source_batch_attestation import (
    build_object_delta_source_batch_attestation,
    source_key_id_from_public_key,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
)
from core.sqlalchemy_authorized_object_delta_receiver_transaction import (
    SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter,
    SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError,
)
import core.sqlalchemy_authorized_object_delta_receiver_transaction as sqlalchemy_adapter
from core.sync_metadata import build_sync_metadata, build_sync_public_identity
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)
from models.object_delta import (
    ObjectDeltaImportReceipt as ImportReceiptModel,
    ObjectDeltaReceiverCursor as ReceiverCursorModel,
)
from models.object_delta_receiver_delivery import (
    ObjectDeltaReceiverDeliveryNonceReceipt as NonceReceiptModel,
)


CAMPAIGN = "wa-ir-sqlalchemy-receiver-adapter-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-sqlalchemy-receiver-stream-20260731"
FINGERPRINT = "0123456789abcdef"
ISSUED_AT = datetime(2026, 7, 31, 18, 0, 0, tzinfo=timezone.utc)
OBSERVED_AT = ISSUED_AT + timedelta(seconds=1)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=4)
CONTROLLER_PRIVATE_KEY = bytes(range(1, 33))
SOURCE_PRIVATE_KEY = bytes(range(33, 65))


def _controller_signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(CONTROLLER_PRIVATE_KEY)


def _controller_public_key() -> bytes:
    return _controller_signer().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _source_signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(SOURCE_PRIVATE_KEY)


def _source_public_key() -> bytes:
    return _source_signer().public_key().public_bytes(
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


def _raw_commodity_payload() -> bytes:
    data = {"id": 101, "name": "SQLAlchemy Adapter Commodity"}
    item = {
        "logical_sequence": 2,
        "type": "db_change",
        "operation": "INSERT",
        "table": "commodities",
        "id": 101,
        "data": data,
        "hash": sha256_bytes(canonical_json_bytes({"change_log_id": 41})),
        "timestamp": 1_785_000_000.0,
        "change_log_id": 41,
        "sync_protocol": {
            "protocol_version": SYNC_PROTOCOL_VERSION,
            "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
            "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
            "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
            "registry_version": SYNC_REGISTRY_VERSION,
            "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
            "registry_fingerprint": FINGERPRINT,
            "producer": {"server_mode": "iran"},
        },
        "sync_meta": build_sync_metadata(
            "commodities",
            101,
            "INSERT",
            data,
            change_log_id=41,
            source_server="iran",
        ),
    }
    public_identity = build_sync_public_identity("commodities", 101, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    return canonical_json_bytes(
        build_object_delta_payload(stream_generation_id=GENERATION, items=(item,))
    )


def _authorization_and_payload():
    raw = _raw_commodity_payload()
    policy = _policy()
    object_key = derive_object_delta_object_key(
        policy,
        source_site="webapp_ir",
        destination_site="webapp_fi",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=2,
        last_sequence=2,
        payload_sha256=sha256_bytes(raw),
    )
    batch = validate_delta_batch(
        build_delta_batch(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=7,
            writer_lease_id="writer-lease-7",
            stream_generation_id=GENERATION,
            stream_sequence_ids=(2,),
            payload=raw,
            prior_chain_sha256="e" * 64,
            immutable_receipt={
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": "read_back_verified",
                "object_kind": "sync_delta_batch",
                "object_key": object_key,
                "version_id": "version-20260731-sqlalchemy-adapter-02",
                "ciphertext_sha256": "d" * 64,
                "ciphertext_bytes": 1024,
            },
        )
    )
    controller_public_key = _controller_public_key()
    packet = verify_object_delta_delivery_control_packet(
        sign_object_delta_delivery_control_packet(
            build_unsigned_object_delta_delivery_control_packet(
                policy=policy,
                batch=batch,
                binding=bind_object_delta_batch(policy=policy, batch=batch),
                issued_at=ISSUED_AT,
                expires_at=EXPIRES_AT,
                nonce="b" * 64,
                controller_public_key=controller_public_key,
            ),
            controller_signer=_controller_signer(),
        ),
        policy=policy,
        expected_destination_site="webapp_fi",
        pinned_controller_public_key=controller_public_key,
        observed_at=OBSERVED_AT,
    )
    source_public_key = _source_public_key()
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
            controller_key_id=controller_key_id_from_public_key(controller_public_key),
            writer_epoch=batch.writer_term.epoch,
            writer_lease_id=batch.writer_term.lease_id,
        ),
        source_public_key=source_public_key,
        source_key_id=source_key_id_from_public_key(source_public_key),
        controller_public_key=controller_public_key,
        expected_registry_fingerprint=FINGERPRINT,
    )
    from core.object_delta_receiver_apply_scope import authorize_object_delta_receiver_delivery

    authorization = authorize_object_delta_receiver_delivery(
        binding=binding,
        verified_packet=packet,
        batch=batch,
        source_attestation=build_object_delta_source_batch_attestation(
            batch=batch,
            transport_policy=policy,
            transport_binding=bind_object_delta_batch(policy=policy, batch=batch),
            source_signer=_source_signer(),
        ),
    )
    return authorization, authorize_object_delta_receiver_payload(
        authorization=authorization,
        raw_payload=raw,
    )


def _cursor_model(authorization, *, terminal: bool = False) -> ReceiverCursorModel:
    batch = authorization.batch
    return ReceiverCursorModel(
        id=801,
        source_site=batch.source_site,
        destination_site=batch.destination_site,
        campaign_id=batch.campaign_id,
        release_sha=batch.release_sha,
        stream_generation_id=batch.stream.generation_id,
        last_sequence=batch.stream.last_sequence if terminal else 1,
        last_batch_sha256=batch.batch_sha256 if terminal else "e" * 64,
    )


def _import_receipt_model(authorization) -> ImportReceiptModel:
    receipt = expected_import_receipt(authorization.batch)
    return ImportReceiptModel(
        id=802,
        source_site=receipt.source_site,
        destination_site=receipt.destination_site,
        campaign_id=receipt.campaign_id,
        release_sha=receipt.release_sha,
        stream_generation_id=receipt.stream_generation_id,
        first_sequence=receipt.first_sequence,
        last_sequence=receipt.last_sequence,
        writer_epoch=receipt.writer_epoch,
        writer_lease_id=receipt.writer_lease_id,
        prior_chain_sha256=receipt.prior_chain_sha256,
        batch_sha256=receipt.batch_sha256,
        payload_sha256=receipt.payload_sha256,
        object_key=receipt.object_key,
        object_version_id=receipt.object_version_id,
        ciphertext_sha256=receipt.ciphertext_sha256,
        ciphertext_bytes=receipt.ciphertext_bytes,
    )


def _nonce_receipt_model(authorization) -> NonceReceiptModel:
    receipt = expected_object_delta_receiver_delivery_nonce_receipt(
        packet=authorization.verified_packet,
        batch=authorization.batch,
        observed_at=OBSERVED_AT,
    )
    return NonceReceiptModel(
        id=803,
        controller_key_id=receipt.controller_key_id,
        nonce=receipt.nonce,
        packet_claim_sha256=receipt.packet_claim_sha256,
        bucket=receipt.bucket,
        source_site=receipt.source_site,
        destination_site=receipt.destination_site,
        destination_age_recipient=receipt.destination_age_recipient,
        campaign_id=receipt.campaign_id,
        release_sha=receipt.release_sha,
        stream_generation_id=receipt.stream_generation_id,
        writer_epoch=receipt.writer_epoch,
        writer_lease_id=receipt.writer_lease_id,
        first_sequence=receipt.first_sequence,
        last_sequence=receipt.last_sequence,
        batch_sha256=receipt.batch_sha256,
        object_key=receipt.object_key,
        object_version_id=receipt.object_version_id,
        expires_at=receipt.expires_at,
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _AsyncSessionDouble:
    """Captures the adapter's SQL and lifecycle without a database engine."""

    def __init__(
        self,
        *,
        cursor=None,
        object_receipt=None,
        stream_receipt=None,
        nonce=None,
        fail_commodity_insert: bool = False,
        active: bool = False,
        fail_connection: bool = False,
    ) -> None:
        self.info = {}
        self.active = active
        self.cursor = cursor
        self.object_receipts = [object_receipt, stream_receipt]
        self.nonce = nonce
        self.fail_commodity_insert = fail_commodity_insert
        self.fail_connection = fail_connection
        self.statements = []
        self.events: list[str] = []
        self.added = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.execution_options = None

    def in_transaction(self):
        return self.active

    async def connection(self, *, execution_options):
        self.events.append("connection")
        self.execution_options = execution_options
        if self.fail_connection:
            raise RuntimeError("injected scope connection failure")
        self.active = True
        return object()

    async def execute(self, statement):
        self.statements.append(statement)
        rendered = str(statement)
        if "pg_advisory_xact_lock" in rendered:
            self.events.append("lock")
            return _ScalarResult(None)
        if "FROM object_delta_receiver_cursors" in rendered:
            self.events.append("cursor_lock")
            return _ScalarResult(self.cursor)
        if "FROM object_delta_import_receipts" in rendered:
            self.events.append("receipt_lock")
            return _ScalarResult(self.object_receipts.pop(0))
        if "FROM object_delta_receiver_delivery_nonce_receipts" in rendered:
            self.events.append("nonce_lock")
            return _ScalarResult(self.nonce)
        if "INSERT INTO commodities" in rendered:
            self.events.append("commodity_insert")
            if self.fail_commodity_insert:
                raise RuntimeError("injected commodity insert failure")
            return _ScalarResult(None)
        raise AssertionError(f"unexpected SQL statement: {statement}")

    def add(self, value):
        self.added.append(value)
        if isinstance(value, ImportReceiptModel):
            self.events.append("add_import_receipt")
        elif isinstance(value, NonceReceiptModel):
            self.events.append("add_nonce_receipt")
        elif isinstance(value, ReceiverCursorModel):
            self.events.append("add_cursor")
        else:
            raise AssertionError(f"unexpected ORM model: {value!r}")

    async def flush(self):
        self.flush_count += 1
        self.events.append("flush")
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = 1000 + self.flush_count

    async def commit(self):
        self.events.append("commit")
        self.commit_count += 1
        self.active = False

    async def rollback(self):
        self.events.append("rollback")
        self.rollback_count += 1
        self.active = False

    async def close(self):
        self.events.append("close")
        self.close_count += 1


class _SessionFactory:
    def __init__(self, session: _AsyncSessionDouble) -> None:
        self.session = session
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.session


class SqlAlchemyAuthorizedReceiverTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_is_scope_bound_locks_before_planning_and_commits_exact_order(self):
        authorization, payload_admission = _authorization_and_payload()
        session = _AsyncSessionDouble(cursor=_cursor_model(authorization))
        factory = _SessionFactory(session)
        adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=factory,
            payload_admission=payload_admission,
        )

        original_import_planner = sqlalchemy_adapter.plan_authorized_object_delta_receiver_payload_import
        original_nonce_planner = sqlalchemy_adapter.plan_object_delta_receiver_delivery_nonce_consumption

        def import_planner(**kwargs):
            self.assertEqual(
                ["connection", "lock", "lock", "lock", "cursor_lock", "receipt_lock", "receipt_lock", "nonce_lock"],
                session.events,
            )
            session.events.append("import_plan")
            return original_import_planner(**kwargs)

        def nonce_planner(**kwargs):
            self.assertEqual("import_plan", session.events[-1])
            session.events.append("nonce_plan")
            return original_nonce_planner(**kwargs)

        with (
            mock.patch.object(
                sqlalchemy_adapter,
                "plan_authorized_object_delta_receiver_payload_import",
                side_effect=import_planner,
            ),
            mock.patch.object(
                sqlalchemy_adapter,
                "plan_object_delta_receiver_delivery_nonce_consumption",
                side_effect=nonce_planner,
            ),
        ):
            result = await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY, result.action)
        self.assertEqual(1, result.changes_applied)
        self.assertEqual(1, factory.calls)
        self.assertEqual(1, session.commit_count)
        self.assertEqual(0, session.rollback_count)
        self.assertEqual(1, session.close_count)
        self.assertEqual({}, session.info)
        self.assertEqual([ImportReceiptModel, NonceReceiptModel], [type(item) for item in session.added])
        self.assertLess(
            session.events.index("add_import_receipt"),
            session.events.index("add_nonce_receipt"),
        )
        self.assertLess(
            session.events.index("add_nonce_receipt"),
            session.events.index("commit"),
        )
        self.assertEqual(3, session.flush_count)

        rendered = [str(statement) for statement in session.statements]
        lock_indexes = [index for index, sql in enumerate(rendered) if "pg_advisory_xact_lock" in sql]
        self.assertEqual([0, 1, 2], lock_indexes[:3])
        self.assertIn("FOR UPDATE", rendered[3])
        self.assertIn("object_delta_receiver_cursors", rendered[3])
        self.assertIn("FOR UPDATE", rendered[4])
        self.assertIn("object_delta_import_receipts", rendered[4])
        self.assertIn("FOR UPDATE", rendered[5])
        self.assertIn("object_delta_import_receipts", rendered[5])
        self.assertIn("FOR UPDATE", rendered[6])
        self.assertIn("object_delta_receiver_delivery_nonce_receipts", rendered[6])

        commodity_statement = next(sql for sql in session.statements if "INSERT INTO commodities" in str(sql))
        compiled = str(
            commodity_statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("INSERT INTO commodities (name)", compiled)
        self.assertIn("ON CONFLICT (name) DO NOTHING", compiled)
        self.assertNotIn("UPDATE", compiled)
        self.assertNotIn(" id", compiled)

    async def test_exact_import_and_nonce_replay_is_zero_write_then_rollback_and_close(self):
        authorization, payload_admission = _authorization_and_payload()
        session = _AsyncSessionDouble(
            cursor=_cursor_model(authorization, terminal=True),
            object_receipt=_import_receipt_model(authorization),
            stream_receipt=_import_receipt_model(authorization),
            nonce=_nonce_receipt_model(authorization),
        )
        adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=_SessionFactory(session),
            payload_admission=payload_admission,
        )

        result = await coordinate_authorized_object_delta_receiver_transaction(
            authorization=authorization,
            observed_at=OBSERVED_AT,
            adapter=adapter,
        )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY, result.action)
        self.assertEqual([], session.added)
        self.assertEqual(0, session.flush_count)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(1, session.rollback_count)
        self.assertEqual(1, session.close_count)
        self.assertFalse([sql for sql in session.statements if "INSERT INTO commodities" in str(sql)])

    async def test_existing_import_with_fresh_nonce_commits_nonce_only_without_cursor_or_commodity_write(self):
        authorization, payload_admission = _authorization_and_payload()
        cursor = _cursor_model(authorization, terminal=True)
        session = _AsyncSessionDouble(
            cursor=cursor,
            object_receipt=_import_receipt_model(authorization),
            stream_receipt=_import_receipt_model(authorization),
            nonce=None,
        )
        adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=_SessionFactory(session),
            payload_admission=payload_admission,
        )

        result = await coordinate_authorized_object_delta_receiver_transaction(
            authorization=authorization,
            observed_at=OBSERVED_AT,
            adapter=adapter,
        )

        self.assertEqual(AUTHORIZED_RECEIVER_TRANSACTION_ACTION_NONCE_ONLY, result.action)
        self.assertEqual(0, result.changes_applied)
        self.assertEqual([NonceReceiptModel], [type(item) for item in session.added])
        self.assertEqual(1, session.flush_count)
        self.assertEqual(1, session.commit_count)
        self.assertEqual(0, session.rollback_count)
        self.assertEqual(1, session.close_count)
        self.assertEqual(authorization.batch.stream.last_sequence, cursor.last_sequence)
        self.assertFalse([sql for sql in session.statements if "INSERT INTO commodities" in str(sql)])

    async def test_apply_failure_rolls_back_scope_and_closes_factory_session(self):
        authorization, payload_admission = _authorization_and_payload()
        session = _AsyncSessionDouble(
            cursor=_cursor_model(authorization),
            fail_commodity_insert=True,
        )
        adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=_SessionFactory(session),
            payload_admission=payload_admission,
        )

        with self.assertRaisesRegex(
            SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError,
            "commodities insert-on-conflict",
        ):
            await coordinate_authorized_object_delta_receiver_transaction(
                authorization=authorization,
                observed_at=OBSERVED_AT,
                adapter=adapter,
            )

        self.assertEqual([], session.added)
        self.assertEqual(0, session.commit_count)
        self.assertEqual(1, session.rollback_count)
        self.assertEqual(1, session.close_count)
        self.assertEqual({}, session.info)

    async def test_factory_session_must_be_fresh_before_scope_entry(self):
        authorization, payload_admission = _authorization_and_payload()
        session = _AsyncSessionDouble(active=True)
        adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=_SessionFactory(session),
            payload_admission=payload_admission,
        )

        with self.assertRaises(ObjectDeltaReceiverApplyScopeError):
            await adapter.begin_authorized_object_delta_receiver_transaction(
                authorization=authorization,
            )

        self.assertEqual(1, session.close_count)
        self.assertEqual([], session.statements)

    async def test_scope_connection_failure_closes_factory_session_and_cleans_marker(self):
        authorization, payload_admission = _authorization_and_payload()
        session = _AsyncSessionDouble(fail_connection=True)
        adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=_SessionFactory(session),
            payload_admission=payload_admission,
        )

        with self.assertRaisesRegex(RuntimeError, "scope connection failure"):
            await adapter.begin_authorized_object_delta_receiver_transaction(
                authorization=authorization,
            )

        self.assertEqual(1, session.close_count)
        self.assertEqual({}, session.info)
        self.assertEqual([], session.statements)

    async def test_direct_replaced_or_stale_payload_capability_never_opens_a_session(self):
        authorization, payload_admission = _authorization_and_payload()
        direct = AuthorizedObjectDeltaReceiverPayload(
            authorization=payload_admission.authorization,
            payload=payload_admission.payload,
            registry_fingerprint=payload_admission.registry_fingerprint,
            payload_sha256=payload_admission.payload_sha256,
            payload_bytes=payload_admission.payload_bytes,
            payload_had_terminal_newline=payload_admission.payload_had_terminal_newline,
        )
        for forged in (direct, replace(payload_admission)):
            with self.subTest(forged=type(forged).__name__):
                factory = _SessionFactory(_AsyncSessionDouble())
                adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
                    session_factory=factory,
                    payload_admission=forged,
                )
                with self.assertRaisesRegex(
                    SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError,
                    "delivery or payload is invalid",
                ):
                    await adapter.begin_authorized_object_delta_receiver_transaction(
                        authorization=authorization,
                    )
                self.assertEqual(0, factory.calls)

        stale_authorization, _fresh_payload = _authorization_and_payload()
        stale_factory = _SessionFactory(_AsyncSessionDouble())
        stale_adapter = SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter(
            session_factory=stale_factory,
            payload_admission=payload_admission,
        )
        with self.assertRaisesRegex(
            SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError,
            "does not match the requested delivery",
        ):
            await stale_adapter.begin_authorized_object_delta_receiver_transaction(
                authorization=stale_authorization,
            )
        self.assertEqual(0, stale_factory.calls)

    def test_adapter_has_no_global_engine_or_transport_dependencies(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "core/sqlalchemy_authorized_object_delta_receiver_transaction.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "core.db",
            "get_db",
            "create_async_engine",
            "async_sessionmaker",
            "boto",
            "httpx",
            "requests",
            "aiohttp",
            "socket",
            "urllib",
            "subprocess",
            "api.routers",
        )
        self.assertFalse([name for name in forbidden if name in source])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
