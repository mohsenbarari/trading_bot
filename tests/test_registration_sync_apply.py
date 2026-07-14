import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from api.routers.sync import (
    _apply_item,
    _apply_user_counter_event,
    _apply_versioned_user_patch,
    _build_upsert_stmt,
    _localize_registration_user_reference,
)
from api.routers import sync as sync_router
from core.registration_sync_policy import REGISTRATION_USER_REFERENCES_FIELD
from core.user_counter_sync import user_counter_event_content_hash
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.invitation import Invitation
from models.user import User


class _ApplyDB:
    def __init__(self, *, rowcount=1, execute_results=None):
        self.rowcount = rowcount
        self.execute_results = list(execute_results or [])
        self.statements = []

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def execute(self, statement, *args, **kwargs):
        self.statements.append((statement, args, kwargs))
        if self.execute_results:
            result = self.execute_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return type("Result", (), {"rowcount": self.rowcount})()


class _ScalarCollection:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _QueryResult:
    def __init__(self, values=(), *, rowcount=1):
        self.values = list(values)
        self.rowcount = rowcount

    def scalars(self):
        return _ScalarCollection(self.values)

    def scalar_one_or_none(self):
        if len(self.values) > 1:
            raise RuntimeError("multiple rows")
        return self.values[0] if self.values else None


