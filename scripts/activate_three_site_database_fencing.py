#!/usr/bin/env python3
"""Dry-run-first database role/grant activation for strict WebApp fencing."""

from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import create_engine, text


ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
CONFIRMATION = "ENABLE-THREE-SITE-DATABASE-FENCING"
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


def _ident(value: str) -> str:
    if not ROLE_RE.fullmatch(value):
        raise RuntimeError("database role names must be unquoted lowercase PostgreSQL identifiers")
    return value


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


def _projection_grants(connection, projection_role: str) -> list[str]:  # noqa: ANN001
    tables = connection.execute(
        text("SELECT table_name FROM dr_projection_table_allowlist ORDER BY table_name")
    ).scalars().all()
    statements: list[str] = []
    for table_name in tables:
        table = str(table_name)
        if table in PROJECTOR_INTERNAL_TABLES:
            continue
        columns = connection.execute(
            text(
                "SELECT column_name FROM dr_projection_field_allowlist "
                "WHERE table_name = :table_name ORDER BY column_name"
            ),
            {"table_name": table},
        ).scalars().all()
        if not columns:
            continue
        column_list = ", ".join(str(column) for column in columns)
        statements.extend(
            (
                f"GRANT SELECT ({column_list}) ON TABLE public.{table} TO {projection_role}",
                f"GRANT INSERT ({column_list}) ON TABLE public.{table} TO {projection_role}",
                f"GRANT UPDATE ({column_list}) ON TABLE public.{table} TO {projection_role}",
                f"GRANT DELETE ON TABLE public.{table} TO {projection_role}",
            )
        )
    return statements


def build_statements(
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

    role_list = ", ".join(roles)
    database_name = _ident(str(connection.scalar(text("SELECT current_database()"))))
    statements = [
        *(
            f"ALTER ROLE {role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            for role in roles
        ),
        *(f"ALTER ROLE {role} RESET ALL" for role in roles),
        *(f"ALTER ROLE {role} IN DATABASE {database_name} RESET session_replication_role" for role in roles),
        f"ALTER DATABASE {database_name} RESET session_replication_role",
        f"REVOKE SET, ALTER SYSTEM ON PARAMETER session_replication_role FROM {role_list}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_list}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_list}",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, {role_list}",
        f"GRANT CONNECT ON DATABASE {database_name} TO {role_list}",
        f"GRANT USAGE ON SCHEMA public TO {role_list}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {application_role}",
        f"GRANT SELECT ON TABLE public.dr_database_runtime, public.dr_durability_state, public.webapp_writer_state, public.webapp_writer_transitions TO {control_role}",
        "GRANT SELECT ON TABLE "
        + ", ".join(f"public.{table}" for table in sorted(SYNC_OBSERVER_TABLES))
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
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public REVOKE ALL ON TABLES FROM {role_list}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role_list}",
            # PostgreSQL's built-in PUBLIC EXECUTE default for functions is
            # global. A per-schema REVOKE cannot override that global default.
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, {role_list}",
        )
    )
    security_definer_functions = connection.execute(
        text(
            "SELECT format('%I.%I(%s)', namespace.nspname, procedure.proname, "
            "pg_get_function_identity_arguments(procedure.oid)) "
            "FROM pg_proc procedure JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
            "WHERE namespace.nspname = 'public' AND procedure.prosecdef ORDER BY procedure.oid"
        )
    ).scalars().all()
    for function_identity in security_definer_functions:
        statements.append(
            f"REVOKE EXECUTE ON FUNCTION {function_identity} FROM PUBLIC, "
            f"{role_list}"
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
    for table in writer_tables:
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
        "public.trading_bot_cleanup_expired_replay_nonces(timestamptz, integer) "
        f"TO {service_roles['projector']}"
    )
    statements.extend(
        (
            f"GRANT SELECT (id, content_hash, size, mime_type, created_at, s3_key) "
            f"ON TABLE public.chat_files TO {service_roles['blob']}",
            f"GRANT UPDATE (s3_key) ON TABLE public.chat_files TO {service_roles['blob']}",
        )
    )
    statements.extend(_projection_grants(connection, projection_role))
    statements.append(
        f"DELETE FROM public.dr_projection_service_roles WHERE physical_site = '{site}'"
    )
    for scope, role in service_roles.items():
        statements.append(
            "INSERT INTO public.dr_projection_service_roles "
            "(physical_site, service_scope, database_role) VALUES "
            f"('{site}', '{scope}', '{role}')"
        )
    escaped_operator = operator.replace("'", "''")
    statements.append(
        "UPDATE public.dr_database_runtime SET "
        f"enforcement_enabled = true, physical_site = '{site}', "
        f"application_role = '{application_role}', projection_role = '{projection_role}', "
        f"control_role = '{control_role}', require_witness_lease = true, "
        f"updated_by = '{escaped_operator}', updated_at = clock_timestamp() WHERE singleton_id = 1"
    )
    return statements


def main() -> int:
    parser = argparse.ArgumentParser()
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
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        parser.error(f"{args.database_url_env} is not set")
    if args.apply and args.confirm != CONFIRMATION:
        parser.error(f"--apply requires --confirm {CONFIRMATION}")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            statements = build_statements(
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
            if not args.apply:
                connection.rollback()
                result = {
                    "status": "planned",
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
                    "required_confirmation": CONFIRMATION,
                }
            else:
                for statement in statements:
                    connection.exec_driver_sql(statement)
                result = {"status": "applied", "site": args.site, "statement_count": len(statements)}
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        engine.dispose()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
