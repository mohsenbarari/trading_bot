#!/usr/bin/env python3
"""Create/rotate closed, non-owner WebApp runtime roles from environment secrets."""

from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import create_engine, text

if __package__:
    from scripts.activate_three_site_database_fencing import (
        PUBLIC_TRUSTED_LANGUAGE_REVOKE,
        _assert_exact_database_authorization_closure,
        _assert_exact_public_type_usage,
        _assert_exact_role_closure,
        _assert_exact_runtime_database_scope,
        _cluster_database_rows,
        _database_scope_statements,
        _direct_grant_inventory,
        _unsafe_public_privilege_count,
    )
else:
    from activate_three_site_database_fencing import (  # type: ignore[no-redef]
        PUBLIC_TRUSTED_LANGUAGE_REVOKE,
        _assert_exact_database_authorization_closure,
        _assert_exact_public_type_usage,
        _assert_exact_role_closure,
        _assert_exact_runtime_database_scope,
        _cluster_database_rows,
        _database_scope_statements,
        _direct_grant_inventory,
        _unsafe_public_privilege_count,
    )


ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "")
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url-env", default="SYNC_DATABASE_URL")
    parser.add_argument("--role-prefix", required=True)
    args = parser.parse_args()
    if not ROLE_RE.fullmatch(args.role_prefix):
        parser.error("--role-prefix must be a lowercase PostgreSQL identifier")
    database_url = _required(args.database_url_env)
    roles = {
        f"{args.role_prefix}_app": _required("THREE_SITE_APP_DB_PASSWORD"),
        f"{args.role_prefix}_receiver": _required("THREE_SITE_RECEIVER_DB_PASSWORD"),
        f"{args.role_prefix}_delivery": _required("THREE_SITE_DELIVERY_DB_PASSWORD"),
        f"{args.role_prefix}_projection": _required("THREE_SITE_PROJECTION_DB_PASSWORD"),
        f"{args.role_prefix}_blob": _required("THREE_SITE_BLOB_DB_PASSWORD"),
        f"{args.role_prefix}_effect": _required("THREE_SITE_EFFECT_DB_PASSWORD"),
        f"{args.role_prefix}_control": _required("THREE_SITE_CONTROL_DB_PASSWORD"),
        f"{args.role_prefix}_observer": _required("THREE_SITE_OBSERVER_DB_PASSWORD"),
    }
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for role, password in roles.items():
                exists = bool(
                    connection.scalar(
                        text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                        {"role": role},
                    )
                )
                statement = connection.scalar(
                    text(
                        "SELECT format(" +
                        ("'ALTER ROLE %I LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1 VALID UNTIL ''infinity'''" if exists else "'CREATE ROLE %I LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1 VALID UNTIL ''infinity'''") +
                        ", :role, :password)"
                    ),
                    {"role": role, "password": password},
                )
                connection.exec_driver_sql(str(statement))
                connection.exec_driver_sql(f"ALTER ROLE {role} RESET ALL")
                _current_database, database_rows = _cluster_database_rows(
                    connection
                )
                for database_name, _allow_connections in database_rows:
                    connection.exec_driver_sql(
                        f"ALTER ROLE {role} IN DATABASE "
                        f"{database_name} RESET ALL"
                    )
                # PostgreSQL NOINHERIT prevents automatic privilege inheritance,
                # but membership still authorizes SET ROLE.  Runtime identities
                # therefore have no role memberships in either direction.
                memberships = connection.execute(
                    text(
                        "SELECT parent.rolname AS parent_role, member.rolname AS member_role "
                        "FROM pg_auth_members membership "
                        "JOIN pg_roles parent ON parent.oid = membership.roleid "
                        "JOIN pg_roles member ON member.oid = membership.member "
                        "WHERE parent.rolname = :role OR member.rolname = :role"
                    ),
                    {"role": role},
                ).mappings().all()
                for membership in memberships:
                    revoke = connection.scalar(
                        text("SELECT format('REVOKE %I FROM %I', :parent, :member)"),
                        {
                            "parent": membership["parent_role"],
                            "member": membership["member_role"],
                        },
                    )
                    connection.exec_driver_sql(str(revoke))
            for statement in _database_scope_statements(
                connection,
                roles,
                grant_current=False,
            ):
                connection.exec_driver_sql(statement)
            role_list = ", ".join(sorted(roles))
            for statement in (
                "REVOKE ALL ON SCHEMA public FROM PUBLIC",
                PUBLIC_TRUSTED_LANGUAGE_REVOKE,
                "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC",
                "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
                "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC",
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_list}",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_list}",
                f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {role_list}",
            ):
                connection.exec_driver_sql(statement)

            _assert_exact_role_closure(connection, roles)
            _assert_exact_runtime_database_scope(
                connection,
                roles,
                grant_current=False,
            )
            if _direct_grant_inventory(connection, roles):
                raise RuntimeError(
                    "pre-migration runtime roles retain direct grants"
                )
            if _unsafe_public_privilege_count(connection):
                raise RuntimeError(
                    "pre-migration database retains unsafe PUBLIC privileges"
                )
            _assert_exact_public_type_usage(connection)
            _assert_exact_database_authorization_closure(
                connection,
                roles,
            )
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        engine.dispose()
    print(json.dumps({"status": "applied", "roles": sorted(roles)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