class RegistrationSyncApplyTests(unittest.IsolatedAsyncioTestCase):
    def test_result_helpers_and_identity_condition_boundaries(self):
        self.assertEqual(sync_router._result_scalars_all(object()), [])
        self.assertEqual(sync_router._result_first(object()), None)
        self.assertEqual(sync_router._result_scalar_one_or_none(object()), None)
        self.assertEqual(sync_router._user_sync_identity_conditions(None), [])
        self.assertEqual(
            sync_router._user_sync_identity_conditions(
                {"current": "invalid", "previous": {}}
            ),
            [],
        )

    async def test_registration_reference_policy_boundary_matrix(self):
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", False):
            self.assertTrue(
                await _localize_registration_user_reference(
                    _ApplyDB(), "invitations", {}
                )
            )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            self.assertTrue(
                await _localize_registration_user_reference(
                    _ApplyDB(), "unmanaged_table", {}
                )
            )
            self.assertFalse(
                await _localize_registration_user_reference(
                    _ApplyDB(), "invitations", {"created_by_id": 8}
                )
            )
            payload = {
                "created_by_id": None,
                "registered_user_id": 8,
                REGISTRATION_USER_REFERENCES_FIELD: {
                    "registered_user_id": "invalid"
                },
            }
            self.assertFalse(
                await _localize_registration_user_reference(
                    _ApplyDB(), "invitations", payload
                )
            )

    async def test_user_patch_resolution_and_noop_boundaries(self):
        for resolution, user_id, expected in (
            ("missing", None, "deferred"),
            ("identity", None, "error"),
        ):
            with self.subTest(resolution=resolution), patch.object(
                sync_router,
                "_resolve_user_sync_target",
                new=AsyncMock(return_value=(resolution, user_id)),
            ):
                self.assertEqual(
                    await _apply_versioned_user_patch(
                        _ApplyDB(),
                        record_id=7,
                        data={"id": 7, "address": "x"},
                        source_server="iran",
                    ),
                    expected,
                )

        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("legacy_id", 7)),
        ):
            self.assertEqual(
                await _apply_versioned_user_patch(
                    _ApplyDB(),
                    record_id=7,
                    data={"id": 7, "address": "ignored"},
                    source_server="foreign",
                ),
                "ignored",
            )

        for existing, expected in ((None, "deferred"), (SimpleNamespace(id=7), "ignored")):
            db = _ApplyDB(
                execute_results=[
                    _QueryResult(rowcount=0),
                    _QueryResult([] if existing is None else [existing]),
                ]
            )
            with self.subTest(expected=expected), patch.object(
                sync_router,
                "_resolve_user_sync_target",
                new=AsyncMock(return_value=("legacy_id", 7)),
            ):
                result = await _apply_versioned_user_patch(
                    db,
                    record_id=7,
                    data={"id": 7, "address": "x", "sync_version": 2},
                    source_server="iran",
                )
            self.assertEqual(result, expected)

    async def test_versioned_insert_and_legacy_resolution_boundaries(self):
        self.assertEqual(
            await sync_router._apply_versioned_user_insert(
                _ApplyDB(),
                record_id=7,
                data={"sync_version": 2},
                source_server="foreign",
            ),
            "error",
        )
        self.assertIsNone(
            await sync_router._apply_versioned_user_insert(
                _ApplyDB(),
                record_id=7,
                data={},
                source_server="iran",
            )
        )
        self.assertEqual(
            await sync_router._apply_versioned_user_insert(
                _ApplyDB(),
                record_id=7,
                data={"sync_version": 2},
                source_server="iran",
            ),
            "error",
        )
        identity = {"current": {"account_name": "stage9"}, "previous": {}}
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("conflict", None)),
        ):
            self.assertEqual(
                await sync_router._apply_versioned_user_insert(
                    _ApplyDB(),
                    record_id=7,
                    data={"sync_version": 2, "_sync_identity": identity},
                    source_server="iran",
                ),
                "error",
            )
        self.assertEqual(
            await sync_router._resolve_user_sync_target(
                _ApplyDB(), record_id="invalid", identity=None, lock=False
            ),
            ("missing", None),
        )

    @staticmethod
    def _counter_payload(**overrides):
        values = {
            "_counter_event_id": "11111111-2222-4333-8444-555555555555",
            "_counter_event_kind": "increment",
            "_counter_epoch": 1,
            "_counter_deltas": {"trades_count": 1},
            "_counter_occurred_at": "2026-07-12T12:00:00+00:00",
            "_sync_identity": {"current": {"account_name": "stage9"}, "previous": {}},
        }
        values.update(overrides)
        return values

    @staticmethod
    def _counter_user(**overrides):
        values = {
            "id": 91,
            "counter_epoch": 1,
            "trades_count": 0,
            "commodities_traded_count": 0,
            "channel_messages_count": 0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    async def test_counter_event_authority_resolution_and_validation_boundaries(self):
        self.assertEqual(
            await _apply_user_counter_event(
                _ApplyDB(),
                record_id=7,
                data=self._counter_payload(),
                source_server="unknown",
            ),
            "error",
        )
        self.assertEqual(
            await _apply_user_counter_event(
                _ApplyDB(),
                record_id=7,
                data=self._counter_payload(_counter_event_kind="reset"),
                source_server="foreign",
            ),
            "error",
        )

        for resolution, user_id, expected in (
            ("conflict", None, "error"),
            ("missing", None, "deferred"),
            ("identity", None, "deferred"),
        ):
            with self.subTest(resolution=resolution), patch.object(
                sync_router,
                "_resolve_user_sync_target",
                new=AsyncMock(return_value=(resolution, user_id)),
            ):
                result = await _apply_user_counter_event(
                    _ApplyDB(),
                    record_id=7,
                    data=self._counter_payload(),
                    source_server="iran",
                )
            self.assertEqual(result, expected)

        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    _ApplyDB(execute_results=[_QueryResult([])]),
                    record_id=7,
                    data=self._counter_payload(),
                    source_server="iran",
                ),
                "deferred",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    _ApplyDB(execute_results=[_QueryResult([self._counter_user()])]),
                    record_id=7,
                    data=self._counter_payload(_counter_event_kind="future"),
                    source_server="iran",
                ),
                "error",
            )

    async def test_counter_event_receipt_epoch_increment_and_reset_boundaries(self):
        user = self._counter_user()
        conflicting_receipt = SimpleNamespace(
            source_server="iran", user_id=91, event_hash="different"
        )
        db = _ApplyDB(
            execute_results=[_QueryResult([user]), _QueryResult([conflicting_receipt])]
        )
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    db,
                    record_id=7,
                    data=self._counter_payload(),
                    source_server="iran",
                ),
                "error",
            )

    async def test_counter_receipt_race_returns_existing_decision_for_increment_and_reset(self):
        matching = SimpleNamespace(
            source_server="iran",
            user_id=91,
            event_hash="stable-hash",
        )
        increment_db = _ApplyDB(
            execute_results=[
                _QueryResult([self._counter_user()]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([matching]),
            ]
        )
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ), patch.object(
            sync_router,
            "user_counter_event_content_hash",
            return_value="stable-hash",
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    increment_db,
                    record_id=7,
                    data=self._counter_payload(),
                    source_server="iran",
                ),
                "ignored",
            )

        pre_boundary_db = _ApplyDB(
            execute_results=[
                _QueryResult([self._counter_user(counter_epoch=2)]),
                _QueryResult([]),
                _QueryResult([(2, "2026-07-12T12:00:00+00:00")]),
                _QueryResult(["receipt-id"]),
            ]
        )
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    pre_boundary_db,
                    record_id=7,
                    data=self._counter_payload(
                        _counter_epoch=2,
                        _counter_occurred_at="2026-07-12T11:00:00+00:00",
                    ),
                    source_server="iran",
                ),
                "ignored",
            )

        reset_success_db = _ApplyDB(
            execute_results=[
                _QueryResult([self._counter_user()]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([
                    {"trades_count": 1},
                    {"commodities_traded_count": 2},
                ]),
                _QueryResult(["receipt-id"]),
                _QueryResult(rowcount=1),
            ]
        )
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    reset_success_db,
                    record_id=7,
                    data=self._counter_payload(
                        _counter_event_kind="reset", _counter_epoch=2
                    ),
                    source_server="iran",
                ),
                "ok",
            )

        reset_db = _ApplyDB(
            execute_results=[
                _QueryResult([self._counter_user()]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([matching]),
            ]
        )
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ), patch.object(
            sync_router,
            "user_counter_event_content_hash",
            return_value="stable-hash",
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    reset_db,
                    record_id=7,
                    data=self._counter_payload(
                        _counter_event_kind="reset", _counter_epoch=2
                    ),
                    source_server="iran",
                ),
                "ignored",
            )

    async def test_apply_item_routes_user_updates_and_defers_registration_references(self):
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True), patch.object(
            sync_router, "is_user_counter_event_payload", return_value=True
        ), patch.object(
            sync_router,
            "_apply_user_counter_event",
            new=AsyncMock(return_value="ignored"),
        ) as counter_apply:
            result = await _apply_item(
                _ApplyDB(),
                "users",
                "UPDATE",
                7,
                {"_counter_event_id": "event"},
                User,
                [],
                source_server="iran",
            )
        self.assertEqual(result, "ignored")
        counter_apply.assert_awaited_once()

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True), patch.object(
            sync_router, "is_user_counter_event_payload", return_value=False
        ), patch.object(
            sync_router,
            "_apply_versioned_user_patch",
            new=AsyncMock(return_value="ok"),
        ) as patch_apply:
            result = await _apply_item(
                _ApplyDB(),
                "users",
                "UPDATE",
                7,
                {"address": "x"},
                User,
                [],
                source_server="iran",
            )
        self.assertEqual(result, "ok")
        patch_apply.assert_awaited_once()

        with patch.object(
            sync_router,
            "_localize_registration_user_reference",
            new=AsyncMock(return_value=False),
        ):
            result = await _apply_item(
                _ApplyDB(),
                "invitations",
                "INSERT",
                8,
                {"token": "INV-deferred"},
                Invitation,
                [],
                source_server="iran",
            )
        self.assertEqual(result, "deferred")

    async def test_apply_item_ignores_stale_registration_upsert_and_natural_merge(self):
        stale_db = _ApplyDB(rowcount=0)
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True), patch.object(
            sync_router,
            "_localize_registration_user_reference",
            new=AsyncMock(return_value=True),
        ):
            result = await _apply_item(
                stale_db,
                "invitations",
                "INSERT",
                8,
                {"token": "INV-stale", "sync_version": 2},
                Invitation,
                [],
                source_server="iran",
            )
        self.assertEqual(result, "ignored")

        duplicate = IntegrityError(
            "insert invitations",
            {},
            RuntimeError("duplicate key value violates unique constraint"),
        )
        legacy_merge_db = _ApplyDB(
            execute_results=[duplicate, _QueryResult(rowcount=1)]
        )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", False):
            result = await _apply_item(
                legacy_merge_db,
                "invitations",
                "INSERT",
                8,
                {"token": "INV-legacy-merge", "account_name": "stage9"},
                Invitation,
                [],
                source_server="iran",
            )
        self.assertEqual(result, "ok")

        merge_db = _ApplyDB(
            execute_results=[duplicate, _QueryResult(rowcount=0)]
        )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True), patch.object(
            sync_router,
            "_localize_registration_user_reference",
            new=AsyncMock(return_value=True),
        ):
            result = await _apply_item(
                merge_db,
                "invitations",
                "INSERT",
                8,
                {"token": "INV-merge", "sync_version": 2},
                Invitation,
                [],
                source_server="iran",
            )
        self.assertEqual(result, "ignored")

    async def test_versioned_insert_missing_identity_and_id_returns_create_path(self):
        identity = {"current": {"account_name": "stage9"}, "previous": {}}
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("missing", None)),
        ):
            result = await sync_router._apply_versioned_user_insert(
                _ApplyDB(execute_results=[_QueryResult([])]),
                record_id=7,
                data={"sync_version": 2, "_sync_identity": identity},
                source_server="iran",
            )
        self.assertIsNone(result)
        user = self._counter_user()

        for current_epoch, latest, expected in (
            (1, (2, "2026-07-12T11:00:00+00:00"), "error"),
            (2, None, "error"),
        ):
            db = _ApplyDB(
                execute_results=[
                    _QueryResult([self._counter_user(counter_epoch=current_epoch)]),
                    _QueryResult([]),
                    _QueryResult([] if latest is None else [latest]),
                ]
            )
            with self.subTest(current_epoch=current_epoch), patch.object(
                sync_router,
                "_resolve_user_sync_target",
                new=AsyncMock(return_value=("identity", 91)),
            ):
                result = await _apply_user_counter_event(
                    db,
                    record_id=7,
                    data=self._counter_payload(_counter_epoch=current_epoch),
                    source_server="iran",
                )
            self.assertEqual(result, expected)

        for payload in (
            self._counter_payload(_counter_epoch=3),
            self._counter_payload(_counter_deltas={"trades_count": -1}),
        ):
            db = _ApplyDB(
                execute_results=[
                    _QueryResult([user]),
                    _QueryResult([]),
                    _QueryResult([]),
                ]
            )
            with patch.object(
                sync_router,
                "_resolve_user_sync_target",
                new=AsyncMock(return_value=("identity", 91)),
            ):
                result = await _apply_user_counter_event(
                    db, record_id=7, data=payload, source_server="iran"
                )
            self.assertIn(result, {"deferred", "error"})

        reset_cases = (
            (self._counter_user(counter_epoch=1), 1, None, "error"),
            (self._counter_user(counter_epoch=1), 3, None, "deferred"),
            (
                self._counter_user(counter_epoch=2),
                3,
                (2, "2026-07-12T12:00:00+00:00"),
                "error",
            ),
        )
        for reset_user, incoming_epoch, latest, expected in reset_cases:
            db = _ApplyDB(
                execute_results=[
                    _QueryResult([reset_user]),
                    _QueryResult([]),
                    _QueryResult([] if latest is None else [latest]),
                ]
            )
            with self.subTest(reset_epoch=incoming_epoch), patch.object(
                sync_router,
                "_resolve_user_sync_target",
                new=AsyncMock(return_value=("identity", 91)),
            ):
                result = await _apply_user_counter_event(
                    db,
                    record_id=7,
                    data=self._counter_payload(
                        _counter_event_kind="reset",
                        _counter_epoch=incoming_epoch,
                    ),
                    source_server="iran",
                )
            self.assertEqual(result, expected)

        overflow_db = _ApplyDB(
            execute_results=[
                _QueryResult([user]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([{"trades_count": sync_router.USER_COUNTER_MAX_VALUE + 1}]),
            ]
        )
        with patch.object(
            sync_router,
            "_resolve_user_sync_target",
            new=AsyncMock(return_value=("identity", 91)),
        ):
            self.assertEqual(
                await _apply_user_counter_event(
                    overflow_db,
                    record_id=7,
                    data=self._counter_payload(
                        _counter_event_kind="reset", _counter_epoch=2
                    ),
                    source_server="iran",
                ),
                "error",
            )

    @staticmethod
    def _identity(label: str) -> dict:
        return {
            "current": {
                "account_name": f"account-{label}",
                "mobile_number": f"0912{abs(hash(label)) % 10000000:07d}",
            },
            "previous": {},
        }

    async def test_registration_user_foreign_keys_are_localized_by_product_identity(self):
        local_user = SimpleNamespace(id=42)
        invitation_data = {
            "account_name": "canonical-user",
            "mobile_number": "09121112233",
            "created_by_id": 99,
            "registered_user_id": 100,
            REGISTRATION_USER_REFERENCES_FIELD: {
                "created_by_id": self._identity("creator"),
                "registered_user_id": self._identity("registered"),
            },
        }
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            resolved = await _localize_registration_user_reference(
                _ApplyDB(
                    execute_results=[
                        _QueryResult([SimpleNamespace(id=41)]),
                        _QueryResult([local_user]),
                    ]
                ),
                "invitations",
                invitation_data,
            )
        self.assertTrue(resolved)
        self.assertEqual(invitation_data["created_by_id"], 41)
        self.assertEqual(invitation_data["registered_user_id"], 42)

        relation_data = {
            "invitation_token": "CUST-localized",
            "owner_user_id": 98,
            "customer_user_id": 100,
            "created_by_user_id": 98,
            REGISTRATION_USER_REFERENCES_FIELD: {
                "owner_user_id": self._identity("owner"),
                "customer_user_id": self._identity("customer"),
                "created_by_user_id": self._identity("creator"),
            },
        }
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            resolved = await _localize_registration_user_reference(
                _ApplyDB(
                    execute_results=[
                        _QueryResult([SimpleNamespace(id=40)]),
                        _QueryResult([SimpleNamespace(id=42)]),
                        _QueryResult([SimpleNamespace(id=40)]),
                    ]
                ),
                "customer_relations",
                relation_data,
            )
        self.assertTrue(resolved)
        self.assertEqual(relation_data["owner_user_id"], 40)
        self.assertEqual(relation_data["customer_user_id"], 42)
        self.assertEqual(relation_data["created_by_user_id"], 40)

        accountant_data = {
            "owner_user_id": 98,
            "accountant_user_id": 101,
            "created_by_user_id": 98,
            REGISTRATION_USER_REFERENCES_FIELD: {
                "owner_user_id": self._identity("owner"),
                "accountant_user_id": self._identity("accountant"),
                "created_by_user_id": self._identity("creator"),
            },
        }
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            resolved = await _localize_registration_user_reference(
                _ApplyDB(
                    execute_results=[
                        _QueryResult([SimpleNamespace(id=40)]),
                        _QueryResult([SimpleNamespace(id=43)]),
                        _QueryResult([SimpleNamespace(id=40)]),
                    ]
                ),
                "accountant_relations",
                accountant_data,
            )
        self.assertTrue(resolved)
        self.assertEqual(accountant_data["owner_user_id"], 40)
        self.assertEqual(accountant_data["accountant_user_id"], 43)
        self.assertEqual(accountant_data["created_by_user_id"], 40)

        token_data = {
            "user_id": 100,
            REGISTRATION_USER_REFERENCES_FIELD: {
                "user_id": self._identity("linked"),
            },
        }
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            resolved = await _localize_registration_user_reference(
                _ApplyDB(execute_results=[_QueryResult([SimpleNamespace(id=42)])]),
                "telegram_link_tokens",
                token_data,
            )
        self.assertTrue(resolved)
        self.assertEqual(token_data["user_id"], 42)

    async def test_deleted_relation_preserves_existing_local_user_links_after_anonymization(self):
        existing_relation = SimpleNamespace(
            id=11,
            owner_user_id=401,
            customer_user_id=402,
            created_by_user_id=401,
        )
        stale_identity = {
            "current": {
                "account_name": "identity-before-delete",
                "mobile_number": "09120000000",
            },
            "previous": {},
        }
        relation_data = {
            "invitation_token": "CUST-delete-regression",
            "status": "deleted",
            "owner_user_id": 30,
            "customer_user_id": 33,
            "created_by_user_id": 30,
            REGISTRATION_USER_REFERENCES_FIELD: {
                "owner_user_id": stale_identity,
                "customer_user_id": stale_identity,
                "created_by_user_id": stale_identity,
            },
        }
        db = _ApplyDB(execute_results=[_QueryResult([existing_relation])])

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            resolved = await _localize_registration_user_reference(
                db,
                "customer_relations",
                relation_data,
            )

        self.assertTrue(resolved)
        self.assertEqual(relation_data["owner_user_id"], 401)
        self.assertEqual(relation_data["customer_user_id"], 402)
        self.assertEqual(relation_data["created_by_user_id"], 401)
        self.assertNotIn(REGISTRATION_USER_REFERENCES_FIELD, relation_data)
        self.assertEqual(len(db.statements), 1)

    async def test_registration_user_reference_defers_until_user_or_invitation_arrives(self):
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            invitation_ready = await _localize_registration_user_reference(
                _ApplyDB(execute_results=[_QueryResult([])]),
                "invitations",
                {
                    "account_name": "missing-user",
                    "mobile_number": "09121112233",
                    "created_by_id": 99,
                    "registered_user_id": 100,
                    REGISTRATION_USER_REFERENCES_FIELD: {
                        "created_by_id": self._identity("missing"),
                        "registered_user_id": self._identity("missing-registered"),
                    },
                },
            )
            relation_ready = await _localize_registration_user_reference(
                _ApplyDB(execute_results=[_QueryResult([])]),
                "customer_relations",
                {
                    "invitation_token": "CUST-missing",
                    "owner_user_id": 99,
                    "customer_user_id": 100,
                    "created_by_user_id": 99,
                    REGISTRATION_USER_REFERENCES_FIELD: {
                        "owner_user_id": self._identity("missing-owner"),
                        "customer_user_id": self._identity("missing-customer"),
                        "created_by_user_id": self._identity("missing-creator"),
                    },
                },
            )
        self.assertFalse(invitation_ready)
        self.assertFalse(relation_ready)

    def test_versioned_upserts_use_newer_only_guards_on_natural_keys(self):
        cases = [
            (
                User,
                "users",
                {"id": 7, "account_name": "user7", "sync_version": 3},
                "ON CONFLICT (id)",
            ),
            (
                Invitation,
                "invitations",
                {"id": 8, "token": "INV-test", "sync_version": 4},
                "ON CONFLICT (token)",
            ),
            (
                CustomerRelation,
                "customer_relations",
                {"id": 9, "invitation_token": "CUST-test", "sync_version": 5},
                "ON CONFLICT (invitation_token)",
            ),
            (
                AccountantRelation,
                "accountant_relations",
                {"id": 10, "invitation_token": "ACCT-test", "sync_version": 6},
                "ON CONFLICT (invitation_token)",
            ),
        ]

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            for model, table_name, data, conflict_fragment in cases:
                with self.subTest(table_name=table_name):
                    statement = _build_upsert_stmt(model, table_name, data)
                    sql = str(
                        statement.compile(
                            dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": False},
                        )
                    )
                    self.assertIn(conflict_fragment, sql)
                    self.assertIn(
                        f"WHERE {table_name}.sync_version < excluded.sync_version",
                        sql,
                    )
                    if table_name == "users":
                        data_with_last_seen = {**data, "last_seen_at": "2026-07-11T10:00:00"}
                        statement = _build_upsert_stmt(model, table_name, data_with_last_seen)
                        sql = str(statement.compile(dialect=postgresql.dialect()))
                        self.assertIn("greatest", sql.lower())

    async def test_user_natural_key_fallback_is_version_guarded_and_monotonic(self):
        duplicate = IntegrityError(
            "insert users",
            {},
            RuntimeError("duplicate key value violates unique constraint"),
        )
        applied = type("Result", (), {"rowcount": 1})()
        db = _ApplyDB(execute_results=[duplicate, applied])

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True), patch(
            "api.routers.sync._apply_versioned_user_insert",
            new=AsyncMock(return_value=None),
        ):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "telegram_id": 9988,
                    "account_name": "user7",
                    "mobile_number": "09120000000",
                    "sync_version": 4,
                    "last_seen_at": "2026-07-11T10:00:00",
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(len(db.statements), 2)
        fallback_sql = str(db.statements[1][0]).lower()
        self.assertIn("users.sync_version <", fallback_sql)
        self.assertIn("greatest", fallback_sql)

    async def test_user_insert_updates_natural_identity_match_with_different_id(self):
        local_user = SimpleNamespace(id=91, sync_version=2)
        db = _ApplyDB(
            execute_results=[
                _QueryResult([local_user]),
                _QueryResult([local_user]),
                _QueryResult(rowcount=1),
            ]
        )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "account_name": "same_user",
                    "mobile_number": "09120000000",
                    "full_name": "Same User",
                    "address": "Authoritative address",
                    "role": "عادی",
                    "sync_version": 4,
                    "_sync_identity": {
                        "current": {"account_name": "same_user"},
                        "previous": {},
                    },
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "ok")
        update_statement = db.statements[-1][0]
        compiled = update_statement.compile(dialect=postgresql.dialect())
        self.assertIn(91, compiled.params.values())

    async def test_user_insert_rejects_unrelated_local_numeric_id_collision(self):
        db = _ApplyDB(
            execute_results=[
                _QueryResult([]),
                _QueryResult([SimpleNamespace(id=7, account_name="different_user")]),
            ]
        )
        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "account_name": "source_user",
                    "mobile_number": "09120000000",
                    "sync_version": 4,
                    "_sync_identity": {
                        "current": {"account_name": "source_user"},
                        "previous": {},
                    },
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "error")
        self.assertEqual(len(db.statements), 2)

    async def test_unversioned_iran_user_insert_uses_legacy_compatibility_upsert(self):
        db = _ApplyDB()

        with patch("api.routers.sync.settings.registration_sync_v2_enabled", True):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                7,
                {
                    "id": 7,
                    "telegram_id": 9988,
                    "account_name": "legacy_user",
                    "mobile_number": "09120000000",
                },
                User,
                [],
                source_server="iran",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(len(db.statements), 1)
        self.assertIn("INSERT INTO users", str(db.statements[0][0]))

    async def test_iran_user_patch_is_version_guarded_and_last_seen_is_monotonic(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 4,
                "address": "authoritative address",
                "last_seen_at": "2026-07-11T10:00:00",
            },
            source_server="iran",
        )

        self.assertEqual(result, "ok")
        sql = str(db.statements[0][0])
        self.assertIn("users.sync_version <", sql)
        self.assertIn("greatest", sql.lower())
        self.assertIn("address", sql)

    async def test_versioned_user_patch_resolves_different_local_id_by_natural_identity(self):
        local_user = SimpleNamespace(id=91, sync_version=2)
        db = _ApplyDB(execute_results=[_QueryResult([local_user])])
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 4,
                "address": "authoritative address",
                "_sync_identity": {
                    "current": {"account_name": "same_user"},
                    "previous": {},
                },
            },
            source_server="iran",
        )

        self.assertEqual(result, "ok")
        update_statement = db.statements[1][0]
        compiled = update_statement.compile(dialect=postgresql.dialect())
        self.assertIn(91, compiled.params.values())
        self.assertNotIn("_sync_identity", str(update_statement))

    async def test_versioned_user_patch_rejects_split_identity_resolution(self):
        db = _ApplyDB(
            execute_results=[
                _QueryResult(
                    [
                        SimpleNamespace(id=91),
                        SimpleNamespace(id=92),
                    ]
                )
            ]
        )
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 4,
                "address": "must not apply",
                "_sync_identity": {
                    "current": {
                        "account_name": "first_user",
                        "mobile_number": "09120000000",
                    },
                    "previous": {},
                },
            },
            source_server="iran",
        )

        self.assertEqual(result, "error")
        self.assertEqual(len(db.statements), 1)

    async def test_unversioned_iran_patch_does_not_compare_shared_updated_at(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "address": "delayed but source-ordered address",
                "updated_at": "2026-07-11T09:00:00",
            },
            source_server="iran",
        )

        self.assertEqual(result, "ok")
        sql = str(db.statements[0][0]).lower()
        self.assertNotIn("users.updated_at <=", sql)
        self.assertNotIn("updated_at=now()", sql.replace(" ", "").lower())

    async def test_foreign_user_patch_is_monotonic_and_never_writes_identity_or_version(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={
                "id": 7,
                "sync_version": 99,
                "bot_onboarding_required_step": 2,
                "bot_onboarding_completed_step": 1,
                "last_seen_at": "2026-07-11T10:00:00",
                "address": "must be ignored",
            },
            source_server="foreign",
        )

        self.assertEqual(result, "ok")
        sql = str(db.statements[0][0])
        self.assertIn("greatest", sql.lower())
        self.assertIn("bot_onboarding_required_step", sql)
        self.assertIn("bot_onboarding_completed_step", sql)
        self.assertNotIn("address=", sql.replace(" ", ""))
        self.assertNotIn("sync_version=", sql.replace(" ", ""))
        self.assertIn("updated_at=users.updated_at", sql.replace(" ", "").lower())

    async def test_counter_event_applies_once_with_epoch_and_local_identity(self):
        user = SimpleNamespace(
            id=91,
            counter_epoch=1,
            trades_count=3,
            commodities_traded_count=4,
            channel_messages_count=5,
        )
        event_id = UUID("11111111-2222-4333-8444-555555555555")
        db = _ApplyDB(
            execute_results=[
                _QueryResult([user]),
                _QueryResult([user]),
                _QueryResult([]),
                _QueryResult([]),
                _QueryResult([event_id]),
                _QueryResult(rowcount=1),
            ]
        )
        result = await _apply_user_counter_event(
            db,
            record_id=7,
            data={
                "_counter_event_id": str(event_id),
                "_counter_event_kind": "increment",
                "_counter_epoch": 1,
                "_counter_deltas": {"trades_count": 1, "commodities_traded_count": 6},
                "_counter_occurred_at": "2026-07-11T12:00:00+00:00",
                "_sync_identity": {
                    "current": {"account_name": "same_user"},
                    "previous": {},
                },
            },
            source_server="foreign",
        )

        self.assertEqual(result, "ok")
        self.assertIn("FOR UPDATE", str(db.statements[0][0]))
        update_statement = db.statements[-1][0]
        sql = str(update_statement)
        self.assertNotIn("counter_epoch", sql)
        self.assertIn("trades_count", sql)
        self.assertIn("commodities_traded_count", sql)
        compiled = update_statement.compile(dialect=postgresql.dialect())
        self.assertIn(91, compiled.params.values())
        self.assertIn(4, compiled.params.values())

    async def test_duplicate_counter_event_does_not_update_user(self):
        user = SimpleNamespace(
            id=91,
            counter_epoch=3,
            trades_count=4,
            commodities_traded_count=10,
            channel_messages_count=5,
        )
        event_id = "11111111-2222-4333-8444-555555555555"
        event_hash = user_counter_event_content_hash(
            source_server="foreign",
            event_id=event_id,
            kind="increment",
            epoch=3,
            deltas={"trades_count": 1},
            occurred_at="2026-07-11T12:00:00+00:00",
        )
        db = _ApplyDB(
            execute_results=[
                _QueryResult([user]),
                _QueryResult([user]),
                _QueryResult(
                    [
                        SimpleNamespace(
                            source_server="foreign",
                            user_id=91,
                            event_hash=event_hash,
                        )
                    ]
                ),
            ]
        )
        result = await _apply_user_counter_event(
            db,
            record_id=7,
            data={
                "_counter_event_id": event_id,
                "_counter_event_kind": "increment",
                "_counter_epoch": 3,
                "_counter_deltas": {"trades_count": 1},
                "_counter_occurred_at": "2026-07-11T12:00:00+00:00",
                "_sync_identity": {
                    "current": {"account_name": "same_user"},
                    "previous": {},
                },
            },
            source_server="foreign",
        )

        self.assertEqual(result, "ignored")
        self.assertEqual(len(db.statements), 3)

    async def test_unknown_source_fails_without_database_write(self):
        db = _ApplyDB()
        result = await _apply_versioned_user_patch(
            db,
            record_id=7,
            data={"id": 7, "sync_version": 2, "address": "x"},
            source_server="unknown",
        )
        self.assertEqual(result, "error")
        self.assertEqual(db.statements, [])


if __name__ == "__main__":
    unittest.main()
