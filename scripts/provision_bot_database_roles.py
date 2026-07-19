#!/usr/bin/env python3
"""Create least-privilege Bot application/projection roles after migrations."""

from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import create_engine, text


ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Bot-FI may project only the cross-authority product contract.  WebApp-private
# Messenger/session/push rows are intentionally absent even though the common
# schema contains their tables.
BOT_PRODUCT_TABLES = frozenset(
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
        "telegram_admin_broadcast_receipts",
        "telegram_admin_broadcasts",
        "telegram_link_tokens",
        "telegram_notification_outbox",
        "trade_delivery_receipts",
        "trades",
        "trading_settings",
        "user_blocks",
        "user_notification_preferences",
        "users",
    }
)
BOT_LOCAL_APPLICATION_TABLES = frozenset(
    {
        "change_log",
        "market_channel_notice_receipts",
        "sync_apply_watermarks",
        "sync_blocks",
        "telegram_registration_intents",
        "user_counter_event_receipts",
    }
)
BOT_APPLICATION_INTERNAL_GRANTS = {
    "dr_database_runtime": "SELECT",
    "dr_destination_cursors": "SELECT, INSERT, UPDATE",
    "dr_producer_cursors": "SELECT, INSERT, UPDATE",
    "dr_events": "SELECT, INSERT, UPDATE",
    "dr_event_deliveries": "SELECT, INSERT",
}
BOT_PROJECTION_INTERNAL_GRANTS = {
    "dr_database_runtime": "SELECT",
    "dr_events": "SELECT, INSERT",
    "dr_event_deliveries": "SELECT, INSERT, UPDATE",
    "dr_event_receipts": "SELECT, INSERT, UPDATE",
    "dr_stream_checkpoints": "SELECT, INSERT, UPDATE",
    "dr_projection_versions": "SELECT, INSERT, UPDATE",
    "dr_conflict_quarantine": "SELECT, INSERT, UPDATE",
    "dr_replay_nonces": "SELECT, INSERT",
}


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "")
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def _ident(value: str) -> str:
    if not ROLE_RE.fullmatch(value):
        raise RuntimeError("database role names must be lowercase PostgreSQL identifiers")
    return value


def _format(connection, template: str, **values) -> str:  # noqa: ANN001
    return str(connection.scalar(text(f"SELECT format('{template}', " + ", ".join(f":{key}" for key in values) + ")"), values))


def _clear_memberships(connection, role: str) -> None:  # noqa: ANN001
    rows = connection.execute(
        text(
            "SELECT parent.rolname parent_role, member.rolname member_role "
            "FROM pg_auth_members membership "
            "JOIN pg_roles parent ON parent.oid = membership.roleid "
            "JOIN pg_roles member ON member.oid = membership.member "
            "WHERE parent.rolname = :role OR member.rolname = :role"
        ),
        {"role": role},
    ).mappings().all()
    for row in rows:
        connection.exec_driver_sql(
            _format(
                connection,
                "REVOKE %I FROM %I",
                parent=row["parent_role"],
                member=row["member_role"],
            )
        )


