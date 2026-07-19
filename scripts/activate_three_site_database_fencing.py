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
    "webapp_writer_state",
    "webapp_writer_transitions",
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
PROJECTION_INTERNAL_GRANTS = {
    "dr_blob_manifests": "SELECT, INSERT, UPDATE",
    "dr_blob_deliveries": "SELECT, UPDATE",
    "dr_blob_receipts": "SELECT, INSERT, UPDATE",
}


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
    control_role: str,
    operator: str,
) -> list[str]:
    roles = tuple(map(_ident, (application_role, projection_role, control_role)))
    if len(set(roles)) != 3:
        raise RuntimeError("application, projection, and control roles must be distinct")
    if site not in {"webapp_fi", "webapp_ir"}:
        raise RuntimeError("physical site must be webapp_fi or webapp_ir")
    for role in roles:
        _role_state(connection, role)
    if not operator.strip() or len(operator) > 128:
        raise RuntimeError("operator identity is required and must be at most 128 characters")

    statements = [
        f"ALTER ROLE {application_role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
        f"ALTER ROLE {projection_role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
        f"ALTER ROLE {control_role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {application_role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {projection_role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {control_role}",
        f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, {application_role}, {projection_role}, {control_role}",
        f"GRANT CONNECT ON DATABASE {_ident(str(connection.scalar(text('SELECT current_database()'))))} TO {application_role}, {projection_role}, {control_role}",
        f"GRANT USAGE ON SCHEMA public TO {application_role}, {projection_role}, {control_role}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {application_role}, {projection_role}",
        f"GRANT SELECT ON TABLE public.dr_database_runtime, public.dr_durability_state, public.webapp_writer_state, public.webapp_writer_transitions TO {control_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {application_role}, {projection_role}",
    ]
    owner_role = _ident(str(connection.scalar(text("SELECT current_user"))))
    statements.extend(
        (
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public REVOKE ALL ON TABLES FROM {application_role}, {projection_role}, {control_role}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {application_role}, {projection_role}, {control_role}",
            # PostgreSQL's built-in PUBLIC EXECUTE default for functions is
            # global. A per-schema REVOKE cannot override that global default.
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, {application_role}, {projection_role}, {control_role}",
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
            f"{application_role}, {projection_role}, {control_role}"
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
        statements.append(
            f"GRANT INSERT, UPDATE, DELETE ON TABLE public.{table} TO {application_role}"
        )
    for table in CONTROL_TABLES:
        statements.append(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.{table} FROM {application_role}, {projection_role}")
    statements.extend(
        (
            f"GRANT UPDATE ON TABLE public.webapp_writer_state TO {control_role}",
            f"GRANT INSERT ON TABLE public.webapp_writer_transitions TO {control_role}",
            f"GRANT UPDATE ON TABLE public.dr_durability_state TO {control_role}",
        )
    )
    for table, permissions in APPLICATION_INTERNAL_GRANTS.items():
        statements.append(f"GRANT {permissions} ON TABLE public.{table} TO {application_role}")
    for table, permissions in PROJECTION_INTERNAL_GRANTS.items():
        statements.append(f"GRANT {permissions} ON TABLE public.{table} TO {projection_role}")
    statements.extend(_projection_grants(connection, projection_role))
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
    parser.add_argument("--control-role", required=True)
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
                control_role=args.control_role,
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
                        "control": args.control_role,
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
