#!/usr/bin/env python3
"""Create/rotate closed, non-owner WebApp runtime roles from environment secrets."""

from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import create_engine, text


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
                        ("'ALTER ROLE %I LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'" if exists else "'CREATE ROLE %I LOGIN PASSWORD %L NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'") +
                        ", :role, :password)"
                    ),
                    {"role": role, "password": password},
                )
                connection.exec_driver_sql(str(statement))
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
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    finally:
        engine.dispose()
    print(json.dumps({"status": "applied", "roles": sorted(roles)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
