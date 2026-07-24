#!/usr/bin/env python3
"""Provision production-like Writer-Witness roles in isolated staging.

The bootstrap phase uses only the PostgreSQL initializer identity to create two
closed login roles and transfer database ownership to the migrator.  The
migrate phase runs as that migrator, applies the versioned Witness schema, and
grants the runtime role only the DML surface used by writer_witness_app.py.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url


ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILES = (
    REPO_ROOT / "deploy/writer-witness/001_initial.sql",
    REPO_ROOT / "deploy/writer-witness/002_failover_operation_ledger.sql",
    REPO_ROOT / "deploy/writer-witness/003_human_approval_relay.sql",
)


class StagingWitnessProvisionError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "")
    if not value:
        raise StagingWitnessProvisionError(f"required environment variable is missing: {name}")
    return value


def _role(name: str) -> str:
    value = _required(name)
    if not ROLE_RE.fullmatch(value):
        raise StagingWitnessProvisionError(f"{name} is not a closed PostgreSQL role name")
    return value


def _sync_dsn(name: str) -> str:
    value = _required(name)
    parsed = make_url(value)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg2"}:
        raise StagingWitnessProvisionError(f"{name} must use postgresql/psycopg2")
    if not parsed.database or not parsed.username or not parsed.password:
        raise StagingWitnessProvisionError(f"{name} must include database, username, and password")
    return value


def _clear_memberships(cursor, role: str) -> None:  # noqa: ANN001
    cursor.execute(
        "SELECT parent.rolname, member.rolname FROM pg_auth_members membership "
        "JOIN pg_roles parent ON parent.oid=membership.roleid "
        "JOIN pg_roles member ON member.oid=membership.member "
        "WHERE parent.rolname=%s OR member.rolname=%s",
        (role, role),
    )
    for parent, member in cursor.fetchall():
        cursor.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(str(parent)), sql.Identifier(str(member))
            )
        )


def _upsert_closed_role(cursor, role: str, password: str) -> None:  # noqa: ANN001
    if len(password.encode("utf-8")) < 20:
        raise StagingWitnessProvisionError(f"password for {role} is too short")
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
    verb = "ALTER" if cursor.fetchone() is not None else "CREATE"
    cursor.execute(
        sql.SQL(
            f"{verb} ROLE {{}} LOGIN PASSWORD %s NOINHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(role)),
        (password,),
    )
    _clear_memberships(cursor, role)


def _validate_bootstrap_identity(
    *,
    current_user: str,
    database_owner: str,
    current_user_is_superuser: bool,
    migrator: str,
    runtime: str,
) -> None:
    if current_user in {migrator, runtime}:
        raise StagingWitnessProvisionError(
            "bootstrap, migrator, and runtime identities must be distinct"
        )
    initial_bootstrap = current_user == database_owner
    safe_reentry = current_user_is_superuser and database_owner == migrator
    if not (initial_bootstrap or safe_reentry):
        raise StagingWitnessProvisionError(
            "bootstrap credential must own an unprovisioned Witness database or "
            "be a superuser re-entering a database already owned by the configured migrator"
        )


def bootstrap_roles() -> dict[str, object]:
    owner_dsn = _sync_dsn("WRITER_WITNESS_BOOTSTRAP_DATABASE_URL")
    migrator = _role("WRITER_WITNESS_MIGRATOR_DB_USER")
    runtime = _role("WRITER_WITNESS_RUNTIME_DB_USER")
    if migrator == runtime:
        raise StagingWitnessProvisionError("Witness migrator and runtime roles must be distinct")
    with psycopg2.connect(owner_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, current_database(), "
                "pg_get_userbyid(database_definition.datdba), "
                "COALESCE((SELECT role_definition.rolsuper FROM pg_roles role_definition "
                "WHERE role_definition.rolname=current_user), FALSE) "
                "FROM pg_database database_definition "
                "WHERE database_definition.datname=current_database()"
            )
            owner_user, database, database_owner, owner_is_superuser = cursor.fetchone()
            _validate_bootstrap_identity(
                current_user=str(owner_user),
                database_owner=str(database_owner),
                current_user_is_superuser=bool(owner_is_superuser),
                migrator=migrator,
                runtime=runtime,
            )
            _upsert_closed_role(
                cursor, migrator, _required("WRITER_WITNESS_MIGRATOR_DB_PASSWORD")
            )
            _upsert_closed_role(
                cursor, runtime, _required("WRITER_WITNESS_RUNTIME_DB_PASSWORD")
            )
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(str(database)), sql.Identifier(migrator)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(str(database))
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                    sql.Identifier(str(database)),
                    sql.Identifier(migrator),
                    sql.Identifier(runtime),
                )
            )
            # Some hardened PostgreSQL templates revoke PUBLIC CREATE on the
            # public schema. Database ownership alone does not restore that
            # ACL, so grant it explicitly only to the dedicated migrator.
            cursor.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(migrator)
                )
            )
    return {
        "status": "bootstrapped",
        "database": str(database),
        "migrator_role": migrator,
        "runtime_role": runtime,
    }


def _apply_schema(cursor) -> str:  # noqa: ANN001
    cursor.execute("SELECT to_regclass('public.writer_witness_schema_version')")
    if cursor.fetchone()[0] is None:
        cursor.execute(SCHEMA_FILES[0].read_text(encoding="utf-8"))
    cursor.execute("SELECT version_num FROM writer_witness_schema_version")
    version = str(cursor.fetchone()[0])
    if version == "001":
        cursor.execute(SCHEMA_FILES[1].read_text(encoding="utf-8"))
        version = "002"
    if version == "002":
        cursor.execute(SCHEMA_FILES[2].read_text(encoding="utf-8"))
        version = "003"
    if version != "003":
        raise StagingWitnessProvisionError(
            f"unsupported Writer-Witness schema version: {version}"
        )
    return version


def migrate_and_grant() -> dict[str, object]:
    migrator_dsn = _sync_dsn("WRITER_WITNESS_MIGRATOR_DATABASE_URL")
    migrator = _role("WRITER_WITNESS_MIGRATOR_DB_USER")
    runtime = _role("WRITER_WITNESS_RUNTIME_DB_USER")
    with psycopg2.connect(migrator_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, current_database(), "
                "pg_get_userbyid(database_definition.datdba) "
                "FROM pg_database database_definition "
                "WHERE database_definition.datname=current_database()"
            )
            database_user, database, database_owner = cursor.fetchone()
            if database_user != migrator or database_owner != migrator:
                raise StagingWitnessProvisionError(
                    "Witness migration must run as the dedicated database owner/migrator"
                )
            version = _apply_schema(cursor)
            cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC, {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, {}").format(sql.Identifier(runtime)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(runtime)))
            grants = (
                ("SELECT", "writer_witness_schema_version"),
                ("SELECT, UPDATE", "webapp_writer_witness_state"),
                ("SELECT, INSERT", "webapp_writer_witness_receipts"),
                ("SELECT, INSERT, UPDATE", "dr_failover_operation_ledger"),
                ("SELECT, INSERT", "human_approval_relay_receipts"),
            )
            for privileges, table in grants:
                cursor.execute(
                    sql.SQL(f"GRANT {privileges} ON TABLE public.{{}} TO {{}}").format(
                        sql.Identifier(table), sql.Identifier(runtime)
                    )
                )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "REVOKE ALL ON TABLES FROM {}"
                ).format(sql.Identifier(migrator), sql.Identifier(runtime))
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS "
                    "FROM PUBLIC, {}"
                ).format(sql.Identifier(migrator), sql.Identifier(runtime))
            )
    return {
        "status": "migrated",
        "database": str(database),
        "schema_version": version,
        "migrator_role": migrator,
        "runtime_role": runtime,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("bootstrap", "migrate"))
    args = parser.parse_args()
    try:
        result = bootstrap_roles() if args.phase == "bootstrap" else migrate_and_grant()
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
