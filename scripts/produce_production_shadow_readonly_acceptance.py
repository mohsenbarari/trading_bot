#!/usr/bin/env python3
"""Produce redacted read-only acceptance evidence inside an exact app image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any
from uuid import UUID

import psycopg2

from scripts.wa_ir_production_operation import (
    DATABASE_FINGERPRINT_ALGORITHM,
    DATABASE_FINGERPRINT_CLIENT_ENCODING,
    DATABASE_FINGERPRINT_SESSION_SETTINGS,
    StreamDigest,
    _fingerprint_from_streams,
)


SCHEMA = "production-shadow-readonly-acceptance-v1"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")
ROLE_RE = re.compile(r"^(bot_fi|webapp_fi)$")
FORBIDDEN_PROVIDER_ENV = frozenset(
    {
        "BOT_TOKEN",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
        "SMSIR_API_KEY",
        "WEB_PUSH_VAPID_PRIVATE_KEY",
        "DR_BLOB_S3_CREDENTIALS_FILE",
        "DR_BLOB_ENCRYPTION_KEYRING_FILE",
    }
)


class ReadonlyAcceptanceError(RuntimeError):
    """A redacted fail-closed acceptance error."""


class _DigestWriter:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self.bytes = 0
        self.records = 0
        self.last_byte: int | None = None

    def write(self, payload: bytes | str) -> int:
        if isinstance(payload, str):
            encoded = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            encoded = payload
        else:
            raise ReadonlyAcceptanceError(
                "database COPY returned an invalid payload"
            )
        self._digest.update(encoded)
        self.bytes += len(encoded)
        self.records += encoded.count(b"\n")
        if encoded:
            self.last_byte = encoded[-1]
        return len(payload)

    def result(self) -> StreamDigest:
        if self.bytes and self.last_byte != ord("\n"):
            raise ReadonlyAcceptanceError(
                "database COPY returned a truncated record"
            )
        return StreamDigest(
            sha256=self._digest.hexdigest(),
            bytes=self.bytes,
            records=self.records,
        )


def _operation_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ReadonlyAcceptanceError("operation id is invalid") from exc
    if str(parsed) != value:
        raise ReadonlyAcceptanceError("operation id is not canonical")
    return value


def _database_url() -> str:
    value = os.environ.get("SYNC_DATABASE_URL", "")
    if not value:
        raise ReadonlyAcceptanceError("SYNC_DATABASE_URL is required")
    return value.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://",
        "postgresql://",
    )


def _provider_environment_is_empty() -> bool:
    return all(not os.environ.get(name) for name in FORBIDDEN_PROVIDER_ENV)


def collect_acceptance(
    *,
    operation_id: str,
    role: str,
    release_sha: str,
    expected_revision: str,
) -> dict[str, Any]:
    operation_id = _operation_id(operation_id)
    if not ROLE_RE.fullmatch(role):
        raise ReadonlyAcceptanceError("role is invalid")
    if not SHA40_RE.fullmatch(release_sha):
        raise ReadonlyAcceptanceError("release SHA is invalid")
    if not REVISION_RE.fullmatch(expected_revision):
        raise ReadonlyAcceptanceError("migration revision is invalid")
    if (
        os.environ.get("RELEASE_SHA") != release_sha
        or os.environ.get("PHYSICAL_SITE") != role
        or os.environ.get("BACKGROUND_JOBS_ENABLED", "").lower() != "false"
        or not _provider_environment_is_empty()
    ):
        raise ReadonlyAcceptanceError(
            "runtime environment is not provider-free and read-only"
        )

    session_settings = " ".join(
        f"-c {name}={value}"
        for name, value in sorted(DATABASE_FINGERPRINT_SESSION_SETTINGS.items())
    )
    try:
        connection = psycopg2.connect(
            _database_url(),
            connect_timeout=10,
            application_name=f"production-shadow-readonly-{operation_id}",
            options=session_settings,
        )
    except Exception as exc:
        raise ReadonlyAcceptanceError(
            "read-only database connection failed"
        ) from exc

    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('transaction_read_only'), "
                "current_setting('default_transaction_read_only'), "
                "current_user"
            )
            transaction_read_only, default_read_only, database_role = (
                cursor.fetchone()
            )
            if (
                transaction_read_only != "on"
                or default_read_only != "on"
                or database_role != f"{role}_observer"
            ):
                raise ReadonlyAcceptanceError(
                    "database session or observer role is not read-only"
                )
            cursor.execute(
                "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
                "rolbypassrls, rolcanlogin "
                "FROM pg_roles WHERE rolname=current_user"
            )
            attributes = cursor.fetchone()
            if attributes != (False, False, False, False, False, True):
                raise ReadonlyAcceptanceError(
                    "observer database role attributes are unsafe"
                )
            cursor.execute("SELECT version_num FROM alembic_version")
            row = cursor.fetchone()
            if row != (expected_revision,):
                raise ReadonlyAcceptanceError(
                    "migration revision differs from the accepted release"
                )
            cursor.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' ORDER BY tablename"
            )
            tables = [str(row[0]) for row in cursor.fetchall()]
            if not tables or tables != sorted(set(tables)):
                raise ReadonlyAcceptanceError(
                    "database table inventory is invalid"
                )

            def stream_copy(sql: str) -> StreamDigest:
                writer = _DigestWriter()
                cursor.copy_expert(sql, writer)
                return writer.result()

            fingerprint, row_count, table_count = _fingerprint_from_streams(
                tables,
                stream_copy,
            )
        connection.rollback()
    except ReadonlyAcceptanceError:
        connection.rollback()
        raise
    except Exception as exc:
        connection.rollback()
        raise ReadonlyAcceptanceError(
            "read-only acceptance query failed"
        ) from exc
    finally:
        connection.close()

    if (
        not SHA256_RE.fullmatch(fingerprint)
        or not 0 <= row_count <= 10**15
        or not 1 <= table_count <= 100_000
    ):
        raise ReadonlyAcceptanceError(
            "database fingerprint evidence is invalid"
        )
    return {
        "schema": SCHEMA,
        "status": "read-only-accepted",
        "operation_id": operation_id,
        "role": role,
        "release_sha": release_sha,
        "migration_revision": expected_revision,
        "database_role": f"{role}_observer",
        "transaction_read_only": True,
        "default_transaction_read_only": True,
        "background_jobs_enabled": False,
        "provider_credentials_present": False,
        "business_write_attempted": False,
        "database_fingerprint_algorithm": DATABASE_FINGERPRINT_ALGORITHM,
        "database_fingerprint_client_encoding": (
            DATABASE_FINGERPRINT_CLIENT_ENCODING
        ),
        "database_fingerprint_sha256": fingerprint,
        "database_row_count": row_count,
        "database_table_count": table_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args(argv)
    try:
        result = collect_acceptance(
            operation_id=args.operation_id,
            role=args.role,
            release_sha=args.release_sha,
            expected_revision=args.expected_revision,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, ReadonlyAcceptanceError)
            else "read-only acceptance failed closed"
        )
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": message,
                    "error_class": "ReadonlyAcceptanceError",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
