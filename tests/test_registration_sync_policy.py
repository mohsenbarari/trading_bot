import unittest
from types import SimpleNamespace

from core.registration_sync_policy import (
    REGISTRATION_VERSIONED_TABLES,
    USER_SYNC_COUNTER_FIELDS,
    allowed_user_fields_for_source,
    registration_sync_capabilities,
    sanitize_registration_sync_payload,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN


class RegistrationSyncPolicyTests(unittest.TestCase):
    @staticmethod
    def _identity():
        return {
            "current": {"account_name": "user7", "mobile_number": "09120000000"},
            "previous": {},
        }

    def test_versioned_table_set_is_exact(self):
        self.assertEqual(
            REGISTRATION_VERSIONED_TABLES,
            {"users", "invitations", "customer_relations", "accountant_relations"},
        )

    def test_policy_is_noop_while_feature_flag_is_off(self):
        payload = {"id": 7, "address": "Iran value", "unexpected": "legacy value"}
        decision = sanitize_registration_sync_payload(
            table="users",
            operation="UPDATE",
            data=payload,
            source_server=None,
            v2_enabled=False,
            accept_unversioned=False,
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.data, payload)
        self.assertEqual(decision.dropped_fields, ())

    def test_iran_user_patch_keeps_identity_and_drops_foreign_or_unknown_fields(self):
        payload = {
            "id": 7,
            "sync_version": "4",
            "address": "Iran address",
            "telegram_id": 9988,
            "bot_onboarding_completed_step": 2,
            "trades_count": 100,
            "unexpected": "drop",
            "_sync_identity": self._identity(),
        }
        decision = sanitize_registration_sync_payload(
            table="users",
            operation="UPDATE",
            data=payload,
            source_server=SERVER_IRAN,
            v2_enabled=True,
            accept_unversioned=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.data["sync_version"], 4)
        self.assertEqual(decision.data["address"], "Iran address")
        self.assertEqual(decision.data["telegram_id"], 9988)
        self.assertNotIn("bot_onboarding_completed_step", decision.data)
        self.assertNotIn("trades_count", decision.data)
        self.assertNotIn("unexpected", decision.data)
        self.assertEqual(
            set(decision.dropped_fields),
            {"bot_onboarding_completed_step", "trades_count", "unexpected"},
        )

    def test_foreign_user_patch_keeps_only_monotonic_onboarding_and_last_seen(self):
        payload = {
            "id": 7,
            "sync_version": 8,
            "bot_onboarding_required_step": 2,
            "bot_onboarding_completed_step": 1,
            "bot_onboarding_completed_at": "2026-07-11T10:00:00+00:00",
            "last_seen_at": "2026-07-11T10:01:00",
            "address": "must not apply",
            "telegram_id": 9988,
            "_sync_identity": self._identity(),
        }
        decision = sanitize_registration_sync_payload(
            table="users",
            operation="UPDATE",
            data=payload,
            source_server=SERVER_FOREIGN,
            v2_enabled=True,
            accept_unversioned=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(
            set(decision.data),
            {
                "id",
                "sync_version",
                "bot_onboarding_required_step",
                "bot_onboarding_completed_step",
                "bot_onboarding_completed_at",
                "last_seen_at",
                "_sync_identity",
            },
        )
        self.assertEqual(set(decision.dropped_fields), {"address", "telegram_id"})

    def test_versioned_user_patch_requires_natural_identity_metadata(self):
        decision = sanitize_registration_sync_payload(
            table="users",
            operation="UPDATE",
            data={"id": 7, "sync_version": 2, "address": "Iran address"},
            source_server=SERVER_IRAN,
            v2_enabled=True,
            accept_unversioned=True,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "versioned_user_identity_missing")

    def test_counter_event_is_explicit_unversioned_and_strict(self):
        valid = {
            "id": 7,
            "_sync_contract": "user_counter_event_v2",
            "_counter_event_id": "11111111-2222-4333-8444-555555555555",
            "_counter_event_kind": "increment",
            "_counter_epoch": 3,
            "_counter_deltas": {"trades_count": 1, "commodities_traded_count": 4},
            "_counter_occurred_at": "2026-07-11T12:00:00+00:00",
            "_sync_identity": self._identity(),
        }
        for source in (SERVER_IRAN, SERVER_FOREIGN):
            with self.subTest(source=source):
                decision = sanitize_registration_sync_payload(
                    table="users",
                    operation="UPDATE",
                    data=valid,
                    source_server=source,
                    v2_enabled=True,
                    accept_unversioned=False,
                )
                self.assertTrue(decision.accepted)
                self.assertEqual(decision.data["_counter_epoch"], 3)

        foreign_reset = sanitize_registration_sync_payload(
            table="users",
            operation="UPDATE",
            data={**valid, "_counter_event_kind": "reset", "_counter_deltas": {}},
            source_server=SERVER_FOREIGN,
            v2_enabled=True,
            accept_unversioned=True,
        )
        self.assertFalse(foreign_reset.accepted)
        self.assertEqual(foreign_reset.reason, "counter_reset_source_forbidden")

        malformed = [
            {**valid, "_counter_event_id": "not-a-uuid"},
            {**valid, "_counter_occurred_at": "not-a-time"},
            {**valid, "_counter_occurred_at": "2026-07-11T12:00:00"},
            {**valid, "_counter_event_kind": "increment", "_counter_deltas": {}},
            {**valid, "_counter_event_kind": "reset", "_counter_deltas": {"trades_count": 1}},
            {**valid, "_counter_deltas": {"unknown": 1}},
            {**valid, "_sync_identity": {}},
        ]
        for payload in malformed:
            with self.subTest(payload=payload):
                decision = sanitize_registration_sync_payload(
                    table="users",
                    operation="UPDATE",
                    data=payload,
                    source_server=SERVER_IRAN,
                    v2_enabled=True,
                    accept_unversioned=True,
                )
                self.assertFalse(decision.accepted)

    def test_foreign_user_insert_and_authoritative_table_mutation_are_rejected(self):
        user_insert = sanitize_registration_sync_payload(
            table="users",
            operation="INSERT",
            data={"id": 7, "sync_version": 1},
            source_server=SERVER_FOREIGN,
            v2_enabled=True,
            accept_unversioned=True,
        )
        self.assertFalse(user_insert.accepted)
        self.assertEqual(user_insert.reason, "source_authority_forbidden:foreign")

        for table_name in {"invitations", "customer_relations", "accountant_relations"}:
            with self.subTest(table_name=table_name):
                decision = sanitize_registration_sync_payload(
                    table=table_name,
                    operation="UPDATE",
                    data={"id": 1, "sync_version": 2},
                    source_server=SERVER_FOREIGN,
                    v2_enabled=True,
                    accept_unversioned=True,
                )
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.reason, "source_authority_forbidden:foreign")

    def test_missing_invalid_and_unversioned_events_fail_closed_as_configured(self):
        missing_source = sanitize_registration_sync_payload(
            table="users",
            operation="UPDATE",
            data={"id": 1, "sync_version": 2},
            source_server=None,
            v2_enabled=True,
            accept_unversioned=True,
        )
        self.assertFalse(missing_source.accepted)
        self.assertEqual(missing_source.reason, "missing_or_invalid_source_server")

        for version in (0, -1, "bad", [], {}):
            with self.subTest(version=version):
                decision = sanitize_registration_sync_payload(
                    table="invitations",
                    operation="UPDATE",
                    data={"id": 1, "sync_version": version},
                    source_server=SERVER_IRAN,
                    v2_enabled=True,
                    accept_unversioned=True,
                )
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.reason, "invalid_sync_version")

        compatible = sanitize_registration_sync_payload(
            table="invitations",
            operation="UPDATE",
            data={"id": 1},
            source_server=SERVER_IRAN,
            v2_enabled=True,
            accept_unversioned=True,
        )
        self.assertTrue(compatible.accepted)

        closed_gate = sanitize_registration_sync_payload(
            table="invitations",
            operation="UPDATE",
            data={"id": 1},
            source_server=SERVER_IRAN,
            v2_enabled=True,
            accept_unversioned=False,
        )
        self.assertFalse(closed_gate.accepted)
        self.assertEqual(closed_gate.reason, "unversioned_event_forbidden")

    def test_counter_fields_are_never_in_either_source_contract(self):
        self.assertTrue(USER_SYNC_COUNTER_FIELDS)
        self.assertTrue(USER_SYNC_COUNTER_FIELDS.isdisjoint(allowed_user_fields_for_source(SERVER_IRAN)))
        self.assertTrue(USER_SYNC_COUNTER_FIELDS.isdisjoint(allowed_user_fields_for_source(SERVER_FOREIGN)))

    def test_health_capabilities_expose_mixed_version_gate(self):
        capabilities = registration_sync_capabilities(
            SimpleNamespace(
                registration_sync_v2_enabled=False,
                registration_sync_accept_unversioned=True,
            )
        )
        self.assertEqual(
            capabilities,
            {
                "schema_version": 2,
                "versioned_events_supported": True,
                "v2_enabled": False,
                "accept_unversioned": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
