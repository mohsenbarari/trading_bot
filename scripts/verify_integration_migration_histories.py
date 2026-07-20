#!/usr/bin/env python3
"""Compare the effective queue/DR database contract across four upgrade paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any

from sqlalchemy import create_engine, text


HISTORY_LABELS = frozenset({"fresh", "main_parent", "queue_parent", "dr_parent"})
DEFAULT_EXPECTED_HEAD = "c431d2e3f5a6"


class MigrationHistoryError(RuntimeError):
    pass


def _strict_object(pairs):  # noqa: ANN001
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationHistoryError(f"duplicate migration-history field: {key}")
        result[key] = value
    return result


def parse_history_urls(raw: str | None) -> dict[str, str]:
    try:
        payload = json.loads(raw or "", object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, MigrationHistoryError) as exc:
        raise MigrationHistoryError("migration-history database URLs are invalid JSON") from exc
    if not isinstance(payload, list) or len(payload) != len(HISTORY_LABELS):
        raise MigrationHistoryError("exactly four migration-history database URLs are required")
    result: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"label", "database_url"}:
            raise MigrationHistoryError("migration-history URL entry fields are invalid")
        label = str(item["label"])
        url = str(item["database_url"])
        if label not in HISTORY_LABELS or label in result or not url.startswith("postgresql"):
            raise MigrationHistoryError("migration-history label/URL is unknown or duplicate")
        result[label] = url
    if set(result) != HISTORY_LABELS:
        raise MigrationHistoryError("migration-history labels are incomplete")
    return result


FINGERPRINT_SQL = text(
    """
    SELECT jsonb_build_object(
      'revision', (SELECT version_num FROM alembic_version),
      'runtime', (
        SELECT jsonb_build_object(
          'physical_site', physical_site,
          'application_role', application_role,
          'projection_role', projection_role,
          'control_role', control_role,
          'enforcement_enabled', enforcement_enabled,
          'require_witness_lease', require_witness_lease
        ) FROM dr_database_runtime WHERE singleton_id=1
      ),
      'table_policy', (
        SELECT COALESCE(jsonb_agg(to_jsonb(item) ORDER BY item.table_name), '[]'::jsonb)
        FROM (SELECT * FROM dr_projection_table_allowlist) item
      ),
      'field_policy', (
        SELECT COALESCE(
          jsonb_agg(to_jsonb(item) ORDER BY item.table_name, item.column_name),
          '[]'::jsonb
        ) FROM (SELECT * FROM dr_projection_field_allowlist) item
      ),
      'service_roles', (
        SELECT COALESCE(
          jsonb_agg(to_jsonb(item) ORDER BY item.physical_site, item.service_scope),
          '[]'::jsonb
        ) FROM (SELECT * FROM dr_projection_service_roles) item
      ),
      'functions', (
        SELECT COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'name', procedure.proname,
              'args', pg_get_function_identity_arguments(procedure.oid),
              'definition', pg_get_functiondef(procedure.oid)
            ) ORDER BY procedure.proname, pg_get_function_identity_arguments(procedure.oid)
          ), '[]'::jsonb
        )
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid=procedure.pronamespace
        WHERE namespace.nspname='public'
          AND (procedure.proname LIKE 'trading_bot_%' OR procedure.proname LIKE 'dr_%')
      ),
      'triggers', (
        SELECT COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'table', relation.relname,
              'name', trigger.tgname,
              'definition', pg_get_triggerdef(trigger.oid, true)
            ) ORDER BY relation.relname, trigger.tgname
          ), '[]'::jsonb
        )
        FROM pg_trigger trigger
        JOIN pg_class relation ON relation.oid=trigger.tgrelid
        JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
        WHERE namespace.nspname='public' AND NOT trigger.tgisinternal
      ),
      'table_grants', (
        SELECT COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'grantee', grantee, 'table', table_name, 'privilege', privilege_type
            ) ORDER BY grantee, table_name, privilege_type
          ), '[]'::jsonb
        )
        FROM information_schema.role_table_grants
        WHERE table_schema='public'
          AND (grantee LIKE 'hist_%' OR grantee LIKE 'webapp\\_%' ESCAPE '\\'
               OR grantee LIKE 'bot\\_%' ESCAPE '\\')
      ),
      'column_grants', (
        SELECT COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'grantee', grantee, 'table', table_name, 'column', column_name,
              'privilege', privilege_type
            ) ORDER BY grantee, table_name, column_name, privilege_type
          ), '[]'::jsonb
        )
        FROM information_schema.role_column_grants
        WHERE table_schema='public'
          AND (grantee LIKE 'hist_%' OR grantee LIKE 'webapp\\_%' ESCAPE '\\'
               OR grantee LIKE 'bot\\_%' ESCAPE '\\')
      )
    )
    """
)


def database_contract(database_url: str, *, expected_head: str) -> dict[str, Any]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if not database_name.startswith(("stage", "test_")):
                raise MigrationHistoryError(
                    "migration-history verification refuses a non-scratch database"
                )
            payload = connection.scalar(FINGERPRINT_SQL)
    finally:
        engine.dispose()
    if not isinstance(payload, dict) or payload.get("revision") != expected_head:
        raise MigrationHistoryError("migration-history database is not at the expected head")
    return payload


def verify_histories(urls: dict[str, str], *, expected_head: str) -> dict[str, Any]:
    fingerprints: dict[str, str] = {}
    baseline: bytes | None = None
    for label in sorted(HISTORY_LABELS):
        payload = database_contract(urls[label], expected_head=expected_head)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        fingerprints[label] = digest
        if baseline is None:
            baseline = canonical
        elif canonical != baseline:
            raise MigrationHistoryError(
                f"effective database policy differs for migration history {label}"
            )
    return {
        "status": "equivalent",
        "expected_head": expected_head,
        "history_count": len(fingerprints),
        "fingerprints": fingerprints,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", default=DEFAULT_EXPECTED_HEAD)
    args = parser.parse_args(argv)
    try:
        result = verify_histories(
            parse_history_urls(os.environ.get("INTEGRATION_HISTORY_DATABASE_URLS_JSON")),
            expected_head=args.expected_head,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
