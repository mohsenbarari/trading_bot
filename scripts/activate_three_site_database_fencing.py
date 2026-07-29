#!/usr/bin/env python3
"""Apply WebApp grants and fencing as separate dry-run-first phases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Iterable

from sqlalchemy import create_engine, text


ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
WRITER_FUNCTION_PROSRC_SHA256 = (
    "c7823146c21ca2e42d516c0c42519fcd6136089d08d1a564be281c45d56e3347"
)
WRITER_FUNCTION_PROSRC_BYTES = 4744
PUBLIC_TRUSTED_LANGUAGE_REVOKE = (
    "REVOKE ALL ON LANGUAGE sql, plpgsql FROM PUBLIC"
)
CLEANUP_FUNCTION_PROSRC_SHA256 = (
    "b7807bc42b1a2306d5858dd8fb6aad2b687e4088343ad7b828c2dce832950371"
)
CLEANUP_FUNCTION_PROSRC_BYTES = 1641
CLEANUP_FUNCTION_CATALOG_IDENTITY = (
    "public.trading_bot_cleanup_expired_replay_nonces("
    "cutoff timestamp with time zone, row_limit integer)"
)
CLEANUP_FUNCTION_GRANT_IDENTITY = (
    "public.trading_bot_cleanup_expired_replay_nonces(timestamptz, integer)"
)
CANONICAL_PROJECTION_POLICY_SHA256 = (
    "dac519ef55644381200676e12e9ab5f4841462cb4ed4430002bb8e7995d8e29f"
)
CANONICAL_PROJECTION_TABLES = (
    "accountant_relations",
    "admin_broadcast_messages",
    "admin_market_messages",
    "chat_files",
    "chat_members",
    "chats",
    "commodities",
    "commodity_aliases",
    "conversations",
    "customer_relations",
    "dr_blob_deliveries",
    "dr_blob_manifests",
    "dr_blob_receipts",
    "dr_conflict_quarantine",
    "dr_effect_fanouts",
    "dr_effect_outbox",
    "dr_event_deliveries",
    "dr_event_receipts",
    "dr_events",
    "dr_file_intents",
    "dr_producer_cursors",
    "dr_projection_versions",
    "dr_recovery_manifests",
    "dr_replay_nonces",
    "dr_stream_checkpoints",
    "invitation_identity_reservations",
    "invitation_sms_deliveries",
    "invitations",
    "market_runtime_state",
    "market_schedule_overrides",
    "messages",
    "notifications",
    "offer_publication_states",
    "offer_requests",
    "offers",
    "push_subscriptions",
    "session_login_requests",
    "single_session_recovery_admin_targets",
    "single_session_recovery_requests",
    "sync_apply_watermarks",
    "sync_blocks",
    "telegram_admin_broadcast_receipts",
    "telegram_admin_broadcasts",
    "telegram_link_tokens",
    "telegram_notification_outbox",
    "trade_delivery_receipts",
    "trades",
    "trading_settings",
    "user_blocks",
    "user_counter_event_receipts",
    "user_notification_preferences",
    "user_sessions",
    "users",
)
CANONICAL_PROJECTION_FORBIDDEN_FIELDS = frozenset(
    {
        ("chats", "avatar_file_id"),
        ("dr_events", "source_xid"),
        ("invitation_identity_reservations", "normalized_account_name"),
        ("invitation_identity_reservations", "normalized_mobile"),
        ("offer_publication_states", "error_code"),
        ("offer_publication_states", "error_message"),
        ("offer_publication_states", "last_attempt_at"),
        ("offer_publication_states", "last_success_at"),
        ("offer_publication_states", "next_retry_at"),
        ("offer_publication_states", "offer_id"),
        ("offer_publication_states", "state_metadata"),
        ("offer_publication_states", "surface_resource_id"),
        ("offer_publication_states", "telegram_chat_id"),
        ("offer_publication_states", "telegram_message_id"),
        ("push_subscriptions", "last_error"),
        ("push_subscriptions", "platform"),
        ("push_subscriptions", "user_agent"),
        ("telegram_admin_broadcast_receipts", "lease_until"),
        ("telegram_admin_broadcast_receipts", "queue_handed_off_at"),
        ("telegram_admin_broadcast_receipts", "queue_job_id"),
        ("telegram_admin_broadcast_receipts", "worker_id"),
        ("telegram_admin_broadcasts", "queue_last_handed_off_at"),
        ("telegram_notification_outbox", "lease_until"),
        ("telegram_notification_outbox", "queue_handed_off_at"),
        ("telegram_notification_outbox", "queue_job_id"),
        ("telegram_notification_outbox", "worker_id"),
        ("trade_delivery_receipts", "lease_until"),
        ("trade_delivery_receipts", "notification_id"),
        ("trade_delivery_receipts", "offer_id"),
        ("trade_delivery_receipts", "trade_id"),
        ("trade_delivery_receipts", "worker_id"),
        ("users", "admin_password_hash"),
        ("users", "avatar_file_id"),
        ("users", "must_change_password"),
        ("users", "normalized_account_name"),
        ("users", "normalized_mobile_number"),
    }
)
DATABASE_RUNTIME_COLUMNS = (
    "singleton_id",
    "enforcement_enabled",
    "physical_site",
    "application_role",
    "projection_role",
    "control_role",
    "require_witness_lease",
    "updated_by",
    "updated_at",
)
GRANTS_CONFIRMATION = "APPLY-THREE-SITE-DATABASE-GRANTS"
FENCE_CONFIRMATION = "ENABLE-THREE-SITE-DATABASE-FENCING"
PHASE_CONFIRMATIONS = {
    "grants": GRANTS_CONFIRMATION,
    "fence": FENCE_CONFIRMATION,
}
RUNTIME_MUTATION_RE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM|MERGE\s+INTO|"
    r"TRUNCATE(?:\s+TABLE)?|ALTER\s+TABLE|DROP\s+TABLE)\s+"
    r"(?:public\.)?dr_database_runtime\b",
    re.IGNORECASE,
)
CONTROL_TABLES = (
    "dr_database_runtime",
    "dr_durability_state",
    "dr_projection_table_allowlist",
    "dr_projection_field_allowlist",
    "dr_projection_service_roles",
    "webapp_writer_state",
    "webapp_writer_activation_operations",
    "webapp_writer_transitions",
)
SYNC_OBSERVER_TABLES = frozenset(
    {
        "alembic_version",
        "dr_database_runtime",
        "dr_events",
        "dr_event_deliveries",
        "dr_event_receipts",
    }
)
# The observer is a separate LOGIN NOINHERIT role.  Its convergence-only
# read surface is deliberately enumerated here instead of borrowing the
# WebApp application role, control role, Blob credentials, or Writer power.
CONVERGENCE_PRODUCT_TABLES = frozenset(
    {
        "accountant_relations",
        "admin_broadcast_messages",
        "admin_market_messages",
        "commodities",
        "commodity_aliases",
        "customer_relations",
        "invitations",
        "market_runtime_state",
        "market_schedule_overrides",
        "notifications",
        "offer_publication_states",
        "offer_requests",
        "offers",
        "trades",
        "trade_delivery_receipts",
        "telegram_link_tokens",
        "telegram_admin_broadcasts",
        "telegram_admin_broadcast_receipts",
        "telegram_notification_outbox",
        "trading_settings",
        "user_blocks",
        "user_notification_preferences",
        "users",
    }
)
CONVERGENCE_OBSERVER_TABLES = CONVERGENCE_PRODUCT_TABLES | SYNC_OBSERVER_TABLES | frozenset(
    {
        "dr_producer_cursors",
        "dr_destination_cursors",
        "dr_stream_checkpoints",
        "dr_conflict_quarantine",
        "dr_blob_manifests",
    }
)
APPLICATION_INTERNAL_GRANTS = {
    "dr_destination_cursors": "SELECT, INSERT, UPDATE",
    "dr_producer_cursors": "SELECT, INSERT, UPDATE",
    "dr_events": "SELECT, INSERT, UPDATE",
    "dr_event_deliveries": "SELECT, INSERT",
    "dr_effect_outbox": "SELECT, INSERT, UPDATE",
    "dr_effect_fanouts": "SELECT, INSERT, UPDATE",
    "dr_blob_manifests": "SELECT, INSERT",
    "dr_file_intents": "SELECT, INSERT",
    "dr_blob_deliveries": "SELECT, INSERT",
    "dr_recovery_manifests": "SELECT, INSERT, UPDATE",
    "dr_durability_state": "SELECT",
}
DR_SERVICE_INTERNAL_GRANTS = {
    "receiver": {
        "dr_events": "SELECT",
        "dr_event_deliveries": "SELECT, INSERT, UPDATE",
        "dr_event_receipts": "SELECT, INSERT, UPDATE",
        "dr_stream_checkpoints": "SELECT, INSERT, UPDATE",
        "dr_conflict_quarantine": "SELECT, INSERT, UPDATE",
        "dr_replay_nonces": "SELECT, INSERT",
        "dr_blob_manifests": "SELECT",
        "dr_blob_deliveries": "SELECT, UPDATE",
    },
    "delivery": {
        "dr_events": "SELECT",
        "dr_event_deliveries": "SELECT, UPDATE",
    },
    "projector": {
        "dr_events": "SELECT",
        "dr_event_receipts": "SELECT, UPDATE",
        "dr_stream_checkpoints": "SELECT, UPDATE",
        "dr_projection_versions": "SELECT, INSERT, UPDATE",
        "dr_conflict_quarantine": "SELECT, INSERT, UPDATE",
    },
    "blob": {
        "dr_events": "SELECT",
        "dr_blob_manifests": "SELECT, INSERT, UPDATE",
        "dr_blob_deliveries": "SELECT, INSERT, UPDATE",
        "dr_blob_receipts": "SELECT, INSERT, UPDATE",
    },
    "effect": {
        "dr_effect_outbox": "SELECT, UPDATE",
    },
}
PROJECTOR_INTERNAL_TABLES = frozenset(
    {
        "sync_apply_watermarks",
        "sync_blocks",
        "user_counter_event_receipts",
        "dr_events",
        "dr_event_receipts",
        "dr_event_deliveries",
        "dr_stream_checkpoints",
        "dr_conflict_quarantine",
        "dr_replay_nonces",
        "dr_effect_outbox",
        "dr_effect_fanouts",
        "dr_producer_cursors",
        "dr_projection_versions",
        "dr_blob_manifests",
        "dr_file_intents",
        "dr_blob_deliveries",
        "dr_blob_receipts",
        "dr_recovery_manifests",
    }
)
BOT_LOCAL_EXECUTION_TABLES = frozenset(
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
APPLICATION_WRITE_EXCLUDED_TABLES = frozenset(CONTROL_TABLES) | PROJECTOR_INTERNAL_TABLES | BOT_LOCAL_EXECUTION_TABLES | frozenset(
    {
        "dr_event_destination_sequences",
    }
)
EXPECTED_WRITER_TRIGGER_TABLES = (
    "accountant_relations",
    "admin_broadcast_messages",
    "admin_market_messages",
    "chat_files",
    "chat_members",
    "chats",
    "commodities",
    "commodity_aliases",
    "conversations",
    "customer_relations",
    "dr_blob_deliveries",
    "dr_blob_manifests",
    "dr_blob_receipts",
    "dr_conflict_quarantine",
    "dr_destination_cursors",
    "dr_durability_state",
    "dr_effect_fanouts",
    "dr_effect_outbox",
    "dr_event_deliveries",
    "dr_event_receipts",
    "dr_events",
    "dr_file_intents",
    "dr_producer_cursors",
    "dr_projection_versions",
    "dr_recovery_manifests",
    "dr_replay_nonces",
    "dr_stream_checkpoints",
    "invitation_identity_reservations",
    "invitation_sms_deliveries",
    "invitations",
    "market_channel_notice_receipts",
    "market_runtime_state",
    "market_schedule_overrides",
    "messages",
    "notifications",
    "offer_publication_states",
    "offer_requests",
    "offers",
    "push_subscriptions",
    "session_login_requests",
    "single_session_recovery_admin_targets",
    "single_session_recovery_requests",
    "sync_apply_watermarks",
    "sync_blocks",
    "telegram_admin_broadcast_receipts",
    "telegram_admin_broadcasts",
    "telegram_channel_membership_sagas",
    "telegram_delivery_feeder_states",
    "telegram_delivery_jobs",
    "telegram_delivery_provider_outcomes",
    "telegram_delivery_reconciliation_evidence",
    "telegram_delivery_resume_operations",
    "telegram_delivery_runtime_gates",
    "telegram_interaction_anchor_states",
    "telegram_link_tokens",
    "telegram_notification_outbox",
    "telegram_registration_command_receipts",
    "telegram_registration_intents",
    "telegram_scheduled_operations",
    "trade_delivery_receipts",
    "trades",
    "trading_settings",
    "upload_batches",
    "upload_sessions",
    "user_blocks",
    "user_counter_event_receipts",
    "user_notification_preferences",
    "user_sessions",
    "users",
)


def _ident(value: str) -> str:
    if not ROLE_RE.fullmatch(value):
        raise RuntimeError("database role names must be unquoted lowercase PostgreSQL identifiers")
    return value


def _cluster_database_rows(
    connection,  # noqa: ANN001
) -> tuple[str, tuple[tuple[str, bool], ...]]:
    current_database = _ident(
        str(connection.scalar(text("SELECT current_database()")))
    )
    rows = tuple(
        (
            _ident(str(row[0])),
            bool(row[1]),
        )
        for row in connection.execute(
            text(
                "SELECT datname, datallowconn FROM pg_database "
                "ORDER BY datname"
            )
        ).all()
    )
    names = tuple(row[0] for row in rows)
    if (
        not rows
        or len(names) != len(set(names))
        or current_database not in names
        or not dict(rows)[current_database]
    ):
        raise RuntimeError("database cluster identity is not exact")
    return current_database, rows


def _database_scope_statements(
    connection,  # noqa: ANN001
    roles: Iterable[str],
    *,
    grant_current: bool,
) -> list[str]:
    validated_roles = tuple(sorted({_ident(role) for role in roles}))
    if not validated_roles:
        raise RuntimeError("database scope requires at least one runtime role")
    current_database, database_rows = _cluster_database_rows(connection)
    role_list = ", ".join(validated_roles)
    statements = [
        f"REVOKE ALL ON DATABASE {database_name} FROM PUBLIC, {role_list}"
        for database_name, _allow_connections in database_rows
    ]
    if grant_current:
        statements.append(
            f"GRANT CONNECT ON DATABASE {current_database} TO {role_list}"
        )
    return statements


def _assert_exact_runtime_database_scope(
    connection,  # noqa: ANN001
    roles: Iterable[str],
    *,
    grant_current: bool = True,
) -> None:
    validated_roles = tuple(sorted({_ident(role) for role in roles}))
    if not validated_roles:
        raise RuntimeError("database scope requires at least one runtime role")
    current_database, database_rows = _cluster_database_rows(connection)
    rows = connection.execute(
        text(
            "SELECT role.rolname, database_row.datname, "
            "has_database_privilege(role.oid, database_row.oid, 'CONNECT') "
            "can_connect, "
            "has_database_privilege(role.oid, database_row.oid, 'TEMPORARY') "
            "can_create_temporary, "
            "has_database_privilege(role.oid, database_row.oid, 'CREATE') "
            "can_create "
            "FROM pg_roles role CROSS JOIN pg_database database_row "
            "WHERE role.rolname=ANY(:roles) "
            "ORDER BY role.rolname, database_row.datname"
        ),
        {"roles": list(validated_roles)},
    ).all()
    actual = {
        (
            _ident(str(row[0])),
            _ident(str(row[1])),
            bool(row[2]),
            bool(row[3]),
            bool(row[4]),
        )
        for row in rows
    }
    expected = {
        (
            role,
            database_name,
            grant_current and database_name == current_database,
            False,
            False,
        )
        for role in validated_roles
        for database_name, _allow_connections in database_rows
    }
    if len(rows) != len(actual) or actual != expected:
        raise RuntimeError(
            "runtime roles are outside their exact database privilege scope"
        )


def _assert_exact_projection_policy(
    connection,  # noqa: ANN001
) -> dict[str, tuple[str, ...]]:
    policy_payload = json.dumps(
        {
            "tables": list(CANONICAL_PROJECTION_TABLES),
            "forbidden_fields": [
                list(item)
                for item in sorted(CANONICAL_PROJECTION_FORBIDDEN_FIELDS)
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if hashlib.sha256(policy_payload).hexdigest() != (
        CANONICAL_PROJECTION_POLICY_SHA256
    ):
        raise RuntimeError("embedded canonical projection policy digest is invalid")
    schema_rows = connection.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=ANY(:tables) "
            "ORDER BY table_name, ordinal_position"
        ),
        {"tables": list(CANONICAL_PROJECTION_TABLES)},
    ).all()
    schema_columns: dict[str, list[str]] = {
        table_name: [] for table_name in CANONICAL_PROJECTION_TABLES
    }
    for raw_table_name, raw_column_name in schema_rows:
        table_name = _ident(str(raw_table_name))
        column_name = _ident(str(raw_column_name))
        if table_name not in schema_columns:
            raise RuntimeError("release projection schema contains an unexpected table")
        schema_columns[table_name].append(column_name)
    if any(not columns for columns in schema_columns.values()):
        raise RuntimeError("release projection schema is incomplete")

    canonical = {
        table_name: tuple(
            column_name
            for column_name in columns
            if (table_name, column_name)
            not in CANONICAL_PROJECTION_FORBIDDEN_FIELDS
        )
        for table_name, columns in schema_columns.items()
    }
    if any(not columns for columns in canonical.values()):
        raise RuntimeError("release projection policy contains an empty table")

    raw_tables = connection.execute(
        text(
            "SELECT table_name FROM public.dr_projection_table_allowlist "
            "ORDER BY table_name"
        )
    ).scalars().all()
    actual_tables = tuple(_ident(str(table_name)) for table_name in raw_tables)
    if (
        len(actual_tables) != len(set(actual_tables))
        or set(actual_tables) != set(CANONICAL_PROJECTION_TABLES)
    ):
        raise RuntimeError(
            "database projection table allowlist does not match the canonical release"
        )

    raw_fields = connection.execute(
        text(
            "SELECT table_name, column_name "
            "FROM public.dr_projection_field_allowlist "
            "ORDER BY table_name, column_name"
        )
    ).all()
    actual_fields = tuple(
        (_ident(str(table_name)), _ident(str(column_name)))
        for table_name, column_name in raw_fields
    )
    expected_fields = {
        (table_name, column_name)
        for table_name, columns in canonical.items()
        for column_name in columns
    }
    if (
        len(actual_fields) != len(set(actual_fields))
        or set(actual_fields) != expected_fields
    ):
        raise RuntimeError(
            "database projection field allowlist does not match the canonical release"
        )
    return canonical


def _assert_exact_cleanup_function(connection) -> str:  # noqa: ANN001
    expected_owner = _ident(str(connection.scalar(text("SELECT current_user"))))
    row = connection.execute(
        text(
            "SELECT namespace.nspname function_schema, procedure.proname, "
            "pg_get_function_identity_arguments(procedure.oid) identity_arguments, "
            "pg_get_function_arguments(procedure.oid) function_arguments, "
            "pg_get_function_result(procedure.oid) function_result, "
            "oidvectortypes(procedure.proargtypes) input_argument_types, "
            "coalesce(array_to_string(procedure.proargnames, ','), '') argument_names, "
            "coalesce(array_to_string(procedure.proargmodes, ','), '') argument_modes, "
            "coalesce(array_to_string(ARRAY("
            " SELECT format_type(argument.type_oid, NULL) "
            " FROM unnest(procedure.proallargtypes) WITH ORDINALITY "
            " AS argument(type_oid, position) ORDER BY argument.position"
            "), ','), '') all_argument_types, "
            "owner.rolname function_owner, language.lanname function_language, "
            "procedure.prokind, procedure.provolatile, procedure.proparallel, "
            "procedure.proleakproof, procedure.proretset, procedure.proisstrict, "
            "procedure.pronargs, procedure.pronargdefaults, "
            "procedure.provariadic, "
            "return_namespace.nspname return_schema, "
            "return_type.typname return_type, procedure.prosecdef, procedure.prosrc, "
            "coalesce(cardinality(procedure.proconfig), 0) config_count, "
            "coalesce(procedure.proconfig[1], '') first_config "
            "FROM pg_proc procedure "
            "JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
            "JOIN pg_roles owner ON owner.oid=procedure.proowner "
            "JOIN pg_language language ON language.oid=procedure.prolang "
            "JOIN pg_type return_type ON return_type.oid=procedure.prorettype "
            "JOIN pg_namespace return_namespace "
            "ON return_namespace.oid=return_type.typnamespace "
            "WHERE procedure.oid=to_regprocedure("
            "'public.trading_bot_cleanup_expired_replay_nonces(timestamptz,integer)'"
            ")"
        )
    ).mappings().one_or_none()
    if row is None or any(
        (
            str(row["function_schema"]) != "public",
            str(row["proname"])
            != "trading_bot_cleanup_expired_replay_nonces",
            str(row["identity_arguments"])
            != "cutoff timestamp with time zone, row_limit integer",
            str(row["function_arguments"])
            != "cutoff timestamp with time zone, row_limit integer",
            str(row["function_result"]) != "TABLE(key_id text, nonce text)",
            str(row["input_argument_types"])
            != "timestamp with time zone, integer",
            str(row["argument_names"]) != "cutoff,row_limit,key_id,nonce",
            str(row["argument_modes"]) != "i,i,t,t",
            str(row["all_argument_types"])
            != "timestamp with time zone,integer,text,text",
            _ident(str(row["function_owner"])) != expected_owner,
            str(row["function_language"]) != "plpgsql",
            str(row["prokind"]) != "f",
            str(row["provolatile"]) != "v",
            str(row["proparallel"]) != "u",
            bool(row["proleakproof"]),
            not bool(row["proretset"]),
            bool(row["proisstrict"]),
            int(row["pronargs"]) != 2,
            int(row["pronargdefaults"]) != 0,
            int(row["provariadic"]) != 0,
            str(row["return_schema"]) != "pg_catalog",
            str(row["return_type"]) != "record",
            not bool(row["prosecdef"]),
            len(str(row["prosrc"]).encode("utf-8"))
            != CLEANUP_FUNCTION_PROSRC_BYTES,
            hashlib.sha256(str(row["prosrc"]).encode("utf-8")).hexdigest()
            != CLEANUP_FUNCTION_PROSRC_SHA256,
            int(row["config_count"]) != 1,
            str(row["first_config"]) != "search_path=public, pg_temp",
        )
    ):
        raise RuntimeError(
            "replay nonce cleanup function does not match the exact release"
        )
    return CLEANUP_FUNCTION_CATALOG_IDENTITY


def _role_state(connection, role: str) -> dict:  # noqa: ANN001
    row = connection.execute(
        text(
            "SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, "
            "rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname = :role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    if row is None:
        raise RuntimeError(f"required database role does not exist: {role}")
    if any(row[key] for key in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")):
        raise RuntimeError(f"runtime database role is over-privileged: {role}")
    if not row["rolcanlogin"] or row["rolinherit"]:
        raise RuntimeError(f"runtime database role must be LOGIN NOINHERIT: {role}")
    membership_paths = connection.execute(
        text(
            "WITH RECURSIVE role_paths AS ("
            " SELECT membership.roleid, membership.member, 1 AS depth"
            " FROM pg_auth_members membership JOIN pg_roles member ON member.oid = membership.member"
            " WHERE member.rolname = :role"
            " UNION ALL"
            " SELECT membership.roleid, membership.member, role_paths.depth + 1"
            " FROM pg_auth_members membership JOIN role_paths ON membership.member = role_paths.roleid"
            " WHERE role_paths.depth < 64"
            ") SELECT DISTINCT parent.rolname FROM role_paths"
            " JOIN pg_roles parent ON parent.oid = role_paths.roleid ORDER BY parent.rolname"
        ),
        {"role": role},
    ).scalars().all()
    if membership_paths:
        raise RuntimeError(
            f"runtime database role has SET ROLE path(s): {role} -> "
            + ",".join(str(item) for item in membership_paths)
        )
    inbound_members = connection.execute(
        text(
            "SELECT member.rolname FROM pg_auth_members membership "
            "JOIN pg_roles parent ON parent.oid = membership.roleid "
            "JOIN pg_roles member ON member.oid = membership.member "
            "WHERE parent.rolname = :role ORDER BY member.rolname"
        ),
        {"role": role},
    ).scalars().all()
    if inbound_members:
        raise RuntimeError(
            f"runtime database role must not be granted to another role: {role} <- "
            + ",".join(str(item) for item in inbound_members)
        )
    owned = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S') AND r.rolname = :role"
            ),
            {"role": role},
        )
        or 0
    )
    if owned:
        raise RuntimeError(f"runtime database role owns public objects: {role}")
    owned_functions = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_proc procedure "
                "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
                "JOIN pg_roles owner ON owner.oid = procedure.proowner "
                "WHERE namespace.nspname = 'public' AND owner.rolname = :role"
            ),
            {"role": role},
        )
        or 0
    )
    if owned_functions:
        raise RuntimeError(f"runtime database role owns public functions: {role}")
    return dict(row)


def _assert_exact_role_closure(
    connection,  # noqa: ANN001
    roles: Iterable[str],
) -> None:
    for role in roles:
        role = _ident(role)
        _role_state(connection, role)
        row = connection.execute(
            text(
                "SELECT role.rolconnlimit, "
                "coalesce(role.rolvaliduntil::text, 'infinity') valid_until, "
                "coalesce(cardinality(role.rolconfig), 0) role_setting_count, "
                "(SELECT count(*) FROM pg_db_role_setting setting "
                " WHERE setting.setrole=role.oid) database_setting_count, "
                "(SELECT count(*) FROM pg_shdepend dependency "
                " WHERE dependency.refclassid='pg_authid'::regclass "
                " AND dependency.refobjid=role.oid "
                " AND dependency.deptype='o') owned_dependency_count, "
                "(SELECT count(*) FROM pg_user_mapping mapping "
                " WHERE mapping.umuser=role.oid) user_mapping_count, "
                "(SELECT count(*) FROM pg_default_acl defaults "
                " WHERE defaults.defaclrole=role.oid) default_acl_owner_count "
                "FROM pg_roles role WHERE role.rolname=:role"
            ),
            {"role": role},
        ).mappings().one()
        if (
            int(row["rolconnlimit"]) != -1
            or str(row["valid_until"]) != "infinity"
            or any(
                int(row[key]) != 0
                for key in (
                    "role_setting_count",
                    "database_setting_count",
                    "owned_dependency_count",
                    "user_mapping_count",
                    "default_acl_owner_count",
                )
            )
        ):
            raise RuntimeError(f"runtime database role is not exactly closed: {role}")


def _database_runtime_state(
    connection,  # noqa: ANN001
    *,
    for_update: bool = True,
) -> dict[str, object]:
    lock_clause = " FOR UPDATE" if for_update else ""
    rows = connection.execute(
        text(
            "SELECT singleton_id, enforcement_enabled, physical_site, "
            "application_role, projection_role, control_role, "
            "require_witness_lease, updated_by, updated_at "
            "FROM public.dr_database_runtime ORDER BY singleton_id"
            + lock_clause
        )
    ).mappings().all()
    if len(rows) != 1:
        raise RuntimeError("database fence singleton is missing or duplicated")
    state = dict(rows[0])
    if (
        tuple(state) != DATABASE_RUNTIME_COLUMNS
        or int(state["singleton_id"]) != 1
        or state["updated_at"] is None
    ):
        raise RuntimeError("database fence singleton has an invalid exact shape")
    return state


def _assert_database_runtime_state(
    connection,  # noqa: ANN001
    *,
    expected: dict[str, object],
    label: str,
    for_update: bool = True,
) -> None:
    if tuple(expected) != DATABASE_RUNTIME_COLUMNS:
        raise RuntimeError("expected database fence state has an invalid exact shape")
    if _database_runtime_state(connection, for_update=for_update) != expected:
        raise RuntimeError(f"dr_database_runtime changed outside the exact {label}")


def _expected_fenced_runtime_state(
    before: dict[str, object],
    *,
    site: str,
    application_role: str,
    projection_role: str,
    control_role: str | None,
    require_witness_lease: bool,
    updated_by: str,
    updated_at: object,
) -> dict[str, object]:
    if tuple(before) != DATABASE_RUNTIME_COLUMNS:
        raise RuntimeError("initial database fence state has an invalid exact shape")
    site = _ident(site)
    application_role = _ident(application_role)
    projection_role = _ident(projection_role)
    if control_role is not None:
        control_role = _ident(control_role)
    expected = dict(before)
    expected.update(
        {
            "singleton_id": 1,
            "enforcement_enabled": True,
            "physical_site": site,
            "application_role": application_role,
            "projection_role": projection_role,
            "control_role": control_role,
            "require_witness_lease": require_witness_lease,
            "updated_by": updated_by,
            "updated_at": updated_at,
        }
    )
    return expected


def _projection_grants(
    connection,  # noqa: ANN001
    projection_role: str,
    *,
    projection_policy: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    projection_role = _ident(projection_role)
    policy = projection_policy or _assert_exact_projection_policy(connection)
    statements: list[str] = []
    for table, columns in policy.items():
        table = _ident(table)
        if table in PROJECTOR_INTERNAL_TABLES:
            continue
        if not columns:
            continue
        column_list = ", ".join(_ident(column) for column in columns)
        statements.extend(
            (
                f"GRANT SELECT ({column_list}) ON TABLE public.{table} TO {projection_role}",
                f"GRANT INSERT ({column_list}) ON TABLE public.{table} TO {projection_role}",
                f"GRANT UPDATE ({column_list}) ON TABLE public.{table} TO {projection_role}",
                f"GRANT DELETE ON TABLE public.{table} TO {projection_role}",
            )
        )
    return statements


def _permissions(value: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in value.split(","))


def _direct_grant_inventory(
    connection,  # noqa: ANN001
    roles: Iterable[str],
) -> set[tuple[str, str, str, str, str, str, bool]]:
    validated_roles = sorted({_ident(role) for role in roles})
    if not validated_roles:
        raise RuntimeError("database grant inventory requires at least one role")
    role_literals = ", ".join(f"'{role}'" for role in validated_roles)
    rows = connection.execute(
        text(
            "SELECT kind, object_schema, object_name, subobject_name, "
            "privilege_type, grantee, is_grantable FROM ("
            " SELECT CASE WHEN target.relkind='S' THEN 'sequence' "
            " ELSE 'table' END kind, namespace.nspname object_schema, "
            " target.relname object_name, ''::text subobject_name, "
            " acl.privilege_type, grantee.rolname grantee, "
            " acl.is_grantable "
            " FROM pg_class target "
            " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            " CROSS JOIN LATERAL aclexplode(target.relacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " AND target.relkind IN ('r','p','v','m','f','S') "
            " UNION ALL "
            " SELECT 'column', namespace.nspname, target.relname, "
            " attribute.attname, acl.privilege_type, grantee.rolname, "
            " acl.is_grantable "
            " FROM pg_attribute attribute "
            " JOIN pg_class target ON target.oid=attribute.attrelid "
            " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            " CROSS JOIN LATERAL aclexplode(attribute.attacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            " UNION ALL "
            " SELECT 'routine', namespace.nspname, "
            " format('%I.%I(%s)', namespace.nspname, procedure.proname, "
            " pg_get_function_identity_arguments(procedure.oid)), '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_proc procedure "
            " JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
            " CROSS JOIN LATERAL aclexplode(procedure.proacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'type', namespace.nspname, type_row.typname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_type type_row "
            " JOIN pg_namespace namespace ON namespace.oid=type_row.typnamespace "
            " CROSS JOIN LATERAL aclexplode(type_row.typacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'language', 'pg_catalog', language.lanname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_language language "
            " CROSS JOIN LATERAL aclexplode(language.lanacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'foreign-data-wrapper', 'pg_catalog', wrapper.fdwname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_foreign_data_wrapper wrapper "
            " CROSS JOIN LATERAL aclexplode(wrapper.fdwacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'foreign-server', 'pg_catalog', server.srvname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_foreign_server server "
            " CROSS JOIN LATERAL aclexplode(server.srvacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'tablespace', 'pg_catalog', tablespace.spcname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_tablespace tablespace "
            " CROSS JOIN LATERAL aclexplode(tablespace.spcacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'large-object', 'pg_catalog', large_object.oid::text, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_largeobject_metadata large_object "
            " CROSS JOIN LATERAL aclexplode(large_object.lomacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'database', '', database_row.datname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_database database_row "
            " CROSS JOIN LATERAL aclexplode(database_row.datacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'schema', namespace.nspname, namespace.nspname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_namespace namespace "
            " CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'parameter', 'pg_catalog', parameter.parname, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_parameter_acl parameter "
            " CROSS JOIN LATERAL aclexplode(parameter.paracl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals}) "
            " UNION ALL "
            " SELECT 'default', coalesce(namespace.nspname,''), "
            " owner.rolname || ':' || defaults.defaclobjtype, '', "
            " acl.privilege_type, grantee.rolname, acl.is_grantable "
            " FROM pg_default_acl defaults "
            " JOIN pg_roles owner ON owner.oid=defaults.defaclrole "
            " LEFT JOIN pg_namespace namespace "
            " ON namespace.oid=defaults.defaclnamespace "
            " CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
            " JOIN pg_roles grantee ON grantee.oid=acl.grantee "
            f" WHERE grantee.rolname IN ({role_literals})"
            ") direct_grants"
        )
    ).all()
    return {
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]).upper(),
            str(row[5]),
            bool(row[6]),
        )
        for row in rows
    }


def _unsafe_public_privilege_count(connection) -> int:  # noqa: ANN001
    return int(
        connection.scalar(
            text(
                "SELECT count(*) FROM ("
                " SELECT 1 FROM pg_class target "
                " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
                " CROSS JOIN LATERAL aclexplode(target.relacl) acl "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " AND target.relkind IN ('r','p','v','m','f','S') "
                " AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_attribute attribute "
                " JOIN pg_class target ON target.oid=attribute.attrelid "
                " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
                " CROSS JOIN LATERAL aclexplode(attribute.attacl) acl "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                " AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_proc procedure "
                " JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(procedure.proacl, "
                " acldefault('f', procedure.proowner))) acl "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_namespace namespace "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(namespace.nspacl, "
                " acldefault('n', namespace.nspowner))) acl "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_type type_row "
                " JOIN pg_namespace namespace "
                " ON namespace.oid=type_row.typnamespace "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(type_row.typacl, "
                " acldefault('T', type_row.typowner))) acl "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " AND type_row.typisdefined AND acl.grantee=0 "
                " AND (namespace.nspname <> 'public' "
                " OR acl.privilege_type <> 'USAGE' OR acl.is_grantable) "
                " UNION ALL "
                " SELECT 1 FROM pg_language language "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(language.lanacl, "
                " acldefault('l', language.lanowner))) acl "
                " WHERE language.lanpltrusted AND acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_largeobject_metadata large_object "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(large_object.lomacl, "
                " acldefault('L', large_object.lomowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_foreign_data_wrapper wrapper "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(wrapper.fdwacl, "
                " acldefault('F', wrapper.fdwowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_foreign_server server "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(server.srvacl, "
                " acldefault('S', server.srvowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_tablespace tablespace "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(tablespace.spcacl, "
                " acldefault('t', tablespace.spcowner))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_database database_row "
                " CROSS JOIN LATERAL aclexplode("
                " coalesce(database_row.datacl, "
                " acldefault('d', database_row.datdba))) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_user_mapping mapping "
                " WHERE mapping.umuser=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_parameter_acl parameter "
                " CROSS JOIN LATERAL aclexplode(parameter.paracl) acl "
                " WHERE acl.grantee=0 "
                " UNION ALL "
                " SELECT 1 FROM pg_default_acl defaults "
                " CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
                " WHERE acl.grantee=0"
                ") unsafe_public"
            )
        )
        or 0
    )


def _assert_exact_database_authorization_closure(
    connection,  # noqa: ANN001
    roles: Iterable[str],
    *,
    require_all_runtime_roles: bool = True,
) -> None:
    current_owner = _ident(str(connection.scalar(text("SELECT current_user"))))
    runtime_roles = {_ident(role) for role in roles}
    expected_custom_roles = {current_owner, *runtime_roles}
    role_rows = connection.execute(
        text(
            "SELECT rolname, rolcanlogin, rolsuper FROM pg_roles "
            "ORDER BY rolname"
        )
    ).mappings().all()
    custom_roles: set[str] = set()
    for row in role_rows:
        role_name = _ident(str(row["rolname"]))
        if role_name.startswith("pg_"):
            if bool(row["rolcanlogin"]) or bool(row["rolsuper"]):
                raise RuntimeError(
                    "predefined PostgreSQL roles must remain NOLOGIN and non-superuser"
                )
            continue
        custom_roles.add(role_name)
        if bool(row["rolsuper"]) and role_name != current_owner:
            raise RuntimeError(
                "database role closure contains an unexpected superuser"
            )
        if bool(row["rolcanlogin"]) != (role_name in expected_custom_roles):
            raise RuntimeError(
                "database role closure contains an unexpected LOGIN state"
            )
    if current_owner not in custom_roles:
        raise RuntimeError("current database owner role is missing")
    if (
        custom_roles - expected_custom_roles
        or (
            require_all_runtime_roles
            and custom_roles != expected_custom_roles
        )
    ):
        raise RuntimeError(
            "database cluster contains an unexpected custom role"
        )

    allowed_grantees = sorted(
        {
            current_owner,
            "pg_database_owner",
            *(_ident(role) for role in roles),
        }
    )
    allowed_literals = ", ".join(f"'{role}'" for role in allowed_grantees)
    unexpected_grants = connection.execute(
        text(
            "SELECT grant_entry.object_kind, count(*) FROM ("
            " SELECT 'relation'::text object_kind, acl.grantee "
            " FROM pg_class target "
            " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            " CROSS JOIN LATERAL aclexplode(target.relacl) acl "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " UNION ALL "
            " SELECT 'column', acl.grantee FROM pg_attribute attribute "
            " JOIN pg_class target ON target.oid=attribute.attrelid "
            " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            " CROSS JOIN LATERAL aclexplode(attribute.attacl) acl "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            " UNION ALL "
            " SELECT 'routine', acl.grantee FROM pg_proc procedure "
            " JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
            " CROSS JOIN LATERAL aclexplode(procedure.proacl) acl "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " UNION ALL "
            " SELECT 'type', acl.grantee FROM pg_type type_row "
            " JOIN pg_namespace namespace ON namespace.oid=type_row.typnamespace "
            " CROSS JOIN LATERAL aclexplode(type_row.typacl) acl "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " AND type_row.typisdefined "
            " UNION ALL "
            " SELECT 'language', acl.grantee FROM pg_language language "
            " CROSS JOIN LATERAL aclexplode(language.lanacl) acl "
            " UNION ALL "
            " SELECT 'large-object', acl.grantee "
            " FROM pg_largeobject_metadata large_object "
            " CROSS JOIN LATERAL aclexplode(large_object.lomacl) acl "
            " UNION ALL "
            " SELECT 'foreign-data-wrapper', acl.grantee "
            " FROM pg_foreign_data_wrapper wrapper "
            " CROSS JOIN LATERAL aclexplode(wrapper.fdwacl) acl "
            " UNION ALL "
            " SELECT 'foreign-server', acl.grantee "
            " FROM pg_foreign_server server "
            " CROSS JOIN LATERAL aclexplode(server.srvacl) acl "
            " UNION ALL "
            " SELECT 'tablespace', acl.grantee FROM pg_tablespace tablespace "
            " CROSS JOIN LATERAL aclexplode(tablespace.spcacl) acl "
            " UNION ALL "
            " SELECT 'database', acl.grantee FROM pg_database database_row "
            " CROSS JOIN LATERAL aclexplode(database_row.datacl) acl "
            " UNION ALL "
            " SELECT 'schema', acl.grantee FROM pg_namespace namespace "
            " CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " UNION ALL "
            " SELECT 'parameter', acl.grantee FROM pg_parameter_acl parameter "
            " CROSS JOIN LATERAL aclexplode(parameter.paracl) acl "
            " UNION ALL "
            " SELECT 'default-acl', acl.grantee FROM pg_default_acl defaults "
            " CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
            " UNION ALL "
            " SELECT 'user-mapping', mapping.umuser FROM pg_user_mapping mapping"
            ") grant_entry "
            "JOIN pg_roles grantee ON grantee.oid=grant_entry.grantee "
            f"WHERE grantee.rolname NOT IN ({allowed_literals}) "
            "GROUP BY grant_entry.object_kind ORDER BY grant_entry.object_kind"
        )
    ).all()
    if unexpected_grants:
        raise RuntimeError(
            "database authorization closure contains an unexpected grantee"
        )

    unexpected_owners = connection.execute(
        text(
            "SELECT owner_entry.object_kind, count(*) FROM ("
            " SELECT 'relation'::text object_kind, target.relowner owner_oid "
            " FROM pg_class target "
            " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " UNION ALL "
            " SELECT 'routine', procedure.proowner FROM pg_proc procedure "
            " JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " UNION ALL "
            " SELECT 'type', type_row.typowner FROM pg_type type_row "
            " JOIN pg_namespace namespace ON namespace.oid=type_row.typnamespace "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " AND type_row.typisdefined "
            " UNION ALL "
            " SELECT 'schema', namespace.nspowner FROM pg_namespace namespace "
            " WHERE namespace.nspname !~ '^pg_' "
            " AND namespace.nspname <> 'information_schema' "
            " UNION ALL "
            " SELECT 'database', database_row.datdba FROM pg_database database_row "
            " WHERE database_row.datname=current_database() "
            " UNION ALL "
            " SELECT 'language', language.lanowner FROM pg_language language "
            " UNION ALL "
            " SELECT 'large-object', large_object.lomowner "
            " FROM pg_largeobject_metadata large_object "
            " UNION ALL "
            " SELECT 'foreign-data-wrapper', wrapper.fdwowner "
            " FROM pg_foreign_data_wrapper wrapper "
            " UNION ALL "
            " SELECT 'foreign-server', server.srvowner "
            " FROM pg_foreign_server server "
            " UNION ALL "
            " SELECT 'tablespace', tablespace.spcowner "
            " FROM pg_tablespace tablespace "
            " UNION ALL "
            " SELECT 'default-acl', defaults.defaclrole "
            " FROM pg_default_acl defaults"
            ") owner_entry "
            "JOIN pg_roles owner ON owner.oid=owner_entry.owner_oid "
            "WHERE owner.rolname<>:current_owner "
            "AND NOT (owner_entry.object_kind='schema' "
            "AND owner.rolname='pg_database_owner') "
            "GROUP BY owner_entry.object_kind ORDER BY owner_entry.object_kind"
        ),
        {"current_owner": current_owner},
    ).all()
    if unexpected_owners:
        raise RuntimeError(
            "database authorization closure contains an unexpected object owner"
        )

    owner_membership_paths = int(
        connection.scalar(
            text(
                "WITH RECURSIVE membership_paths AS ("
                " SELECT membership.member, membership.roleid, 1 depth "
                " FROM pg_auth_members membership "
                " UNION ALL "
                " SELECT path.member, membership.roleid, path.depth + 1 "
                " FROM membership_paths path "
                " JOIN pg_auth_members membership "
                " ON membership.member=path.roleid WHERE path.depth < 64"
                "), object_owners AS ("
                " SELECT target.relowner owner_oid FROM pg_class target "
                " JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " UNION SELECT procedure.proowner FROM pg_proc procedure "
                " JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " UNION SELECT type_row.typowner FROM pg_type type_row "
                " JOIN pg_namespace namespace ON namespace.oid=type_row.typnamespace "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " UNION SELECT namespace.nspowner FROM pg_namespace namespace "
                " WHERE namespace.nspname !~ '^pg_' "
                " AND namespace.nspname <> 'information_schema' "
                " UNION SELECT database_row.datdba FROM pg_database database_row "
                " WHERE database_row.datname=current_database()"
                "), unexpected_login AS ("
                " SELECT role.oid FROM pg_roles role "
                f" WHERE role.rolcanlogin AND role.rolname NOT IN ({allowed_literals})"
                ") SELECT count(*) FROM membership_paths path "
                "JOIN unexpected_login login ON login.oid=path.member "
                "JOIN object_owners owner ON owner.owner_oid=path.roleid"
            )
        )
        or 0
    )
    if owner_membership_paths:
        raise RuntimeError(
            "unexpected LOGIN role has a membership path to an object owner"
        )


def _assert_exact_public_type_usage(connection) -> None:  # noqa: ANN001
    rows = connection.execute(
        text(
            "SELECT type_row.typname, acl.privilege_type, acl.is_grantable "
            "FROM pg_type type_row "
            "JOIN pg_namespace namespace ON namespace.oid=type_row.typnamespace "
            "CROSS JOIN LATERAL aclexplode("
            "coalesce(type_row.typacl, acldefault('T', type_row.typowner))) acl "
            "WHERE namespace.nspname='public' AND type_row.typisdefined "
            "AND acl.grantee=0 ORDER BY type_row.typname, acl.privilege_type"
        )
    ).all()
    actual = {
        (_ident(str(row[0])), str(row[1]).upper(), bool(row[2]))
        for row in rows
    }
    type_names = {
        _ident(str(value))
        for value in connection.execute(
            text(
                "SELECT type_row.typname FROM pg_type type_row "
                "JOIN pg_namespace namespace "
                "ON namespace.oid=type_row.typnamespace "
                "WHERE namespace.nspname='public' AND type_row.typisdefined "
                "ORDER BY type_row.typname"
            )
        ).scalars().all()
    }
    expected = {(type_name, "USAGE", False) for type_name in type_names}
    if actual != expected:
        raise RuntimeError("public type USAGE policy is not installed exactly")


def _assert_exact_grant_inventory(
    *,
    actual: set[tuple[str, str, str, str, str, str, bool]],
    expected: set[tuple[str, str, str, str, str, str, bool]],
    unsafe_public_count: int,
    label: str,
) -> None:
    if actual != expected:
        raise RuntimeError(f"exact release {label} grant policy is not installed")
    if unsafe_public_count != 0:
        raise RuntimeError(f"{label} contains an unsafe PUBLIC privilege")


def _assert_exact_writer_trigger_policy(connection) -> set[str]:  # noqa: ANN001
    expected_owner = _ident(str(connection.scalar(text("SELECT current_user"))))
    rows = connection.execute(
        text(
            "SELECT target.relname, trigger.tgenabled, trigger.tgtype, "
            "trigger.tgqual IS NULL tgqual_is_null, trigger.tgnargs, "
            "encode(trigger.tgargs, 'hex') tgargs_hex, procedure.oid function_oid, "
            "function_namespace.nspname function_schema, procedure.proname, "
            "pg_get_function_identity_arguments(procedure.oid) function_arguments, "
            "owner.rolname function_owner, language.lanname function_language, "
            "procedure.prokind, procedure.provolatile, procedure.proparallel, "
            "procedure.proleakproof, procedure.proretset, procedure.pronargs, "
            "return_namespace.nspname return_schema, "
            "return_type.typname return_type, procedure.prosecdef, procedure.prosrc, "
            "coalesce(cardinality(procedure.proconfig), 0) config_count, "
            "coalesce(procedure.proconfig[1], '') first_config "
            "FROM pg_trigger trigger "
            "JOIN pg_class target ON target.oid=trigger.tgrelid "
            "JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            "JOIN pg_proc procedure ON procedure.oid=trigger.tgfoid "
            "JOIN pg_namespace function_namespace "
            "ON function_namespace.oid=procedure.pronamespace "
            "JOIN pg_roles owner ON owner.oid=procedure.proowner "
            "JOIN pg_language language ON language.oid=procedure.prolang "
            "JOIN pg_type return_type ON return_type.oid=procedure.prorettype "
            "JOIN pg_namespace return_namespace "
            "ON return_namespace.oid=return_type.typnamespace "
            "WHERE namespace.nspname='public' "
            "AND trigger.tgname='trg_three_site_writer_term' "
            "AND NOT trigger.tgisinternal ORDER BY target.relname"
        )
    ).mappings().all()
    table_names = tuple(_ident(str(row["relname"])) for row in rows)
    if (
        table_names != EXPECTED_WRITER_TRIGGER_TABLES
        or len(table_names) != len(set(table_names))
        or len({int(row["function_oid"]) for row in rows}) != 1
        or any(
            str(row["tgenabled"]) != "A"
            or int(row["tgtype"]) != 31
            or not bool(row["tgqual_is_null"])
            or int(row["tgnargs"]) != 0
            or str(row["tgargs_hex"]) != ""
            or str(row["function_schema"]) != "public"
            or str(row["proname"]) != "trading_bot_enforce_writer_term"
            or str(row["function_arguments"]) != ""
            or _ident(str(row["function_owner"])) != expected_owner
            or str(row["function_language"]) != "plpgsql"
            or str(row["prokind"]) != "f"
            or str(row["provolatile"]) != "v"
            or str(row["proparallel"]) != "u"
            or bool(row["proleakproof"])
            or bool(row["proretset"])
            or int(row["pronargs"]) != 0
            or str(row["return_schema"]) != "pg_catalog"
            or str(row["return_type"]) != "trigger"
            or not bool(row["prosecdef"])
            or len(str(row["prosrc"]).encode("utf-8"))
            != WRITER_FUNCTION_PROSRC_BYTES
            or hashlib.sha256(str(row["prosrc"]).encode("utf-8")).hexdigest()
            != WRITER_FUNCTION_PROSRC_SHA256
            or int(row["config_count"]) != 1
            or str(row["first_config"]) != "search_path=public, pg_temp"
            for row in rows
        )
    ):
        raise RuntimeError("exact release database Writer trigger policy is not installed")
    return set(table_names)


def _expected_webapp_grant_inventory(
    connection,  # noqa: ANN001
    *,
    application_role: str,
    projection_role: str,
    control_role: str,
    observer_role: str,
    service_roles: dict[str, str],
    projection_policy: dict[str, tuple[str, ...]] | None = None,
) -> set[tuple[str, str, str, str, str, str, bool]]:
    canonical_projection_policy = (
        projection_policy or _assert_exact_projection_policy(connection)
    )
    expected: set[tuple[str, str, str, str, str, str, bool]] = set()

    def add(
        kind: str,
        object_name: str,
        permissions: str,
        role: str,
        *,
        schema: str = "public",
        subobject: str = "",
    ) -> None:
        expected.update(
            (
                kind,
                schema,
                object_name,
                subobject,
                privilege,
                role,
                False,
            )
            for privilege in _permissions(permissions)
        )

    relations = connection.execute(
        text(
            "SELECT target.relname, target.relkind FROM pg_class target "
            "JOIN pg_namespace namespace ON namespace.oid=target.relnamespace "
            "WHERE namespace.nspname='public' "
            "AND target.relkind IN ('r','p','v','m','f','S') "
            "ORDER BY target.relname"
        )
    ).all()
    table_names = {
        _ident(str(row[0])) for row in relations if str(row[1]) != "S"
    }
    sequence_names = {
        _ident(str(row[0])) for row in relations if str(row[1]) == "S"
    }
    for table_name in table_names:
        add("table", table_name, "SELECT", application_role)
    for sequence_name in sequence_names:
        add("sequence", sequence_name, "USAGE, SELECT", application_role)
        add("sequence", sequence_name, "USAGE, SELECT", projection_role)

    writer_tables = _assert_exact_writer_trigger_policy(connection)
    for table_name in writer_tables - APPLICATION_WRITE_EXCLUDED_TABLES:
        add("table", table_name, "INSERT, UPDATE, DELETE", application_role)

    add(
        "table",
        "dr_database_runtime",
        "SELECT",
        control_role,
    )
    add("table", "dr_durability_state", "SELECT, UPDATE", control_role)
    add("table", "webapp_writer_state", "SELECT, UPDATE", control_role)
    add("table", "webapp_writer_transitions", "SELECT, INSERT", control_role)
    add(
        "table",
        "webapp_writer_activation_operations",
        "SELECT, INSERT, UPDATE",
        control_role,
    )
    for table_name in CONVERGENCE_OBSERVER_TABLES:
        add("table", table_name, "SELECT", observer_role)
    for role in service_roles.values():
        for table_name in (
            "alembic_version",
            "dr_database_runtime",
            "dr_projection_service_roles",
            "dr_durability_state",
            "webapp_writer_state",
        ):
            add("table", table_name, "SELECT", role)
    for table_name, permissions in APPLICATION_INTERNAL_GRANTS.items():
        add("table", table_name, permissions, application_role)
    for scope, grants in DR_SERVICE_INTERNAL_GRANTS.items():
        for table_name, permissions in grants.items():
            add("table", table_name, permissions, service_roles[scope])

    receiver_columns = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='dr_events' "
            "AND column_name <> 'source_xid' ORDER BY ordinal_position"
        )
    ).scalars().all()
    if not receiver_columns:
        raise RuntimeError("dr_events receiver insert columns are missing")
    for column_name in receiver_columns:
        add(
            "column",
            "dr_events",
            "INSERT",
            service_roles["receiver"],
            subobject=_ident(str(column_name)),
        )
    for column_name in (
        "id",
        "content_hash",
        "size",
        "mime_type",
        "created_at",
        "s3_key",
    ):
        add(
            "column",
            "chat_files",
            "SELECT",
            service_roles["blob"],
            subobject=column_name,
        )
    add(
        "column",
        "chat_files",
        "UPDATE",
        service_roles["blob"],
        subobject="s3_key",
    )
    for table_name, columns in canonical_projection_policy.items():
        table_name = _ident(table_name)
        if table_name in PROJECTOR_INTERNAL_TABLES:
            continue
        if not columns:
            continue
        add("table", table_name, "DELETE", projection_role)
        for column_name in columns:
            add(
                "column",
                table_name,
                "SELECT, INSERT, UPDATE",
                projection_role,
                subobject=_ident(column_name),
            )
    cleanup_function = _assert_exact_cleanup_function(connection)
    add(
        "routine",
        cleanup_function,
        "EXECUTE",
        service_roles["projector"],
    )
    database_name = _ident(str(connection.scalar(text("SELECT current_database()"))))
    all_roles = {
        application_role,
        control_role,
        observer_role,
        *service_roles.values(),
    }
    for role in all_roles:
        add("database", database_name, "CONNECT", role, schema="")
        add("schema", "public", "USAGE", role)
    return expected


def _validated_context(
    connection,  # noqa: ANN001
    *,
    site: str,
    application_role: str,
    projection_role: str,
    receiver_role: str,
    delivery_role: str,
    blob_role: str,
    effect_role: str,
    control_role: str,
    observer_role: str,
    operator: str,
) -> tuple[dict[str, str], tuple[str, ...], str]:
    service_roles = {
        "receiver": _ident(receiver_role),
        "delivery": _ident(delivery_role),
        "projector": _ident(projection_role),
        "blob": _ident(blob_role),
        "effect": _ident(effect_role),
    }
    roles = tuple(
        map(
            _ident,
            (
                application_role,
                control_role,
                observer_role,
                *service_roles.values(),
            ),
        )
    )
    if len(set(roles)) != len(roles):
        raise RuntimeError("application, control, and DR service roles must all be distinct")
    if site not in {"webapp_fi", "webapp_ir"}:
        raise RuntimeError("physical site must be webapp_fi or webapp_ir")
    for role in roles:
        _role_state(connection, role)
    if not operator.strip() or len(operator) > 128:
        raise RuntimeError("operator identity is required and must be at most 128 characters")

    database_name = _ident(str(connection.scalar(text("SELECT current_database()"))))
    return service_roles, roles, database_name


def build_grant_statements(
    connection,  # noqa: ANN001
    *,
    site: str,
    application_role: str,
    projection_role: str,
    receiver_role: str,
    delivery_role: str,
    blob_role: str,
    effect_role: str,
    control_role: str,
    observer_role: str,
    operator: str,
) -> list[str]:
    service_roles, roles, database_name = _validated_context(
        connection,
        site=site,
        application_role=application_role,
        projection_role=projection_role,
        receiver_role=receiver_role,
        delivery_role=delivery_role,
        blob_role=blob_role,
        effect_role=effect_role,
        control_role=control_role,
        observer_role=observer_role,
        operator=operator,
    )
    projection_policy = _assert_exact_projection_policy(connection)
    _assert_exact_cleanup_function(connection)
    _assert_exact_database_authorization_closure(connection, roles)
    role_list = ", ".join(roles)
    statements = [
        *(
            f"ALTER ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            "CONNECTION LIMIT -1 VALID UNTIL 'infinity'"
            for role in roles
        ),
        *(f"ALTER ROLE {role} RESET ALL" for role in roles),
        *(
            f"ALTER ROLE {role} IN DATABASE {database_name} RESET ALL"
            for role in roles
        ),
        f"ALTER DATABASE {database_name} RESET session_replication_role",
        f"REVOKE SET, ALTER SYSTEM ON PARAMETER session_replication_role FROM {role_list}",
        *_database_scope_statements(
            connection,
            roles,
            grant_current=True,
        ),
        "REVOKE ALL ON SCHEMA public FROM PUBLIC",
        PUBLIC_TRUSTED_LANGUAGE_REVOKE,
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC",
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_list}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_list}",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, {role_list}",
        f"GRANT USAGE ON SCHEMA public TO {role_list}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {application_role}",
        f"GRANT SELECT ON TABLE public.dr_database_runtime, public.dr_durability_state, public.webapp_writer_state, public.webapp_writer_transitions TO {control_role}",
        "GRANT SELECT ON TABLE "
        + ", ".join(f"public.{table}" for table in sorted(CONVERGENCE_OBSERVER_TABLES))
        + f" TO {_ident(observer_role)}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {application_role}, {projection_role}",
    ]
    for role in service_roles.values():
        statements.append(
            "GRANT SELECT ON TABLE public.alembic_version, public.dr_database_runtime, "
            "public.dr_projection_service_roles, public.dr_durability_state, "
            f"public.webapp_writer_state TO {role}"
        )
    owner_role = _ident(str(connection.scalar(text("SELECT current_user"))))
    statements.extend(
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE ALL ON TABLES FROM PUBLIC, {role_list}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE ALL ON SEQUENCES FROM PUBLIC, {role_list}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE ALL ON TYPES FROM PUBLIC, {role_list}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE ALL ON SCHEMAS FROM PUBLIC, {role_list}",
            # PostgreSQL's built-in PUBLIC EXECUTE default for functions is
            # global. A per-schema REVOKE cannot override that global default.
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, {role_list}",
        )
    )
    writer_tables = connection.execute(
        text(
            "SELECT DISTINCT c.relname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND t.tgname = 'trg_three_site_writer_term' "
            "ORDER BY c.relname"
        )
    ).scalars().all()
    if not writer_tables:
        raise RuntimeError("database has no installed three-site Writer triggers")
    for raw_table in writer_tables:
        table = _ident(str(raw_table))
        if table in APPLICATION_WRITE_EXCLUDED_TABLES:
            # A security trigger is never an authorization source.  Business
            # tables receive application DML here; all control, transport,
            # projection, provider and Bot-local state is granted only by a
            # closed role-specific map below.
            continue
        statements.append(
            f"GRANT INSERT, UPDATE, DELETE ON TABLE public.{table} TO {application_role}"
        )
    for table in CONTROL_TABLES:
        statements.append(
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.{table} "
            f"FROM {application_role}, {', '.join(service_roles.values())}"
        )
    statements.extend(
        (
            f"GRANT UPDATE ON TABLE public.webapp_writer_state TO {control_role}",
            f"GRANT INSERT ON TABLE public.webapp_writer_transitions TO {control_role}",
            f"GRANT SELECT, INSERT, UPDATE ON TABLE public.webapp_writer_activation_operations TO {control_role}",
            f"GRANT UPDATE ON TABLE public.dr_durability_state TO {control_role}",
        )
    )
    for table, permissions in APPLICATION_INTERNAL_GRANTS.items():
        statements.append(f"GRANT {permissions} ON TABLE public.{table} TO {application_role}")
    for scope, grants in DR_SERVICE_INTERNAL_GRANTS.items():
        for table, permissions in grants.items():
            statements.append(
                f"GRANT {permissions} ON TABLE public.{table} TO {service_roles[scope]}"
            )
    receiver_event_columns = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='dr_events' "
            "AND column_name <> 'source_xid' ORDER BY ordinal_position"
        )
    ).scalars().all()
    if not receiver_event_columns:
        raise RuntimeError("dr_events receiver insert columns are missing")
    statements.append(
        "GRANT INSERT ("
        + ", ".join(_ident(str(column)) for column in receiver_event_columns)
        + f") ON TABLE public.dr_events TO {service_roles['receiver']}"
    )
    statements.append(
        "GRANT EXECUTE ON FUNCTION "
        f"{CLEANUP_FUNCTION_GRANT_IDENTITY} "
        f"TO {service_roles['projector']}"
    )
    statements.extend(
        (
            f"GRANT SELECT (id, content_hash, size, mime_type, created_at, s3_key) "
            f"ON TABLE public.chat_files TO {service_roles['blob']}",
            f"GRANT UPDATE (s3_key) ON TABLE public.chat_files TO {service_roles['blob']}",
        )
    )
    statements.extend(
        _projection_grants(
            connection,
            projection_role,
            projection_policy=projection_policy,
        )
    )
    statements.append(
        f"DELETE FROM public.dr_projection_service_roles WHERE physical_site = '{site}'"
    )
    for scope, role in service_roles.items():
        statements.append(
            "INSERT INTO public.dr_projection_service_roles "
            "(physical_site, service_scope, database_role) VALUES "
            f"('{site}', '{scope}', '{role}')"
        )
    _assert_phase_statement_boundary("grants", statements)
    return statements


def _assert_fence_prerequisites(
    connection,  # noqa: ANN001
    *,
    site: str,
    application_role: str,
    control_role: str,
    observer_role: str,
    service_roles: dict[str, str],
) -> None:
    singleton_count = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM public.dr_database_runtime "
                "WHERE singleton_id = 1"
            )
        )
        or 0
    )
    if singleton_count != 1:
        raise RuntimeError("database fence singleton is missing or duplicated")
    _assert_exact_role_closure(
        connection,
        {
            application_role,
            control_role,
            observer_role,
            *service_roles.values(),
        },
    )
    _assert_exact_runtime_database_scope(
        connection,
        {
            application_role,
            control_role,
            observer_role,
            *service_roles.values(),
        },
    )

    mappings = connection.execute(
        text(
            "SELECT service_scope, database_role "
            "FROM public.dr_projection_service_roles "
            "WHERE physical_site = :site ORDER BY service_scope"
        ),
        {"site": site},
    ).mappings().all()
    actual_mappings = {
        _ident(str(row["service_scope"])): _ident(str(row["database_role"]))
        for row in mappings
    }
    if len(mappings) != len(service_roles) or actual_mappings != service_roles:
        raise RuntimeError("database grant policy role mapping is not installed exactly")

    projection_policy = _assert_exact_projection_policy(connection)
    expected_grants = _expected_webapp_grant_inventory(
        connection,
        application_role=application_role,
        projection_role=service_roles["projector"],
        control_role=control_role,
        observer_role=observer_role,
        service_roles=service_roles,
        projection_policy=projection_policy,
    )
    actual_grants = _direct_grant_inventory(
        connection,
        {
            application_role,
            control_role,
            observer_role,
            *service_roles.values(),
        },
    )
    _assert_exact_grant_inventory(
        actual=actual_grants,
        expected=expected_grants,
        unsafe_public_count=_unsafe_public_privilege_count(connection),
        label="database",
    )
    _assert_exact_database_authorization_closure(
        connection,
        {
            application_role,
            control_role,
            observer_role,
            *service_roles.values(),
        },
    )
    _assert_exact_public_type_usage(connection)


def build_fence_statements(
    connection,  # noqa: ANN001
    *,
    site: str,
    application_role: str,
    projection_role: str,
    receiver_role: str,
    delivery_role: str,
    blob_role: str,
    effect_role: str,
    control_role: str,
    observer_role: str,
    operator: str,
) -> list[str]:
    service_roles, _roles, _database_name = _validated_context(
        connection,
        site=site,
        application_role=application_role,
        projection_role=projection_role,
        receiver_role=receiver_role,
        delivery_role=delivery_role,
        blob_role=blob_role,
        effect_role=effect_role,
        control_role=control_role,
        observer_role=observer_role,
        operator=operator,
    )
    _assert_fence_prerequisites(
        connection,
        site=site,
        application_role=application_role,
        control_role=control_role,
        observer_role=observer_role,
        service_roles=service_roles,
    )
    escaped_operator = operator.replace("'", "''")
    statements = [
        "UPDATE public.dr_database_runtime SET "
        f"enforcement_enabled = true, physical_site = '{site}', "
        f"application_role = '{application_role}', projection_role = '{projection_role}', "
        f"control_role = '{control_role}', require_witness_lease = true, "
        f"updated_by = '{escaped_operator}', updated_at = transaction_timestamp() "
        "WHERE singleton_id = 1"
    ]
    _assert_phase_statement_boundary("fence", statements)
    return statements


def _assert_phase_statement_boundary(phase: str, statements: list[str]) -> None:
    runtime_mutations = [
        statement for statement in statements if RUNTIME_MUTATION_RE.search(statement)
    ]
    if phase == "grants" and runtime_mutations:
        raise RuntimeError("grant phase may not mutate dr_database_runtime")
    if phase == "fence" and (
        len(statements) != 1
        or len(runtime_mutations) != 1
        or not statements[0].startswith(
            "UPDATE public.dr_database_runtime SET enforcement_enabled = true, "
        )
        or not statements[0].endswith("WHERE singleton_id = 1")
    ):
        raise RuntimeError("fence phase must contain only the bounded fence update")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=tuple(PHASE_CONFIRMATIONS))
    parser.add_argument("--site", required=True, choices=("webapp_fi", "webapp_ir"))
    parser.add_argument("--application-role", required=True)
    parser.add_argument("--projection-role", required=True)
    parser.add_argument("--receiver-role", required=True)
    parser.add_argument("--delivery-role", required=True)
    parser.add_argument("--blob-role", required=True)
    parser.add_argument("--effect-role", required=True)
    parser.add_argument("--control-role", required=True)
    parser.add_argument("--observer-role", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--database-url-env", default="SYNC_DATABASE_URL")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        parser.error(f"{args.database_url_env} is not set")
    required_confirmation = PHASE_CONFIRMATIONS[args.phase]
    if args.apply and args.confirm != required_confirmation:
        parser.error(f"--apply requires --confirm {required_confirmation}")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            runtime_before = _database_runtime_state(
                connection,
                for_update=args.apply,
            )
            transaction_timestamp = None
            builder = (
                build_grant_statements
                if args.phase == "grants"
                else build_fence_statements
            )
            statements = builder(
                connection,
                site=args.site,
                application_role=args.application_role,
                projection_role=args.projection_role,
                receiver_role=args.receiver_role,
                delivery_role=args.delivery_role,
                blob_role=args.blob_role,
                effect_role=args.effect_role,
                control_role=args.control_role,
                observer_role=args.observer_role,
                operator=args.operator,
            )
            _assert_phase_statement_boundary(args.phase, statements)
            if not args.apply:
                _assert_database_runtime_state(
                    connection,
                    expected=runtime_before,
                    label=f"{args.phase} planning phase",
                    for_update=False,
                )
                connection.rollback()
                result = {
                    "status": "planned",
                    "phase": args.phase,
                    "site": args.site,
                    "roles": {
                        "application": args.application_role,
                        "projection": args.projection_role,
                        "receiver": args.receiver_role,
                        "delivery": args.delivery_role,
                        "blob": args.blob_role,
                        "effect": args.effect_role,
                        "control": args.control_role,
                        "observer": args.observer_role,
                    },
                    "statement_count": len(statements),
                    "required_confirmation": required_confirmation,
                }
            else:
                for statement in statements:
                    cursor = connection.exec_driver_sql(statement)
                    if (
                        args.phase == "fence"
                        and getattr(cursor, "rowcount", None) != 1
                    ):
                        raise RuntimeError(
                            "bounded database fence update did not affect exactly one row"
                        )
                if args.phase == "grants":
                    _assert_fence_prerequisites(
                        connection,
                        site=args.site,
                        application_role=args.application_role,
                        control_role=args.control_role,
                        observer_role=args.observer_role,
                        service_roles={
                            "receiver": args.receiver_role,
                            "delivery": args.delivery_role,
                            "projector": args.projection_role,
                            "blob": args.blob_role,
                            "effect": args.effect_role,
                        },
                    )
                    expected_runtime = runtime_before
                    runtime_label = "grants phase"
                else:
                    transaction_timestamp = connection.scalar(
                        text("SELECT transaction_timestamp()")
                    )
                    if transaction_timestamp is None:
                        raise RuntimeError(
                            "database transaction timestamp is missing"
                        )
                    expected_runtime = _expected_fenced_runtime_state(
                        runtime_before,
                        site=args.site,
                        application_role=args.application_role,
                        projection_role=args.projection_role,
                        control_role=args.control_role,
                        require_witness_lease=True,
                        updated_by=args.operator,
                        updated_at=transaction_timestamp,
                    )
                    runtime_label = "fence phase"
                _assert_database_runtime_state(
                    connection,
                    expected=expected_runtime,
                    label=runtime_label,
                    for_update=True,
                )
                result = {
                    "status": "applied",
                    "phase": args.phase,
                    "site": args.site,
                    "statement_count": len(statements),
                }
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        engine.dispose()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
