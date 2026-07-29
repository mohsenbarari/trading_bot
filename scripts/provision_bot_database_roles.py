#!/usr/bin/env python3
"""Apply Bot role grants and fencing as separate dry-run-first phases."""

from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import create_engine, text

if __package__:
    from scripts.activate_three_site_database_fencing import (
        CLEANUP_FUNCTION_GRANT_IDENTITY,
        PUBLIC_TRUSTED_LANGUAGE_REVOKE,
        _assert_database_runtime_state,
        _assert_exact_cleanup_function,
        _assert_exact_database_authorization_closure,
        _assert_exact_role_closure,
        _assert_exact_grant_inventory,
        _assert_exact_projection_policy,
        _assert_exact_public_type_usage,
        _assert_exact_runtime_database_scope,
        _assert_exact_writer_trigger_policy,
        _database_scope_statements,
        _database_runtime_state,
        _direct_grant_inventory,
        _expected_fenced_runtime_state,
        _permissions,
        _unsafe_public_privilege_count,
    )
else:
    from activate_three_site_database_fencing import (  # type: ignore[no-redef]
        CLEANUP_FUNCTION_GRANT_IDENTITY,
        PUBLIC_TRUSTED_LANGUAGE_REVOKE,
        _assert_database_runtime_state,
        _assert_exact_cleanup_function,
        _assert_exact_database_authorization_closure,
        _assert_exact_role_closure,
        _assert_exact_grant_inventory,
        _assert_exact_projection_policy,
        _assert_exact_public_type_usage,
        _assert_exact_runtime_database_scope,
        _assert_exact_writer_trigger_policy,
        _database_scope_statements,
        _database_runtime_state,
        _direct_grant_inventory,
        _expected_fenced_runtime_state,
        _permissions,
        _unsafe_public_privilege_count,
    )


ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ROLE_GRANTS_CONFIRMATION = "APPLY-BOT-DATABASE-ROLE-GRANTS"
FENCE_CONFIRMATION = "ENABLE-BOT-DATABASE-FENCING"
PHASE_CONFIRMATIONS = {
    "roles-grants": ROLE_GRANTS_CONFIRMATION,
    "fence": FENCE_CONFIRMATION,
}
RUNTIME_MUTATION_RE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM|MERGE\s+INTO|"
    r"TRUNCATE(?:\s+TABLE)?|ALTER\s+TABLE|DROP\s+TABLE)\s+"
    r"(?:public\.)?dr_database_runtime\b",
    re.IGNORECASE,
)

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
        "chat_members",
        "chats",
        "market_channel_notice_receipts",
        "sync_apply_watermarks",
        "sync_blocks",
        "telegram_registration_intents",
        "user_counter_event_receipts",
        "user_sessions",
    }
)
# Site-local Telegram execution state is deliberately absent from every DR
# projection route, but the credentialed Bot process still needs explicit
# least-privilege CRUD to enqueue, claim, recover, and retain its durable jobs.
# Keep this closed set separate from product tables so WebApp role provisioning
# can never infer access from a shared migration/trigger list.
BOT_LOCAL_QUEUE_APPLICATION_GRANTS = {
    "telegram_delivery_jobs": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_delivery_provider_outcomes": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_delivery_reconciliation_evidence": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_delivery_runtime_gates": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_delivery_resume_operations": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_delivery_feeder_states": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_scheduled_operations": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_interaction_anchor_states": "SELECT, INSERT, UPDATE, DELETE",
    "telegram_channel_membership_sagas": "SELECT, INSERT, UPDATE, DELETE",
}
SYNC_OBSERVER_TABLES = frozenset(
    {
        "alembic_version",
        "dr_database_runtime",
        "dr_events",
        "dr_event_deliveries",
        "dr_event_receipts",
    }
)
# A convergence observation is a bounded, redacted, read-only audit that is
# intentionally broader than the timing observer.  It needs every synced
# product table plus only the immutable/local DR ledgers required to prove an
# exact source-to-destination checkpoint.  Keep it as a named closed surface;
# do not reuse the application or migration owner credentials for this job.
CONVERGENCE_OBSERVER_TABLES = frozenset(BOT_PRODUCT_TABLES) | SYNC_OBSERVER_TABLES | frozenset(
    {
        "dr_producer_cursors",
        "dr_destination_cursors",
        "dr_stream_checkpoints",
        "dr_conflict_quarantine",
        "dr_blob_manifests",
    }
)
BOT_APPLICATION_INTERNAL_GRANTS = {
    "dr_database_runtime": "SELECT",
    "dr_destination_cursors": "SELECT, INSERT, UPDATE",
    "dr_producer_cursors": "SELECT, INSERT, UPDATE",
    "dr_events": "SELECT, INSERT, UPDATE",
    "dr_event_deliveries": "SELECT, INSERT",
}
BOT_DR_SERVICE_GRANTS = {
    "receiver": {
        "dr_events": "SELECT",
        "dr_event_deliveries": "SELECT, INSERT, UPDATE",
        "dr_event_receipts": "SELECT, INSERT, UPDATE",
        "dr_stream_checkpoints": "SELECT, INSERT, UPDATE",
        "dr_conflict_quarantine": "SELECT, INSERT, UPDATE",
        "dr_replay_nonces": "SELECT, INSERT",
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
        parent_role = _ident(str(row["parent_role"]))
        member_role = _ident(str(row["member_role"]))
        connection.exec_driver_sql(
            _format(
                connection,
                "REVOKE %I FROM %I",
                parent=parent_role,
                member=member_role,
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


def _role_names(prefix: str) -> tuple[str, ...]:
    return (
        f"{prefix}_app",
        f"{prefix}_receiver",
        f"{prefix}_delivery",
        f"{prefix}_projection",
        f"{prefix}_observer",
    )


def _build_role_grant_statements(
    connection,  # noqa: ANN001
    *,
    prefix: str,
) -> list[str]:
    app_role = f"{prefix}_app"
    service_roles = {
        "receiver": f"{prefix}_receiver",
        "delivery": f"{prefix}_delivery",
        "projector": f"{prefix}_projection",
    }
    projection_role = service_roles["projector"]
    observer_role = f"{prefix}_observer"
    database = _ident(str(connection.scalar(text("SELECT current_database()"))))
    owner = _ident(str(connection.scalar(text("SELECT current_user"))))
    role_names = _role_names(prefix)
    role_list = ", ".join(role_names)
    projection_policy = _assert_exact_projection_policy(connection)
    _assert_exact_cleanup_function(connection)
    _assert_exact_writer_trigger_policy(connection)
    _assert_exact_database_authorization_closure(
        connection,
        role_names,
        require_all_runtime_roles=False,
    )
    statements = [
        *(f"ALTER ROLE {role} RESET ALL" for role in role_names),
        *(
            f"ALTER ROLE {role} IN DATABASE {database} RESET ALL"
            for role in role_names
        ),
        f"ALTER DATABASE {database} RESET session_replication_role",
        f"REVOKE SET, ALTER SYSTEM ON PARAMETER session_replication_role FROM {role_list}",
        *_database_scope_statements(
            connection,
            role_names,
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
        # Bot-FI API readiness and the read-only forward-rollback checker both
        # verify the exact migration head through the application role.
        f"GRANT SELECT ON TABLE public.alembic_version TO {app_role}",
        "GRANT SELECT ON TABLE "
        + ", ".join(
            f"public.{table}" for table in sorted(CONVERGENCE_OBSERVER_TABLES)
        )
        + f" TO {observer_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}, {projection_role}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE ALL ON TABLES FROM PUBLIC, {role_list}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE ALL ON SEQUENCES FROM PUBLIC, {role_list}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE ALL ON TYPES FROM PUBLIC, {role_list}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE ALL ON SCHEMAS FROM PUBLIC, {role_list}",
        # Function EXECUTE defaults are global in PostgreSQL; a schema-scoped
        # REVOKE does not remove PUBLIC's global default.
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, {role_list}",
    ]
    for role in service_roles.values():
        statements.append(
            "GRANT SELECT ON TABLE public.alembic_version, public.dr_database_runtime, "
            f"public.dr_projection_service_roles TO {role}"
        )
    for table in sorted(BOT_PRODUCT_TABLES | BOT_LOCAL_APPLICATION_TABLES):
        statements.append(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{_ident(table)} TO {app_role}"
        )
    for table, permissions in sorted(BOT_LOCAL_QUEUE_APPLICATION_GRANTS.items()):
        statements.append(
            f"GRANT {permissions} ON TABLE public.{_ident(table)} TO {app_role}"
        )
    for table, permissions in BOT_APPLICATION_INTERNAL_GRANTS.items():
        statements.append(
            f"GRANT {permissions} ON TABLE public.{_ident(table)} TO {app_role}"
        )
    for scope, grants in BOT_DR_SERVICE_GRANTS.items():
        for table, permissions in grants.items():
            statements.append(
                f"GRANT {permissions} ON TABLE public.{_ident(table)} "
                f"TO {service_roles[scope]}"
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
    if not BOT_PRODUCT_TABLES.issubset(projection_policy):
        raise RuntimeError("Bot product projection policy is incomplete")
    for table_name in sorted(BOT_PRODUCT_TABLES):
        columns = projection_policy[table_name]
        if not columns:
            continue
        column_list = ", ".join(_ident(str(column)) for column in columns)
        statements.extend(
            (
                f"GRANT SELECT ({column_list}) ON TABLE public.{table_name} TO {projection_role}",
                f"GRANT INSERT ({column_list}) ON TABLE public.{table_name} TO {projection_role}",
                f"GRANT UPDATE ({column_list}) ON TABLE public.{table_name} TO {projection_role}",
                f"GRANT DELETE ON TABLE public.{table_name} TO {projection_role}",
            )
        )
    statements.append(
        "DELETE FROM public.dr_projection_service_roles WHERE physical_site='bot_fi'"
    )
    for scope, role in service_roles.items():
        statements.append(
            "INSERT INTO public.dr_projection_service_roles "
            "(physical_site, service_scope, database_role) VALUES "
            f"('bot_fi', '{scope}', '{role}')"
        )
    if any(RUNTIME_MUTATION_RE.search(statement) for statement in statements):
        raise RuntimeError("Bot roles-grants phase may not mutate dr_database_runtime")
    return statements


def _execute_role_grants(
    connection,  # noqa: ANN001
    *,
    prefix: str,
    roles: dict[str, str],
    statements: list[str] | None = None,
) -> int:
    if set(roles) != set(_role_names(prefix)) or any(
        not password for password in roles.values()
    ):
        raise RuntimeError("Bot role password material is incomplete")
    grant_statements = statements or _build_role_grant_statements(
        connection,
        prefix=prefix,
    )
    for role, password in roles.items():
        exists = bool(
            connection.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname=:role"),
                {"role": role},
            )
        )
        verb = "ALTER" if exists else "CREATE"
        command = connection.scalar(
            text(
                "SELECT format('"
                + verb
                + " ROLE %I LOGIN PASSWORD %L "
                "NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1 "
                "VALID UNTIL ''infinity''', :role, :password)"
            ),
            {"role": role, "password": password},
        )
        connection.exec_driver_sql(str(command))
        _clear_memberships(connection, role)
        _assert_closed_role(connection, role)
    for statement in grant_statements:
        connection.exec_driver_sql(statement)
    _assert_fence_prerequisites(connection, prefix=prefix)
    return len(grant_statements)


def _expected_bot_grant_inventory(
    connection,  # noqa: ANN001
    *,
    prefix: str,
    projection_policy: dict[str, tuple[str, ...]] | None = None,
) -> set[tuple[str, str, str, str, str, str, bool]]:
    app_role = f"{prefix}_app"
    projection_role = f"{prefix}_projection"
    observer_role = f"{prefix}_observer"
    service_roles = {
        "receiver": f"{prefix}_receiver",
        "delivery": f"{prefix}_delivery",
        "projector": projection_role,
    }
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
    for row in relations:
        relation_name = _ident(str(row[0]))
        if str(row[1]) == "S":
            add("sequence", relation_name, "USAGE, SELECT", app_role)
            add("sequence", relation_name, "USAGE, SELECT", projection_role)

    add("table", "alembic_version", "SELECT", app_role)
    for table_name in CONVERGENCE_OBSERVER_TABLES:
        add("table", table_name, "SELECT", observer_role)
    for role in service_roles.values():
        for table_name in (
            "alembic_version",
            "dr_database_runtime",
            "dr_projection_service_roles",
        ):
            add("table", table_name, "SELECT", role)
    for table_name in BOT_PRODUCT_TABLES | BOT_LOCAL_APPLICATION_TABLES:
        add("table", table_name, "SELECT, INSERT, UPDATE, DELETE", app_role)
    for table_name, permissions in BOT_LOCAL_QUEUE_APPLICATION_GRANTS.items():
        add("table", table_name, permissions, app_role)
    for table_name, permissions in BOT_APPLICATION_INTERNAL_GRANTS.items():
        add("table", table_name, permissions, app_role)
    for scope, grants in BOT_DR_SERVICE_GRANTS.items():
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
    for raw_column_name in receiver_columns:
        add(
            "column",
            "dr_events",
            "INSERT",
            service_roles["receiver"],
            subobject=_ident(str(raw_column_name)),
        )
    if not BOT_PRODUCT_TABLES.issubset(canonical_projection_policy):
        raise RuntimeError("Bot product projection policy is incomplete")
    for table_name in sorted(BOT_PRODUCT_TABLES):
        columns = canonical_projection_policy[table_name]
        if not columns:
            continue
        add("table", table_name, "DELETE", projection_role)
        for raw_column_name in columns:
            add(
                "column",
                table_name,
                "SELECT, INSERT, UPDATE",
                projection_role,
                subobject=_ident(str(raw_column_name)),
            )
    cleanup_function = _assert_exact_cleanup_function(connection)
    add(
        "routine",
        cleanup_function,
        "EXECUTE",
        projection_role,
    )
    database = _ident(str(connection.scalar(text("SELECT current_database()"))))
    for role in _role_names(prefix):
        add("database", database, "CONNECT", role, schema="")
        add("schema", "public", "USAGE", role)
    return expected


def _assert_fence_prerequisites(connection, *, prefix: str) -> None:  # noqa: ANN001
    roles = _role_names(prefix)
    _assert_exact_role_closure(connection, roles)
    _assert_exact_runtime_database_scope(connection, roles)
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
        raise RuntimeError("Bot database fence singleton is missing or duplicated")
    expected_mappings = {
        "receiver": f"{prefix}_receiver",
        "delivery": f"{prefix}_delivery",
        "projector": f"{prefix}_projection",
    }
    rows = connection.execute(
        text(
            "SELECT service_scope, database_role "
            "FROM public.dr_projection_service_roles "
            "WHERE physical_site = 'bot_fi' ORDER BY service_scope"
        )
    ).mappings().all()
    actual_mappings = {
        _ident(str(row["service_scope"])): _ident(str(row["database_role"]))
        for row in rows
    }
    if len(rows) != len(expected_mappings) or actual_mappings != expected_mappings:
        raise RuntimeError("Bot database grant policy role mapping is not installed exactly")
    _assert_exact_writer_trigger_policy(connection)
    projection_policy = _assert_exact_projection_policy(connection)
    expected_grants = _expected_bot_grant_inventory(
        connection,
        prefix=prefix,
        projection_policy=projection_policy,
    )
    actual_grants = _direct_grant_inventory(connection, roles)
    _assert_exact_grant_inventory(
        actual=actual_grants,
        expected=expected_grants,
        unsafe_public_count=_unsafe_public_privilege_count(connection),
        label="Bot database",
    )
    _assert_exact_database_authorization_closure(connection, roles)
    _assert_exact_public_type_usage(connection)


def _build_fence_statements(prefix: str) -> list[str]:
    statements = [
        "UPDATE public.dr_database_runtime SET enforcement_enabled=true, "
        "physical_site='bot_fi', application_role='"
        + f"{prefix}_app"
        + "', projection_role='"
        + f"{prefix}_projection"
        + "', control_role=NULL, require_witness_lease=false, "
        "updated_by='provision_bot_database_roles:fence', "
        "updated_at=transaction_timestamp() WHERE singleton_id=1"
    ]
    if (
        len(statements) != 1
        or not RUNTIME_MUTATION_RE.search(statements[0])
        or not statements[0].endswith("WHERE singleton_id=1")
    ):
        raise RuntimeError(
            "Bot fence phase must contain only the bounded fence update"
        )
    return statements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=tuple(PHASE_CONFIRMATIONS))
    parser.add_argument("--role-prefix", default="bot_fi")
    parser.add_argument("--database-url-env", default="SYNC_DATABASE_URL")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    prefix = _ident(args.role_prefix)
    if prefix != "bot_fi":
        parser.error("--role-prefix must be exactly bot_fi")
    required_confirmation = PHASE_CONFIRMATIONS[args.phase]
    if args.apply and args.confirm != required_confirmation:
        parser.error(f"--apply requires --confirm {required_confirmation}")
    role_names = _role_names(prefix)
    passwords = (
        {
            f"{prefix}_app": _required("BOT_APP_DB_PASSWORD"),
            f"{prefix}_receiver": _required("BOT_RECEIVER_DB_PASSWORD"),
            f"{prefix}_delivery": _required("BOT_DELIVERY_DB_PASSWORD"),
            f"{prefix}_projection": _required("BOT_PROJECTION_DB_PASSWORD"),
            f"{prefix}_observer": _required("BOT_OBSERVER_DB_PASSWORD"),
        }
        if args.phase == "roles-grants" and args.apply
        else {}
    )
    engine = create_engine(_required(args.database_url_env))
    try:
        with engine.begin() as connection:
            runtime_before = _database_runtime_state(
                connection,
                for_update=args.apply,
            )
            transaction_timestamp = None
            if args.phase == "roles-grants":
                statements = _build_role_grant_statements(
                    connection,
                    prefix=prefix,
                )
                statement_count = len(statements)
                if args.apply:
                    statement_count = _execute_role_grants(
                        connection,
                        prefix=prefix,
                        roles=passwords,
                        statements=statements,
                    )
            else:
                _assert_fence_prerequisites(connection, prefix=prefix)
                statements = _build_fence_statements(prefix)
                statement_count = len(statements)
                if args.apply:
                    transaction_timestamp = connection.scalar(
                        text("SELECT transaction_timestamp()")
                    )
                    if transaction_timestamp is None:
                        raise RuntimeError(
                            "database transaction timestamp is missing"
                        )
                    cursor = connection.exec_driver_sql(statements[0])
                    if getattr(cursor, "rowcount", None) != 1:
                        raise RuntimeError(
                            "bounded Bot fence update did not affect exactly one row"
                        )
            expected_runtime = (
                runtime_before
                if args.phase == "roles-grants" or not args.apply
                else _expected_fenced_runtime_state(
                    runtime_before,
                    site="bot_fi",
                    application_role=f"{prefix}_app",
                    projection_role=f"{prefix}_projection",
                    control_role=None,
                    require_witness_lease=False,
                    updated_by="provision_bot_database_roles:fence",
                    updated_at=transaction_timestamp,
                )
            )
            _assert_database_runtime_state(
                connection,
                expected=expected_runtime,
                label=f"Bot {args.phase} phase",
                for_update=args.apply,
            )
            if not args.apply:
                connection.rollback()
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "status": "applied" if args.apply else "planned",
                "phase": args.phase,
                "roles": sorted(role_names),
                "statement_count": statement_count,
                "required_confirmation": required_confirmation,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