def _assert_closed_role(connection, role: str) -> None:  # noqa: ANN001
    row = connection.execute(
        text(
            "SELECT rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, "
            "rolreplication, rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one()
    if (
        not row["rolcanlogin"]
        or row["rolinherit"]
        or any(
            row[key]
            for key in (
                "rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls"
            )
        )
    ):
        raise RuntimeError(f"Bot runtime role is not LOGIN NOINHERIT and unprivileged: {role}")
    memberships = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_auth_members membership "
                "JOIN pg_roles parent ON parent.oid=membership.roleid "
                "JOIN pg_roles member ON member.oid=membership.member "
                "WHERE parent.rolname=:role OR member.rolname=:role"
            ),
            {"role": role},
        )
        or 0
    )
    if memberships:
        raise RuntimeError(f"Bot runtime role still has a SET ROLE path: {role}")
    owned_objects = int(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_class object "
                "JOIN pg_namespace namespace ON namespace.oid=object.relnamespace "
                "JOIN pg_roles owner ON owner.oid=object.relowner "
                "WHERE namespace.nspname='public' AND owner.rolname=:role"
            ),
            {"role": role},
        )
        or 0
    )
    if owned_objects:
        raise RuntimeError(f"Bot runtime role owns public database objects: {role}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role-prefix", default="bot_fi")
    parser.add_argument("--database-url-env", default="SYNC_DATABASE_URL")
    args = parser.parse_args()
    prefix = _ident(args.role_prefix)
    roles = {
        f"{prefix}_app": _required("BOT_APP_DB_PASSWORD"),
        f"{prefix}_projection": _required("BOT_PROJECTION_DB_PASSWORD"),
    }
    app_role, projection_role = roles
    engine = create_engine(_required(args.database_url_env))
    try:
        with engine.begin() as connection:
            database = _ident(str(connection.scalar(text("SELECT current_database()"))))
            owner = _ident(str(connection.scalar(text("SELECT current_user"))))
            for role, password in roles.items():
                exists = bool(connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}))
                verb = "ALTER" if exists else "CREATE"
                command = connection.scalar(
                    text(
                        "SELECT format('" + verb + " ROLE %I "
                        + ("LOGIN PASSWORD %L " if exists else "LOGIN PASSWORD %L ")
                        + "NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', :role, :password)"
                    ),
                    {"role": role, "password": password},
                )
                connection.exec_driver_sql(str(command))
                _clear_memberships(connection, role)
                _assert_closed_role(connection, role)
            statements = [
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app_role}, {projection_role}",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {app_role}, {projection_role}",
                f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, {app_role}, {projection_role}",
                f"GRANT CONNECT ON DATABASE {database} TO {app_role}, {projection_role}",
                f"GRANT USAGE ON SCHEMA public TO {app_role}, {projection_role}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}, {projection_role}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public REVOKE ALL ON TABLES FROM {app_role}, {projection_role}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {app_role}, {projection_role}",
                # Function EXECUTE defaults are global in PostgreSQL; a
                # schema-scoped REVOKE does not remove PUBLIC's global default.
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, {app_role}, {projection_role}",
            ]
            for table in sorted(BOT_PRODUCT_TABLES | BOT_LOCAL_APPLICATION_TABLES):
                statements.append(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{_ident(table)} TO {app_role}"
                )
            for table, permissions in BOT_APPLICATION_INTERNAL_GRANTS.items():
                statements.append(
                    f"GRANT {permissions} ON TABLE public.{_ident(table)} TO {app_role}"
                )
            for table, permissions in BOT_PROJECTION_INTERNAL_GRANTS.items():
                statements.append(
                    f"GRANT {permissions} ON TABLE public.{_ident(table)} TO {projection_role}"
                )
            tables = connection.execute(
                text(
                    "SELECT table_name FROM dr_projection_table_allowlist "
                    "WHERE table_name = ANY(:tables) ORDER BY table_name"
                ),
                {"tables": sorted(BOT_PRODUCT_TABLES)},
            ).scalars().all()
            if set(map(str, tables)) != set(BOT_PRODUCT_TABLES):
                raise RuntimeError("Bot product projection policy is incomplete")
            for table in tables:
                columns = connection.execute(
                    text("SELECT column_name FROM dr_projection_field_allowlist WHERE table_name=:table ORDER BY column_name"),
                    {"table": table},
                ).scalars().all()
                if not columns:
                    continue
                column_list = ", ".join(_ident(str(column)) for column in columns)
                table_name = _ident(str(table))
                statements.extend(
                    (
                        f"GRANT SELECT ON TABLE public.{table_name} TO {projection_role}",
                        f"GRANT INSERT ({column_list}) ON TABLE public.{table_name} TO {projection_role}",
                        f"GRANT UPDATE ({column_list}) ON TABLE public.{table_name} TO {projection_role}",
                        f"GRANT DELETE ON TABLE public.{table_name} TO {projection_role}",
                    )
                )
            statements.append(
                "UPDATE public.dr_database_runtime SET enforcement_enabled=true, "
                "physical_site='bot_fi', application_role='"
                + app_role
                + "', projection_role='"
                + projection_role
                + "', control_role=NULL, require_witness_lease=false, "
                "updated_by='provision_bot_database_roles', updated_at=clock_timestamp() "
                "WHERE singleton_id=1"
            )
            for statement in statements:
                connection.exec_driver_sql(statement)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        engine.dispose()
    print(json.dumps({"status": "applied", "roles": sorted(roles)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
