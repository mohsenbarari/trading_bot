"""Cross-feature database policy invariants for the integrated topology."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import unittest
from unittest.mock import patch

from migrations.versions.c431d2e3f5a6_reconcile_integrated_database_policy import (
    PROJECTION_FORBIDDEN_FIELDS,
    PROJECTION_TABLES,
    WRITER_FUNCTION_SQL,
)
from migrations.versions.e653f4a5b7c8_close_three_site_database_trust_gaps import (
    NONCE_CLEANUP_SQL,
)
from core.dr_event_receiver import _event_values
from core.dr_event_protocol import sha256_json, validate_envelope
from core.dr_database_roles import (
    PROJECTION_SERVICE_SCOPES,
    projection_database_role_suffix_for_service,
    projection_scope_for_service,
)
from core.sync_parity import synced_parity_table_names
import scripts.activate_three_site_database_fencing as webapp_database_policy
import scripts.provision_bot_database_roles as bot_database_policy
from scripts.activate_three_site_database_fencing import (
    BOT_LOCAL_EXECUTION_TABLES,
    CANONICAL_PROJECTION_FORBIDDEN_FIELDS,
    CANONICAL_PROJECTION_POLICY_SHA256,
    CANONICAL_PROJECTION_TABLES,
    CLEANUP_FUNCTION_CATALOG_IDENTITY,
    CLEANUP_FUNCTION_PROSRC_BYTES,
    CLEANUP_FUNCTION_PROSRC_SHA256,
    CONVERGENCE_OBSERVER_TABLES as WEBAPP_CONVERGENCE_OBSERVER_TABLES,
    DR_SERVICE_INTERNAL_GRANTS,
    PUBLIC_TRUSTED_LANGUAGE_REVOKE,
    SYNC_OBSERVER_TABLES as WEBAPP_SYNC_OBSERVER_TABLES,
    WRITER_FUNCTION_PROSRC_BYTES,
    WRITER_FUNCTION_PROSRC_SHA256,
    _assert_database_runtime_state,
    _assert_exact_cleanup_function,
    _assert_exact_database_authorization_closure,
    _assert_exact_runtime_database_scope,
    _assert_exact_role_closure,
    _assert_exact_grant_inventory,
    _assert_exact_projection_policy,
    _assert_exact_writer_trigger_policy,
    _assert_phase_statement_boundary as assert_webapp_phase_boundary,
    _expected_fenced_runtime_state,
    _projection_grants,
    _database_scope_statements,
    _unsafe_public_privilege_count,
    build_fence_statements as build_webapp_fence_statements,
    build_grant_statements as build_webapp_grant_statements,
)
from scripts.provision_bot_database_roles import (
    BOT_DR_SERVICE_GRANTS,
    BOT_LOCAL_QUEUE_APPLICATION_GRANTS,
    CONVERGENCE_OBSERVER_TABLES as BOT_CONVERGENCE_OBSERVER_TABLES,
    SYNC_OBSERVER_TABLES as BOT_SYNC_OBSERVER_TABLES,
    _build_fence_statements as build_bot_fence_statements,
    _build_role_grant_statements as build_bot_role_grant_statements,
)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def one(self):
        if len(self.rows) != 1:
            raise AssertionError("expected exactly one fake row")
        return self.rows[0]

    def one_or_none(self):
        if len(self.rows) > 1:
            raise AssertionError("expected at most one fake row")
        return self.rows[0] if self.rows else None


class _RuntimeConnection:
    def __init__(self, state):
        self.state = state

    def execute(self, statement, params=None):
        del statement, params
        return _Rows([self.state])


EXPECTED_BOT_LOCAL_EXECUTION_TABLES = frozenset(
    {
        "telegram_delivery_jobs",
        "telegram_delivery_provider_outcomes",
        "telegram_delivery_reconciliation_evidence",
        "telegram_delivery_runtime_gates",
        "telegram_delivery_resume_operations",
        "telegram_delivery_feeder_states",
        "telegram_scheduled_operations",
        "telegram_interaction_anchor_states",
        "telegram_channel_membership_sagas",
    }
)


class IntegrationDatabasePolicyTests(unittest.TestCase):
    @staticmethod
    def _writer_prosrc() -> str:
        return WRITER_FUNCTION_SQL.split(" AS $$", 1)[1].rsplit("$$", 1)[0]

    @staticmethod
    def _cleanup_prosrc() -> str:
        return NONCE_CLEANUP_SQL.split(" AS $$", 1)[1].rsplit(
            "$$;",
            1,
        )[0]

    def _cleanup_function_row(self) -> dict[str, object]:
        return {
            "function_schema": "public",
            "proname": "trading_bot_cleanup_expired_replay_nonces",
            "identity_arguments": (
                "cutoff timestamp with time zone, row_limit integer"
            ),
            "function_arguments": (
                "cutoff timestamp with time zone, row_limit integer"
            ),
            "function_result": "TABLE(key_id text, nonce text)",
            "input_argument_types": "timestamp with time zone, integer",
            "argument_names": "cutoff,row_limit,key_id,nonce",
            "argument_modes": "i,i,t,t",
            "all_argument_types": (
                "timestamp with time zone,integer,text,text"
            ),
            "function_owner": "migration_owner",
            "function_language": "plpgsql",
            "prokind": "f",
            "provolatile": "v",
            "proparallel": "u",
            "proleakproof": False,
            "proretset": True,
            "proisstrict": False,
            "pronargs": 2,
            "pronargdefaults": 0,
            "provariadic": 0,
            "return_schema": "pg_catalog",
            "return_type": "record",
            "prosecdef": True,
            "prosrc": self._cleanup_prosrc(),
            "config_count": 1,
            "first_config": "search_path=public, pg_temp",
        }

    def _writer_trigger_rows(self) -> list[dict[str, object]]:
        prosrc = self._writer_prosrc()
        return [
            {
                "relname": table_name,
                "tgenabled": "A",
                "tgtype": 31,
                "tgqual_is_null": True,
                "tgnargs": 0,
                "tgargs_hex": "",
                "function_oid": 12345,
                "function_schema": "public",
                "proname": "trading_bot_enforce_writer_term",
                "function_arguments": "",
                "function_owner": "migration_owner",
                "function_language": "plpgsql",
                "prokind": "f",
                "provolatile": "v",
                "proparallel": "u",
                "proleakproof": False,
                "proretset": False,
                "pronargs": 0,
                "return_schema": "pg_catalog",
                "return_type": "trigger",
                "prosecdef": True,
                "prosrc": prosrc,
                "config_count": 1,
                "first_config": "search_path=public, pg_temp",
            }
            for table_name in webapp_database_policy.EXPECTED_WRITER_TRIGGER_TABLES
        ]

    def test_writer_trigger_contract_is_always_and_release_body_bound(self):
        prosrc = self._writer_prosrc()
        self.assertEqual(len(prosrc.encode("utf-8")), WRITER_FUNCTION_PROSRC_BYTES)
        self.assertEqual(
            hashlib.sha256(prosrc.encode("utf-8")).hexdigest(),
            WRITER_FUNCTION_PROSRC_SHA256,
        )

        class Connection:
            def __init__(self, rows):
                self.rows = rows

            def scalar(self, statement, params=None):
                del statement, params
                return "migration_owner"

            def execute(self, statement, params=None):
                del statement, params
                return _Rows(self.rows)

        rows = self._writer_trigger_rows()
        self.assertEqual(
            _assert_exact_writer_trigger_policy(Connection(rows)),
            set(webapp_database_policy.EXPECTED_WRITER_TRIGGER_TABLES),
        )
        for key, unsafe in (
            ("tgenabled", "O"),
            ("tgqual_is_null", False),
            ("tgnargs", 1),
            ("function_owner", "foreign_owner"),
            ("function_language", "sql"),
            ("prosrc", prosrc + " "),
        ):
            with self.subTest(key=key):
                changed = [dict(row) for row in rows]
                changed[0][key] = unsafe
                with self.assertRaisesRegex(RuntimeError, "Writer trigger policy"):
                    _assert_exact_writer_trigger_policy(Connection(changed))

    def test_cleanup_function_is_exact_pg15_catalog_and_release_body(self):
        prosrc = self._cleanup_prosrc()
        self.assertEqual(
            len(prosrc.encode("utf-8")),
            CLEANUP_FUNCTION_PROSRC_BYTES,
        )
        self.assertEqual(
            hashlib.sha256(prosrc.encode("utf-8")).hexdigest(),
            CLEANUP_FUNCTION_PROSRC_SHA256,
        )

        class Connection:
            def __init__(self, row):
                self.row = row
                self.sql = ""

            def scalar(self, statement, params=None):
                del statement, params
                return "migration_owner"

            def execute(self, statement, params=None):
                del params
                self.sql = str(statement)
                return _Rows([self.row])

        row = self._cleanup_function_row()
        connection = Connection(row)
        self.assertEqual(
            _assert_exact_cleanup_function(connection),
            CLEANUP_FUNCTION_CATALOG_IDENTITY,
        )
        for catalog_term in (
            "pg_get_function_identity_arguments",
            "pg_get_function_result",
            "proargnames",
            "proargmodes",
            "proallargtypes",
            "prosrc",
            "proconfig",
        ):
            self.assertIn(catalog_term, connection.sql)
        for phase_check in (
            build_webapp_grant_statements,
            webapp_database_policy._expected_webapp_grant_inventory,
            build_bot_role_grant_statements,
            bot_database_policy._expected_bot_grant_inventory,
        ):
            with self.subTest(phase_check=phase_check.__name__):
                self.assertIn(
                    "_assert_exact_cleanup_function",
                    inspect.getsource(phase_check),
                )

        for key, unsafe in (
            (
                "identity_arguments",
                "timestamp with time zone, integer",
            ),
            ("function_owner", "legacy_owner"),
            ("function_language", "sql"),
            ("function_result", "record"),
            ("argument_names", "cutoff,row_limit"),
            ("argument_modes", "i,i"),
            ("prosecdef", False),
            ("prosrc", prosrc + " "),
            ("first_config", "search_path=public"),
        ):
            with self.subTest(key=key):
                changed = dict(row)
                changed[key] = unsafe
                with self.assertRaisesRegex(
                    RuntimeError,
                    "cleanup function",
                ):
                    _assert_exact_cleanup_function(Connection(changed))

    def test_projection_allowlists_equal_the_immutable_release_policy(self):
        policy_payload = json.dumps(
            {
                "tables": list(PROJECTION_TABLES),
                "forbidden_fields": [
                    list(item)
                    for item in sorted(PROJECTION_FORBIDDEN_FIELDS)
                ],
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertEqual(tuple(PROJECTION_TABLES), CANONICAL_PROJECTION_TABLES)
        self.assertEqual(
            frozenset(PROJECTION_FORBIDDEN_FIELDS),
            CANONICAL_PROJECTION_FORBIDDEN_FIELDS,
        )
        self.assertEqual(
            hashlib.sha256(policy_payload).hexdigest(),
            CANONICAL_PROJECTION_POLICY_SHA256,
        )

        schema_rows = [
            (table_name, "id")
            for table_name in CANONICAL_PROJECTION_TABLES
        ] + [
            ("dr_event_deliveries", "first_attempt_at"),
            ("dr_events", "source_xid"),
            ("users", "admin_password_hash"),
        ]
        expected_fields = [
            (table_name, "id")
            for table_name in CANONICAL_PROJECTION_TABLES
        ] + [("dr_event_deliveries", "first_attempt_at")]

        class Connection:
            def __init__(self, tables, fields):
                self.tables = tables
                self.fields = fields

            def execute(self, statement, params=None):
                del params
                sql = str(statement)
                if "information_schema.columns" in sql:
                    return _Rows(schema_rows)
                if "dr_projection_table_allowlist" in sql:
                    return _Rows(self.tables)
                if "dr_projection_field_allowlist" in sql:
                    return _Rows(self.fields)
                raise AssertionError(sql)

        exact_tables = list(CANONICAL_PROJECTION_TABLES)
        policy = _assert_exact_projection_policy(
            Connection(exact_tables, expected_fields)
        )
        self.assertNotIn("source_xid", policy["dr_events"])
        self.assertNotIn("admin_password_hash", policy["users"])

        tampered = (
            (
                exact_tables + ["dr_database_runtime"],
                expected_fields,
            ),
            (
                exact_tables[:-1],
                expected_fields,
            ),
            (
                exact_tables,
                expected_fields[:-1],
            ),
            (
                exact_tables,
                expected_fields + [("users", "admin_password_hash")],
            ),
            (
                exact_tables,
                expected_fields
                + [("dr_database_runtime", "enforcement_enabled")],
            ),
        )
        for tables, fields in tampered:
            with self.subTest(tables=len(tables), fields=len(fields)):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "projection .* allowlist",
                ):
                    _assert_exact_projection_policy(
                        Connection(tables, fields)
                    )

    def test_runtime_role_closure_uses_cluster_ownership_dependencies(self):
        base = {
            "rolconnlimit": -1,
            "valid_until": "infinity",
            "role_setting_count": 0,
            "database_setting_count": 0,
            "owned_dependency_count": 0,
            "user_mapping_count": 0,
            "default_acl_owner_count": 0,
        }

        class Connection:
            def __init__(self, row):
                self.row = row
                self.sql = ""

            def execute(self, statement, params=None):
                del params
                self.sql = str(statement)
                return _Rows([self.row])

        with patch.object(webapp_database_policy, "_role_state"):
            safe = Connection(base)
            _assert_exact_role_closure(safe, ["runtime_role"])
            for catalog in ("pg_shdepend", "pg_user_mapping", "pg_default_acl"):
                self.assertIn(catalog, safe.sql)
            for key in (
                "owned_dependency_count",
                "user_mapping_count",
                "default_acl_owner_count",
            ):
                with self.subTest(key=key):
                    row = dict(base)
                    row[key] = 1
                    with self.assertRaisesRegex(RuntimeError, "exactly closed"):
                        _assert_exact_role_closure(Connection(row), ["runtime_role"])

    def test_database_authorization_closure_rejects_stale_access_and_owners(self):
        safe_roles = [
            {
                "rolname": "migration_owner",
                "rolcanlogin": True,
                "rolsuper": True,
            },
            {
                "rolname": "runtime_role",
                "rolcanlogin": True,
                "rolsuper": False,
            },
            {
                "rolname": "pg_database_owner",
                "rolcanlogin": False,
                "rolsuper": False,
            },
        ]

        class Connection:
            def __init__(
                self,
                *,
                roles=None,
                grants=None,
                owners=None,
                owner_path_count=0,
            ):
                self.roles = roles or safe_roles
                self.grants = grants or []
                self.owners = owners or []
                self.owner_path_count = owner_path_count
                self.sql: list[str] = []

            def scalar(self, statement, params=None):
                del params
                sql = str(statement)
                self.sql.append(sql)
                if sql == "SELECT current_user":
                    return "migration_owner"
                if "WITH RECURSIVE membership_paths" in sql:
                    return self.owner_path_count
                raise AssertionError(sql)

            def execute(self, statement, params=None):
                del params
                sql = str(statement)
                self.sql.append(sql)
                if "SELECT rolname, rolcanlogin, rolsuper" in sql:
                    return _Rows(self.roles)
                if "SELECT grant_entry.object_kind" in sql:
                    return _Rows(self.grants)
                if "SELECT owner_entry.object_kind" in sql:
                    return _Rows(self.owners)
                raise AssertionError(sql)

        safe = Connection()
        _assert_exact_database_authorization_closure(
            safe,
            ["runtime_role"],
        )
        combined_sql = "\n".join(safe.sql)
        for catalog in (
            "pg_database",
            "pg_namespace",
            "pg_class",
            "pg_default_acl",
            "pg_auth_members",
        ):
            self.assertIn(catalog, combined_sql)

        for object_kind in (
            "database",
            "schema",
            "relation",
            "default-acl",
        ):
            with self.subTest(stale_grant=object_kind):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "unexpected grantee",
                ):
                    _assert_exact_database_authorization_closure(
                        Connection(grants=[(object_kind, 1)]),
                        ["runtime_role"],
                    )

        with self.assertRaisesRegex(RuntimeError, "unexpected object owner"):
            _assert_exact_database_authorization_closure(
                Connection(owners=[("relation", 1)]),
                ["runtime_role"],
            )
        with self.assertRaisesRegex(RuntimeError, "membership path"):
            _assert_exact_database_authorization_closure(
                Connection(owner_path_count=1),
                ["runtime_role"],
            )

        legacy_path_roles = safe_roles + [
            {
                "rolname": "legacy_owner",
                "rolcanlogin": False,
                "rolsuper": False,
            },
            {
                "rolname": "stale_login",
                "rolcanlogin": True,
                "rolsuper": False,
            },
        ]
        with self.assertRaisesRegex(
            RuntimeError,
            "unexpected (?:LOGIN state|custom role)",
        ):
            _assert_exact_database_authorization_closure(
                Connection(roles=legacy_path_roles),
                ["runtime_role"],
            )

        stale_superuser_roles = safe_roles + [
            {
                "rolname": "stale_superuser",
                "rolcanlogin": True,
                "rolsuper": True,
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "unexpected superuser"):
            _assert_exact_database_authorization_closure(
                Connection(roles=stale_superuser_roles),
                ["runtime_role"],
            )

    def test_runtime_roles_are_confined_to_the_current_database(self):
        database_rows = [
            ("appdb", True),
            ("postgres", True),
            ("template0", False),
            ("template1", True),
        ]

        class Connection:
            def __init__(self, scope_rows=None):
                self.scope_rows = scope_rows
                self.sql: list[str] = []

            def scalar(self, statement, params=None):
                del params
                sql = str(statement)
                self.sql.append(sql)
                if sql == "SELECT current_database()":
                    return "appdb"
                raise AssertionError(sql)

            def execute(self, statement, params=None):
                del params
                sql = str(statement)
                self.sql.append(sql)
                if "SELECT datname, datallowconn FROM pg_database" in sql:
                    return _Rows(database_rows)
                if "has_database_privilege" in sql:
                    rows = self.scope_rows
                    if rows is None:
                        rows = [
                            (
                                "runtime_role",
                                database_name,
                                database_name == "appdb",
                                False,
                                False,
                            )
                            for database_name, _allow_connections in database_rows
                        ]
                    return _Rows(rows)
                raise AssertionError(sql)

        connection = Connection()
        statements = _database_scope_statements(
            connection,
            ["runtime_role"],
            grant_current=True,
        )
        self.assertEqual(
            statements,
            [
                "REVOKE ALL ON DATABASE appdb FROM PUBLIC, runtime_role",
                "REVOKE ALL ON DATABASE postgres FROM PUBLIC, runtime_role",
                "REVOKE ALL ON DATABASE template0 FROM PUBLIC, runtime_role",
                "REVOKE ALL ON DATABASE template1 FROM PUBLIC, runtime_role",
                "GRANT CONNECT ON DATABASE appdb TO runtime_role",
            ],
        )
        _assert_exact_runtime_database_scope(connection, ["runtime_role"])
        self.assertIn("has_database_privilege", "\n".join(connection.sql))

        zero_scope_rows = [
            ("runtime_role", database_name, False, False, False)
            for database_name, _allow_connections in database_rows
        ]
        _assert_exact_runtime_database_scope(
            Connection(scope_rows=zero_scope_rows),
            ["runtime_role"],
            grant_current=False,
        )
        with self.assertRaisesRegex(RuntimeError, "exact database"):
            _assert_exact_runtime_database_scope(
                Connection(),
                ["runtime_role"],
                grant_current=False,
            )

        with self.assertRaisesRegex(RuntimeError, "exact database"):
            _assert_exact_runtime_database_scope(
                Connection(
                    scope_rows=[
                        (
                            "runtime_role",
                            database_name,
                            database_name == "appdb",
                            False,
                            database_name == "postgres",
                        )
                        for database_name, _allow_connections in database_rows
                    ]
                ),
                ["runtime_role"],
            )

    def test_pre_migration_roles_have_zero_database_scope(self):
        module = __import__(
            "scripts.provision_three_site_database_roles",
            fromlist=["main"],
        )
        source = inspect.getsource(module)
        self.assertIn("CONNECTION LIMIT -1", source)
        self.assertIn("ALTER ROLE {role} RESET ALL", source)
        self.assertIn("grant_current=False", source)

        class Connection:
            def __init__(self):
                self.executed: list[str] = []
                self.format_sql: list[str] = []

            def scalar(self, statement, params=None):
                sql = str(statement)
                if sql == "SELECT current_database()":
                    return "appdb"
                if sql.startswith("SELECT 1 FROM pg_roles"):
                    return False
                if sql.startswith("SELECT format("):
                    self.format_sql.append(sql)
                    if "CREATE ROLE" in sql:
                        return f"CREATE ROLE {params['role']} CLOSED"
                    return (
                        f"REVOKE {params['parent']} "
                        f"FROM {params['member']}"
                    )
                raise AssertionError(sql)

            def execute(self, statement, params=None):
                del params
                sql = str(statement)
                if "SELECT datname, datallowconn FROM pg_database" in sql:
                    return _Rows(
                        [
                            ("appdb", True),
                            ("postgres", True),
                            ("template0", False),
                            ("template1", True),
                        ]
                    )
                if "FROM pg_auth_members membership" in sql:
                    return _Rows([])
                raise AssertionError(sql)

            def exec_driver_sql(self, statement):
                self.executed.append(statement)

        connection = Connection()

        class Begin:
            def __enter__(self):
                return connection

            def __exit__(self, exc_type, exc, traceback):
                del exc_type, exc, traceback
                return False

        class Engine:
            def begin(self):
                return Begin()

            def dispose(self):
                return None

        role_passwords = {
            "THREE_SITE_APP_DB_PASSWORD": "app-password",
            "THREE_SITE_RECEIVER_DB_PASSWORD": "receiver-password",
            "THREE_SITE_DELIVERY_DB_PASSWORD": "delivery-password",
            "THREE_SITE_PROJECTION_DB_PASSWORD": "projection-password",
            "THREE_SITE_BLOB_DB_PASSWORD": "blob-password",
            "THREE_SITE_EFFECT_DB_PASSWORD": "effect-password",
            "THREE_SITE_CONTROL_DB_PASSWORD": "control-password",
            "THREE_SITE_OBSERVER_DB_PASSWORD": "observer-password",
        }
        with (
            patch.dict(
                os.environ,
                {
                    "SYNC_DATABASE_URL": "postgresql://plan.invalid/appdb",
                    **role_passwords,
                },
                clear=True,
            ),
            patch.object(module, "create_engine", return_value=Engine()),
            patch.object(module, "_assert_exact_role_closure") as role_closure,
            patch.object(
                module,
                "_assert_exact_runtime_database_scope",
            ) as database_scope,
            patch.object(
                module,
                "_direct_grant_inventory",
                return_value=set(),
            ) as grant_inventory,
            patch.object(
                module,
                "_unsafe_public_privilege_count",
                return_value=0,
            ) as public_inventory,
            patch.object(
                module,
                "_assert_exact_public_type_usage",
            ) as public_type_usage,
            patch.object(
                module,
                "_assert_exact_database_authorization_closure",
            ) as authorization_closure,
            patch.object(
                __import__("sys"),
                "argv",
                [
                    "provision_three_site_database_roles.py",
                    "--role-prefix",
                    "webapp_fi",
                ],
            ),
            patch("builtins.print"),
        ):
            self.assertEqual(module.main(), 0)

        self.assertEqual(len(connection.format_sql), 8)
        self.assertTrue(
            all(
                "CONNECTION LIMIT -1 VALID UNTIL ''infinity''"
                in sql
                for sql in connection.format_sql
            )
        )
        self.assertEqual(
            sum(" RESET ALL" in sql for sql in connection.executed),
            8 * 5,
        )
        public_revokes = [
            sql
            for sql in connection.executed
            if sql.startswith("REVOKE ALL ON DATABASE")
        ]
        self.assertEqual(len(public_revokes), 4)
        self.assertTrue(all("FROM PUBLIC" in sql for sql in public_revokes))
        self.assertFalse(
            any(sql.startswith("GRANT CONNECT") for sql in connection.executed)
        )
        for statement in (
            "REVOKE ALL ON SCHEMA public FROM PUBLIC",
            PUBLIC_TRUSTED_LANGUAGE_REVOKE,
            "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC",
            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
            "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC",
        ):
            self.assertIn(statement, connection.executed)
        role_closure.assert_called_once_with(connection, module_roles := {
            "webapp_fi_app": "app-password",
            "webapp_fi_receiver": "receiver-password",
            "webapp_fi_delivery": "delivery-password",
            "webapp_fi_projection": "projection-password",
            "webapp_fi_blob": "blob-password",
            "webapp_fi_effect": "effect-password",
            "webapp_fi_control": "control-password",
            "webapp_fi_observer": "observer-password",
        })
        database_scope.assert_called_once_with(
            connection,
            module_roles,
            grant_current=False,
        )
        grant_inventory.assert_called_once_with(connection, module_roles)
        public_inventory.assert_called_once_with(connection)
        public_type_usage.assert_called_once_with(connection)
        authorization_closure.assert_called_once_with(
            connection,
            module_roles,
        )

    def test_public_closure_inventory_covers_every_sensitive_object_class(self):
        class Connection:
            def __init__(self):
                self.sql = ""

            def scalar(self, statement, params=None):
                del params
                self.sql = str(statement)
                return 1

        connection = Connection()
        self.assertEqual(_unsafe_public_privilege_count(connection), 1)
        self.assertNotIn(
            "database_row.datname=current_database()",
            connection.sql,
        )
        for catalog in (
            "pg_namespace",
            "pg_proc",
            "pg_type",
            "pg_language",
            "pg_largeobject_metadata",
            "pg_foreign_data_wrapper",
            "pg_foreign_server",
            "pg_tablespace",
            "pg_database",
            "pg_user_mapping",
            "pg_default_acl",
        ):
            self.assertIn(catalog, connection.sql)

    def test_public_closure_revokes_both_pg15_trusted_languages(self):
        self.assertEqual(
            PUBLIC_TRUSTED_LANGUAGE_REVOKE,
            "REVOKE ALL ON LANGUAGE sql, plpgsql FROM PUBLIC",
        )
        for phase_builder in (
            build_webapp_grant_statements,
            build_bot_role_grant_statements,
        ):
            with self.subTest(phase_builder=phase_builder.__name__):
                self.assertIn(
                    "PUBLIC_TRUSTED_LANGUAGE_REVOKE",
                    inspect.getsource(phase_builder),
                )

    def test_database_derived_grant_identifiers_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "identifiers"):
            _projection_grants(
                object(),
                "webapp_fi_projection",
                projection_policy={
                    "safe_table; DROP TABLE users": ("id",),
                },
            )

    def test_database_runtime_state_is_preserved_or_exactly_fenced(self):
        before = {
            "singleton_id": 1,
            "enforcement_enabled": False,
            "physical_site": None,
            "application_role": None,
            "projection_role": None,
            "control_role": None,
            "require_witness_lease": True,
            "updated_by": "migration",
            "updated_at": "2026-07-28T00:00:00+00:00",
        }
        _assert_database_runtime_state(
            _RuntimeConnection(dict(before)),
            expected=before,
            label="grants phase",
        )
        after = _expected_fenced_runtime_state(
            before,
            site="webapp_fi",
            application_role="webapp_fi_app",
            projection_role="webapp_fi_projection",
            control_role="webapp_fi_control",
            require_witness_lease=True,
            updated_by="operator",
            updated_at="2026-07-28T00:01:00+00:00",
        )
        _assert_database_runtime_state(
            _RuntimeConnection(dict(after)),
            expected=after,
            label="fence phase",
        )
        rewritten = dict(after)
        rewritten["updated_by"] = "trigger-rewrite"
        with self.assertRaisesRegex(RuntimeError, "outside the exact fence"):
            _assert_database_runtime_state(
                _RuntimeConnection(rewritten),
                expected=after,
                label="fence phase",
            )

    def test_exact_grant_inventory_rejects_missing_extra_and_public_privileges(self):
        grant = (
            "table",
            "public",
            "alembic_version",
            "",
            "SELECT",
            "webapp_fi_app",
            False,
        )
        _assert_exact_grant_inventory(
            actual={grant},
            expected={grant},
            unsafe_public_count=0,
            label="database",
        )
        with self.assertRaisesRegex(RuntimeError, "exact release"):
            _assert_exact_grant_inventory(
                actual=set(),
                expected={grant},
                unsafe_public_count=0,
                label="database",
            )
        with self.assertRaisesRegex(RuntimeError, "unsafe PUBLIC"):
            _assert_exact_grant_inventory(
                actual={grant},
                expected={grant},
                unsafe_public_count=1,
                label="database",
            )

    def test_webapp_grants_and_fence_sql_are_runtime_separated(self):
        assert_webapp_phase_boundary(
            "grants",
            ["GRANT SELECT ON TABLE public.alembic_version TO webapp_fi_app"],
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "grant phase may not mutate",
        ):
            assert_webapp_phase_boundary(
                "grants",
                [
                    "UPDATE public.dr_database_runtime "
                    "SET enforcement_enabled = true WHERE singleton_id = 1"
                ],
            )

        service_roles = {
            "receiver": "webapp_fi_receiver",
            "delivery": "webapp_fi_delivery",
            "projector": "webapp_fi_projection",
            "blob": "webapp_fi_blob",
            "effect": "webapp_fi_effect",
        }
        with (
            patch.object(
                webapp_database_policy,
                "_validated_context",
                return_value=(service_roles, tuple(), "webapp"),
            ),
            patch.object(
                webapp_database_policy,
                "_assert_fence_prerequisites",
            ) as prerequisites,
        ):
            statements = build_webapp_fence_statements(
                object(),
                site="webapp_fi",
                application_role="webapp_fi_app",
                projection_role="webapp_fi_projection",
                receiver_role="webapp_fi_receiver",
                delivery_role="webapp_fi_delivery",
                blob_role="webapp_fi_blob",
                effect_role="webapp_fi_effect",
                control_role="webapp_fi_control",
                observer_role="webapp_fi_observer",
                operator="test-operator",
            )

        prerequisites.assert_called_once()
        self.assertEqual(len(statements), 1)
        self.assertEqual(
            statements,
            [
                "UPDATE public.dr_database_runtime SET "
                "enforcement_enabled = true, physical_site = 'webapp_fi', "
                "application_role = 'webapp_fi_app', "
                "projection_role = 'webapp_fi_projection', "
                "control_role = 'webapp_fi_control', "
                "require_witness_lease = true, "
                "updated_by = 'test-operator', "
                "updated_at = transaction_timestamp() WHERE singleton_id = 1"
            ],
        )
        assert_webapp_phase_boundary("fence", statements)

    def test_bot_fence_is_one_bounded_update_separate_from_role_grants(self):
        statements = build_bot_fence_statements("bot_fi")
        self.assertEqual(len(statements), 1)
        statement = statements[0]
        self.assertEqual(statement.count("UPDATE public.dr_database_runtime"), 1)
        self.assertIn("enforcement_enabled=true", statement)
        self.assertTrue(statement.endswith("WHERE singleton_id=1"))
        self.assertNotIn("GRANT ", statement)
        self.assertNotIn("ALTER ROLE", statement)

    def test_bot_roles_grants_dry_run_needs_no_password_and_executes_nothing(self):
        runtime = {
            "singleton_id": 1,
            "enforcement_enabled": False,
            "physical_site": None,
            "application_role": None,
            "projection_role": None,
            "control_role": None,
            "require_witness_lease": False,
            "updated_by": "migration",
            "updated_at": "2026-07-28T00:00:00+00:00",
        }

        class Connection:
            def __init__(self):
                self.rolled_back = False

            def rollback(self):
                self.rolled_back = True

            def exec_driver_sql(self, statement):
                raise AssertionError(f"dry-run executed SQL: {statement}")

        connection = Connection()

        class Begin:
            def __enter__(self):
                return connection

            def __exit__(self, exc_type, exc, traceback):
                del exc_type, exc, traceback
                return False

        class Engine:
            def begin(self):
                return Begin()

            def dispose(self):
                return None

        with (
            patch.dict(
                os.environ,
                {"SYNC_DATABASE_URL": "postgresql://plan.invalid/db"},
                clear=True,
            ),
            patch.object(
                bot_database_policy,
                "create_engine",
                return_value=Engine(),
            ),
            patch.object(
                bot_database_policy,
                "_database_runtime_state",
                return_value=runtime,
            ) as runtime_reader,
            patch.object(
                bot_database_policy,
                "_build_role_grant_statements",
                return_value=["GRANT SELECT ON TABLE planned TO bot_fi_app"],
            ) as planner,
            patch.object(
                bot_database_policy,
                "_execute_role_grants",
            ) as executor,
            patch.object(
                bot_database_policy,
                "_assert_database_runtime_state",
            ) as runtime_assertion,
            patch("builtins.print"),
        ):
            status = bot_database_policy.main(
                ["--phase", "roles-grants", "--role-prefix", "bot_fi"]
            )

        self.assertEqual(status, 0)
        self.assertTrue(connection.rolled_back)
        self.assertNotIn(
            "exec_driver_sql",
            inspect.getsource(build_bot_role_grant_statements),
        )
        runtime_reader.assert_called_once_with(connection, for_update=False)
        planner.assert_called_once_with(connection, prefix="bot_fi")
        executor.assert_not_called()
        self.assertFalse(runtime_assertion.call_args.kwargs["for_update"])

    def test_private_dr_processes_have_closed_distinct_database_scopes(self):
        self.assertEqual(
            set(PROJECTION_SERVICE_SCOPES.values()),
            {"receiver", "delivery", "projector", "blob", "effect"},
        )
        self.assertEqual(set(DR_SERVICE_INTERNAL_GRANTS), set(PROJECTION_SERVICE_SCOPES.values()))
        self.assertEqual(set(BOT_DR_SERVICE_GRANTS), {"receiver", "delivery", "projector"})
        self.assertEqual(projection_scope_for_service("dr_receiver"), "receiver")
        self.assertEqual(
            projection_database_role_suffix_for_service("dr_projection_worker"),
            "projection",
        )
        self.assertEqual(
            projection_database_role_suffix_for_service("dr_receiver"),
            "receiver",
        )
        with self.assertRaises(RuntimeError):
            projection_scope_for_service("api")

    def test_bot_queue_grants_and_webapp_deny_set_are_the_same_closed_surface(self):
        self.assertEqual(
            frozenset(BOT_LOCAL_QUEUE_APPLICATION_GRANTS),
            EXPECTED_BOT_LOCAL_EXECUTION_TABLES,
        )
        self.assertEqual(BOT_LOCAL_EXECUTION_TABLES, EXPECTED_BOT_LOCAL_EXECUTION_TABLES)
        self.assertTrue(
            all(
                permissions == "SELECT, INSERT, UPDATE, DELETE"
                for permissions in BOT_LOCAL_QUEUE_APPLICATION_GRANTS.values()
            )
        )

    def test_sync_observer_has_one_closed_read_only_table_surface(self):
        expected = frozenset(
            {
                "alembic_version", "dr_database_runtime", "dr_events",
                "dr_event_deliveries", "dr_event_receipts",
            }
        )
        self.assertEqual(BOT_SYNC_OBSERVER_TABLES, expected)
        self.assertEqual(WEBAPP_SYNC_OBSERVER_TABLES, expected)

    def test_convergence_observer_is_explicitly_broader_but_read_only(self):
        transport = {
            "dr_producer_cursors", "dr_destination_cursors", "dr_stream_checkpoints",
            "dr_conflict_quarantine", "dr_blob_manifests",
        }
        self.assertTrue(transport <= BOT_CONVERGENCE_OBSERVER_TABLES)
        self.assertTrue(transport <= WEBAPP_CONVERGENCE_OBSERVER_TABLES)
        self.assertTrue(BOT_SYNC_OBSERVER_TABLES < BOT_CONVERGENCE_OBSERVER_TABLES)
        self.assertTrue(WEBAPP_SYNC_OBSERVER_TABLES < WEBAPP_CONVERGENCE_OBSERVER_TABLES)

    def test_convergence_observer_tracks_the_exact_deep_product_contract(self):
        # A newly synchronized product table must not silently be omitted from
        # the read-only convergence proof.  The Bot and WebApp projection
        # contracts intentionally contain the same cross-authority data set.
        expected = frozenset(synced_parity_table_names("deep"))
        self.assertEqual(BOT_CONVERGENCE_OBSERVER_TABLES & expected, expected)
        self.assertEqual(WEBAPP_CONVERGENCE_OBSERVER_TABLES & expected, expected)

    def test_remote_event_insert_omits_source_local_xid(self):
        payload = {
            "protocol_version": 2,
            "event_id": "00000000-0000-4000-8000-000000000001",
            "origin_authority": "foreign",
            "origin_physical_site": "bot_fi",
            "producer_epoch": 1,
            "producer_sequence": 1,
            "aggregate_type": "commodities",
            "aggregate_id": "1",
            "aggregate_db_id": "1",
            "aggregate_version": 1,
            "operation": "INSERT",
            "canonical_payload": {"id": 1, "name": "gold"},
            "canonical_payload_hash": sha256_json({"id": 1, "name": "gold"}),
            "schema_version": 1,
            "causation_id": None,
            "idempotency_key": None,
            "writer_epoch": None,
            "tombstone": False,
            "created_at": "2026-07-20T00:00:00+00:00",
            "transaction_id": "00000000-0000-4000-8000-000000000002",
            "transaction_position": 1,
            "transaction_size": 1,
            "transaction_hash": "4" * 64,
            "destination_streams": {
                "webapp_fi": {
                    "sequence": 1,
                    "transaction_id": "00000000-0000-4000-8000-000000000002",
                    "transaction_position": 1,
                    "transaction_size": 1,
                    "transaction_hash": "4" * 64,
                },
                "webapp_ir": {
                    "sequence": 1,
                    "transaction_id": "00000000-0000-4000-8000-000000000002",
                    "transaction_position": 1,
                    "transaction_size": 1,
                    "transaction_hash": "4" * 64,
                },
            },
        }
        values = _event_values(validate_envelope(payload))
        self.assertNotIn("source_xid", values)


if __name__ == "__main__":
    unittest.main()
