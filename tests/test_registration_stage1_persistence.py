import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import models  # noqa: F401 - register all metadata

from core.config import settings
from core.registration_contracts import TelegramRegistrationCommand, TelegramRegistrationOutcome
from core.registration_identity import (
    NORMALIZED_ACCOUNT_NAME_SQL,
    NORMALIZED_MOBILE_NUMBER_SQL,
)
from core.services.invitation_identity_reservation_service import (
    InvitationIdentityReservationConflict,
    acquire_invitation_identity_locks,
    find_identity_reservation,
    invitation_identity_lock_keys,
    normalize_invitation_identity,
    reserve_invitation_identity,
)
from core.services.registration_command_receipt_service import (
    RegistrationCommandReplayConflict,
    finalize_registration_command_receipt,
    prepare_registration_command_receipt,
    registration_command_lock_keys,
)
from core.sync_parity import synced_parity_table_names
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.sync_worker import SYNC_OUTBOUND_TABLE_PRIORITY
from models.database import Base
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.invitation import Invitation, InvitationKind
from models.telegram_registration_command_receipt import TelegramRegistrationCommandReceipt
from models.telegram_registration_intent import TelegramRegistrationIntentStatus
from models.user import User
from scripts.build_production_full_matrix_manifest import SYNC_TABLES
from scripts.inspect_shared_sync_state import SHARED_SYNC_TABLES
from scripts.run_production_backup import REGISTRATION_STAGE1_RESTORE_TABLES
from scripts.seed_shared_sync_tables import DEFAULT_TABLES


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_REGISTRATION_TABLES = {
    "invitation_identity_reservations",
    "telegram_registration_command_receipts",
    "telegram_registration_intents",
    "user_counter_event_receipts",
}


class _ScalarList:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _Result:
    def __init__(self, values=()):
        self.values = list(values)

    def scalars(self):
        return _ScalarList(self.values)


class _FakeDB:
    def __init__(self, results=()):
        self.results = list(results)
        self.execute_calls = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        if self.results:
            return self.results.pop(0)
        return _Result()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1


def _command(**overrides):
    verified = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    payload = {
        "command_id": "11111111-2222-4333-8444-555555555555",
        "idempotency_key": "telegram-registration:test-0001",
        "invitation_token": "INV-1234567890abcdef",
        "mobile_number": "09120000000",
        "telegram_id": 123456789,
        "address": "1234567890",
        "contact_verified_at": verified,
        "local_completed_at": verified + timedelta(seconds=1),
        "invitation_expires_at_snapshot": verified + timedelta(hours=1),
    }
    payload.update(overrides)
    return TelegramRegistrationCommand.model_validate(payload)


class RegistrationStage1PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def test_user_canonical_identity_is_generated_and_uniquely_indexed(self):
        account_column = User.__table__.c.normalized_account_name
        mobile_column = User.__table__.c.normalized_mobile_number
        self.assertIsNotNone(account_column.computed)
        self.assertIsNotNone(mobile_column.computed)
        self.assertEqual(str(account_column.computed.sqltext), NORMALIZED_ACCOUNT_NAME_SQL)
        self.assertEqual(str(mobile_column.computed.sqltext), NORMALIZED_MOBILE_NUMBER_SQL)
        unique_indexes = {
            index.name
            for index in User.__table__.indexes
            if index.unique
        }
        self.assertIn("ux_users_normalized_account_name", unique_indexes)
        self.assertIn("ux_users_normalized_mobile_number", unique_indexes)
        counter_receipt_indexes = {
            index.name
            for index in Base.metadata.tables["user_counter_event_receipts"].indexes
            if index.unique
        }
        self.assertIn(
            "ux_user_counter_event_receipts_user_reset_epoch",
            counter_receipt_indexes,
        )

    def test_new_models_enums_constraints_defaults_and_metadata_are_explicit(self):
        self.assertTrue(LOCAL_REGISTRATION_TABLES.issubset(Base.metadata.tables))
        self.assertEqual(
            Base.metadata.tables["invitations"].c.kind.type.enums,
            ["standard", "accountant", "customer", "legacy_unknown"],
        )
        self.assertEqual(
            Base.metadata.tables["telegram_registration_intents"].c.status.type.enums,
            [status.value for status in TelegramRegistrationIntentStatus],
        )

        expected_constraints = {
            "invitations": {
                "ck_invitations_sync_version_positive",
                "ck_invitations_completion_metadata_atomic",
                "ck_invitations_not_completed_and_revoked",
            },
            "invitation_identity_reservations": {
                "ck_invitation_identity_reservations_mobile_not_blank",
                "ck_invitation_identity_reservations_account_not_blank",
            },
            "telegram_registration_intents": {
                "ck_telegram_registration_intents_telegram_id_positive",
                "ck_telegram_registration_intents_retry_count_nonnegative",
            },
            "telegram_registration_command_receipts": {
                "ck_telegram_registration_receipts_request_hash",
                "ck_telegram_registration_receipts_token_hash",
                "ck_telegram_registration_receipts_source_foreign",
                "ck_telegram_registration_receipts_terminal_atomic",
                "ck_telegram_registration_receipts_user_outcome",
            },
            "user_counter_event_receipts": {
                "ck_user_counter_event_receipts_known_source",
                "ck_user_counter_event_receipts_event_hash",
                "ck_user_counter_event_receipts_known_kind",
                "ck_user_counter_event_receipts_epoch_positive",
                "ck_user_counter_event_receipts_known_outcome",
            },
        }
        for table_name, names in expected_constraints.items():
            actual = {
                constraint.name
                for constraint in Base.metadata.tables[table_name].constraints
                if constraint.name
            }
            with self.subTest(table_name=table_name):
                self.assertTrue(names.issubset(actual))

        for table_name in ("users", "invitations", "customer_relations", "accountant_relations"):
            column = Base.metadata.tables[table_name].c.sync_version
            self.assertFalse(column.nullable)
            self.assertEqual(str(column.server_default.arg), "1")

        for model in (User, Invitation, CustomerRelation, AccountantRelation):
            with self.subTest(versioned_model=model.__name__):
                self.assertIs(model.__mapper__.version_id_col, model.__table__.c.sync_version)
                self.assertFalse(model.__mapper__.version_id_generator)

    def test_stage1_feature_flags_default_off_and_sms_categories_remain_independent(self):
        for field_name in (
            "telegram_direct_registration_enabled",
            "telegram_registration_reconciliation_enabled",
            "telegram_login_otp_enabled",
            "otp_sms_auto_fallback_enabled",
            "invitation_contract_v2_enabled",
            "registration_sync_v2_enabled",
        ):
            with self.subTest(field_name=field_name):
                self.assertFalse(getattr(settings, field_name))

        self.assertFalse(settings.invitation_sms_standard_enabled)
        self.assertFalse(settings.invitation_sms_customer_tier1_enabled)
        self.assertTrue(settings.invitation_sms_accountant_enabled)
        self.assertTrue(settings.invitation_sms_customer_tier2_enabled)
        self.assertTrue(settings.registration_sync_accept_unversioned)

    def test_registry_and_tooling_keep_local_tables_out_of_generic_sync(self):
        for table_name in LOCAL_REGISTRATION_TABLES:
            with self.subTest(table_name=table_name):
                self.assertEqual(get_sync_registry_entry(table_name).policy, SyncPolicy.NO_SYNC)
                self.assertNotIn(table_name, synced_parity_table_names())
                self.assertNotIn(table_name, SYNC_OUTBOUND_TABLE_PRIORITY)
                self.assertNotIn(table_name, DEFAULT_TABLES)
                self.assertNotIn(table_name, SHARED_SYNC_TABLES)
                self.assertNotIn(table_name, SYNC_TABLES)
                self.assertIn(table_name, REGISTRATION_STAGE1_RESTORE_TABLES)

        for versioned_table in {
            "users",
            "invitations",
            "customer_relations",
            "accountant_relations",
        }:
            with self.subTest(versioned_table=versioned_table):
                self.assertEqual(get_sync_registry_entry(versioned_table).policy, SyncPolicy.SYNC)
                self.assertIn(versioned_table, synced_parity_table_names())
                self.assertIn(versioned_table, DEFAULT_TABLES)
                self.assertIn(versioned_table, SHARED_SYNC_TABLES)

        self.assertIn("invitations", SYNC_TABLES)

    def test_migration_is_additive_conservative_and_has_full_downgrade(self):
        source = (
            REPO_ROOT
            / "migrations/versions/a8d9e0f1b2c3_add_registration_stage1_foundation.py"
        ).read_text(encoding="utf-8")

        self.assertIn('down_revision: Union[str, Sequence[str], None] = "f7c8d9e0a1b2"', source)
        self.assertIn("legacy_unknown", source)
        self.assertIn("conflicting pending invitations", source)
        self.assertIn("translate(", source)
        self.assertIn("U&'\\06F0", source)
        self.assertNotIn("ROW_NUMBER()", source.upper())
        self.assertNotIn("ON CONFLICT DO NOTHING", source.upper())
        self.assertIn("Legacy standard invitations stay", source)
        self.assertIn("ar.accountant_user_id", source)
        self.assertIn("cr.customer_user_id", source)
        self.assertIn("ar.activated_at <= i.expires_at", source)
        self.assertIn("cr.activated_at <= i.expires_at", source)
        for table_name in LOCAL_REGISTRATION_TABLES:
            self.assertIn(f'op.create_table(\n        "{table_name}"', source)
            self.assertIn(f'op.drop_table("{table_name}")', source)

    def test_identity_normalization_and_lock_order_cover_persian_and_arabic_digits(self):
        identity = normalize_invitation_identity(
            mobile_number=" ۰۹۱۲٣٤٥٦٧٨٩ ",
            account_name=" User۱۲٣ ",
        )
        self.assertEqual(identity.mobile_number, "09123456789")
        self.assertEqual(identity.account_name, "user123")
        keys = invitation_identity_lock_keys(identity)
        self.assertEqual(keys, tuple(sorted(keys)))
        self.assertEqual(len(set(keys)), 2)
        self.assertNotIn(identity.mobile_number, " ".join(keys))
        self.assertNotIn(identity.account_name, " ".join(keys))
        self.assertTrue(all(key.startswith("registration-identity:") for key in keys))

    async def test_identity_lock_and_reservation_are_flush_only(self):
        identity = normalize_invitation_identity(
            mobile_number="09120000000",
            account_name="sample_user",
        )
        db = _FakeDB(results=[_Result(), _Result(), _Result()])
        await acquire_invitation_identity_locks(db, identity)
        reservation = await reserve_invitation_identity(
            db,
            invitation=SimpleNamespace(id=77),
            identity=identity,
        )

        self.assertEqual(len(db.execute_calls), 3)
        self.assertEqual(reservation.invitation_id, 77)
        self.assertEqual(db.added, [reservation])
        self.assertEqual(db.flush_count, 1)

    async def test_split_identity_reservation_fails_closed(self):
        identity = normalize_invitation_identity(
            mobile_number="09120000000",
            account_name="sample_user",
        )
        db = _FakeDB(
            results=[
                _Result(
                    [
                        SimpleNamespace(id=1, normalized_mobile=identity.mobile_number),
                        SimpleNamespace(id=2, normalized_account_name=identity.account_name),
                    ]
                )
            ]
        )
        with self.assertRaises(InvitationIdentityReservationConflict) as exc_info:
            await find_identity_reservation(db, identity)
        self.assertEqual(exc_info.exception.code, "identity_split_reserved")

    async def test_exact_same_invitation_reservation_retry_is_idempotent(self):
        identity = normalize_invitation_identity(
            mobile_number="09120000000",
            account_name="sample_user",
        )
        existing = SimpleNamespace(
            id=1,
            invitation_id=77,
            normalized_mobile=identity.mobile_number,
            normalized_account_name=identity.account_name,
        )
        db = _FakeDB(results=[_Result([existing])])
        result = await reserve_invitation_identity(
            db,
            invitation=SimpleNamespace(id=77),
            identity=identity,
        )
        self.assertIs(result, existing)
        self.assertEqual(db.added, [])
        self.assertEqual(db.flush_count, 0)

    def test_command_lock_order_is_deterministic(self):
        command = _command()
        keys = registration_command_lock_keys(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
        )
        self.assertEqual(keys, tuple(sorted(keys)))
        self.assertEqual(len(set(keys)), 2)
        joined = " ".join(keys)
        self.assertNotIn(str(command.command_id), joined)
        self.assertNotIn(command.idempotency_key, joined)
        self.assertTrue(all(key.startswith("telegram-registration:") for key in keys))

    async def test_command_receipt_replay_is_idempotent_and_changed_payload_rejects(self):
        command = _command()
        existing = TelegramRegistrationCommandReceipt(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            request_hash="unused",
            invitation_token_hash="a" * 64,
            source_server="foreign",
        )

        from core.registration_contracts import registration_command_hash

        existing.request_hash = registration_command_hash(command)
        replay_db = _FakeDB(results=[_Result(), _Result(), _Result([existing])])
        receipt, replayed = await prepare_registration_command_receipt(
            replay_db,
            command=command,
            source_server="foreign",
        )
        self.assertIs(receipt, existing)
        self.assertTrue(replayed)
        self.assertEqual(replay_db.added, [])

        changed = _command(address="changed address value")
        conflict_db = _FakeDB(results=[_Result(), _Result(), _Result([existing])])
        with self.assertRaises(RegistrationCommandReplayConflict) as exc_info:
            await prepare_registration_command_receipt(
                conflict_db,
                command=changed,
                source_server="foreign",
            )
        self.assertEqual(
            str(exc_info.exception),
            TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY.value,
        )

    async def test_command_receipt_rejects_non_foreign_source_before_db_access(self):
        db = _FakeDB()
        with self.assertRaises(RegistrationCommandReplayConflict) as exc_info:
            await prepare_registration_command_receipt(
                db,
                command=_command(),
                source_server="iran",
            )
        self.assertEqual(str(exc_info.exception), "source_server_forbidden")
        self.assertEqual(db.execute_calls, [])

    def test_finalize_receipt_sets_bounded_terminal_result_without_commit(self):
        receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)
        completed_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
        finalize_registration_command_receipt(
            receipt,
            outcome=TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=44,
            completed_at=completed_at,
        )
        self.assertEqual(receipt.outcome_code, "created")
        self.assertEqual(receipt.authoritative_user_id, 44)
        self.assertEqual(receipt.completed_at, completed_at)


if __name__ == "__main__":
    unittest.main()
