"""reconcile deployed queue and coin-intelligence schema history

Revision ID: fb1c2d3e4f5a
Revises: fa0b1c2d3e4f
Create Date: 2026-08-12 12:00:00.000000

The deployed combined line already treated the coin-intelligence chain as an
ancestor of ``fa0b1c2d3e4f``.  A later queue-only line reused revision
``f9c8d7e6a5b4`` with a different parent, so a database carrying ``fa0...`` may
contain either the complete coin schema or none of it.  This revision repairs
only the all-absent case and refuses partial or drifted schemas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fb1c2d3e4f5a"
down_revision: Union[str, Sequence[str], None] = "fa0b1c2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "coin_intelligence_market_outbox",
    "coin_intelligence_inference_audits",
    "coin_intelligence_inference_outcomes",
)

_COLUMN_SPECS: Mapping[str, Mapping[str, tuple[str, int | None, bool | None, bool]]] = {
    "coin_intelligence_market_outbox": {
        "id": ("INTEGER", None, None, False),
        "idempotency_key": ("VARCHAR", 64, None, False),
        "event_kind": ("VARCHAR", 32, None, False),
        "subject_kind": ("VARCHAR", 16, None, False),
        "subject_id": ("INTEGER", None, None, False),
        "occurred_at_utc": ("TIMESTAMP", None, True, False),
        "payload": ("JSON", None, None, False),
        "status": ("VARCHAR", 16, None, False),
        "attempts": ("INTEGER", None, None, False),
        "available_at_utc": ("TIMESTAMP", None, True, False),
        "lease_token": ("VARCHAR", 64, None, True),
        "lease_expires_at_utc": ("TIMESTAMP", None, True, True),
        "last_error_code": ("VARCHAR", 96, None, True),
        "completed_at_utc": ("TIMESTAMP", None, True, True),
        "model_eligible": ("BOOLEAN", None, None, False),
        "created_at": ("TIMESTAMP", None, True, False),
    },
    "coin_intelligence_inference_audits": {
        "id": ("INTEGER", None, None, False),
        "decision_key": ("VARCHAR", 64, None, False),
        "source_surface": ("VARCHAR", 16, None, False),
        "decision_status": ("VARCHAR", 16, None, False),
        "reason_code": ("VARCHAR", 96, None, True),
        "settlement_term": ("VARCHAR", 16, None, False),
        "candidate_scope": ("VARCHAR", 16, None, False),
        "submitted_project_price": ("INTEGER", None, None, False),
        "candidate_count": ("INTEGER", None, None, False),
        "selected_commodity_id": ("INTEGER", None, None, True),
        "selected_commodity_code": ("VARCHAR", 32, None, True),
        "selected_commodity_name": ("VARCHAR", 96, None, True),
        "inference_version": ("VARCHAR", 64, None, False),
        "catalog_resolution_version": ("VARCHAR", 64, None, False),
        "snapshot_receipt": ("VARCHAR", 64, None, True),
        "snapshot_generated_at_utc": ("TIMESTAMP", None, True, True),
        "created_at": ("TIMESTAMP", None, True, False),
        "dominant_underlying_source": ("VARCHAR", 64, None, True),
        "market_regime": ("VARCHAR", 16, None, False),
    },
    "coin_intelligence_inference_outcomes": {
        "id": ("INTEGER", None, None, False),
        "outcome_key": ("VARCHAR", 64, None, False),
        "decision_key": ("VARCHAR", 64, None, False),
        "source_surface": ("VARCHAR", 16, None, False),
        "outcome_kind": ("VARCHAR", 32, None, False),
        "selected_commodity_id": ("INTEGER", None, None, False),
        "selected_commodity_code": ("VARCHAR", 32, None, False),
        "selected_commodity_name": ("VARCHAR", 96, None, False),
        "created_at": ("TIMESTAMP", None, True, False),
    },
}

_REQUIRED_INDEXES: Mapping[str, frozenset[str]] = {
    "coin_intelligence_market_outbox": frozenset(
        {
            "ix_coin_intelligence_market_outbox_claim",
            "ix_coin_intelligence_market_outbox_subject",
        }
    ),
    "coin_intelligence_inference_audits": frozenset(
        {"ix_coin_intelligence_inference_audit_created_status"}
    ),
    "coin_intelligence_inference_outcomes": frozenset(
        {
            "ix_coin_infer_outcome_created_surface",
            "ix_coin_infer_outcome_decision_key",
        }
    ),
}

_REQUIRED_UNIQUES: Mapping[str, frozenset[str]] = {
    "coin_intelligence_market_outbox": frozenset(
        {"uq_coin_intelligence_market_outbox_idempotency_key"}
    ),
    "coin_intelligence_inference_audits": frozenset(
        {"uq_coin_intelligence_inference_audit_decision_key"}
    ),
    "coin_intelligence_inference_outcomes": frozenset(
        {"uq_coin_infer_outcome_key"}
    ),
}

_REQUIRED_CHECKS: Mapping[str, frozenset[str]] = {
    "coin_intelligence_market_outbox": frozenset(
        {
            "ck_coin_intelligence_market_outbox_event_kind",
            "ck_coin_intelligence_market_outbox_status",
            "ck_coin_intelligence_market_outbox_attempts",
        }
    ),
    "coin_intelligence_inference_audits": frozenset(
        {
            "ck_coin_intelligence_inference_audit_source_surface",
            "ck_coin_intelligence_inference_audit_decision_status",
            "ck_coin_intelligence_inference_audit_settlement_term",
            "ck_coin_intelligence_inference_audit_candidate_scope",
            "ck_coin_intelligence_inference_audit_price_positive",
            "ck_coin_intelligence_inference_audit_candidate_count",
            "ck_coin_infer_audit_selected_commodity_positive",
            "ck_coin_intelligence_inference_audit_decision_shape",
            "ck_coin_infer_audit_market_regime",
        }
    ),
    "coin_intelligence_inference_outcomes": frozenset(
        {
            "ck_coin_infer_outcome_source_surface",
            "ck_coin_infer_outcome_kind",
            "ck_coin_infer_outcome_selected_positive",
        }
    ),
}

_REQUIRED_FOREIGN_KEYS: Mapping[str, frozenset[str]] = {
    "coin_intelligence_market_outbox": frozenset(),
    "coin_intelligence_inference_audits": frozenset(),
    "coin_intelligence_inference_outcomes": frozenset(
        {"fk_coin_infer_outcome_decision"}
    ),
}

_REQUIRED_TRIGGERS: Mapping[str, frozenset[str]] = {
    "coin_intelligence_market_outbox": frozenset(),
    "coin_intelligence_inference_audits": frozenset(
        {"trg_coin_intelligence_inference_audit_immutable"}
    ),
    "coin_intelligence_inference_outcomes": frozenset(
        {"trg_coin_intelligence_inference_outcome_immutable"}
    ),
}


def _names(rows: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(str(row["name"]) for row in rows if row.get("name"))


def _require_names(*, table: str, kind: str, actual: frozenset[str], expected: frozenset[str]) -> None:
    missing = expected - actual
    if missing:
        raise RuntimeError(
            f"coin schema reconciliation refused: {table} missing {kind}: "
            + ", ".join(sorted(missing))
        )


def _validate_columns(inspector: sa.Inspector, table: str) -> None:
    actual = {str(column["name"]): column for column in inspector.get_columns(table)}
    expected = _COLUMN_SPECS[table]
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise RuntimeError(
            f"coin schema reconciliation refused: {table} column set mismatch "
            f"(missing={missing}, extra={extra})"
        )

    for name, (type_name, length, timezone, nullable) in expected.items():
        column = actual[name]
        reflected_type = column["type"]
        actual_type_name = type(reflected_type).__name__.upper()
        actual_length = getattr(reflected_type, "length", None)
        actual_timezone = getattr(reflected_type, "timezone", None)
        if (
            actual_type_name != type_name
            or actual_length != length
            or (timezone is not None and actual_timezone is not timezone)
            or bool(column["nullable"]) is not nullable
        ):
            raise RuntimeError(
                f"coin schema reconciliation refused: {table}.{name} definition mismatch"
            )


def _trigger_names(bind: sa.Connection, table: str) -> frozenset[str]:
    rows = bind.execute(
        sa.text(
            """
            SELECT trigger.tgname
            FROM pg_trigger AS trigger
            JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = :table_name
              AND NOT trigger.tgisinternal
            """
        ),
        {"table_name": table},
    )
    return frozenset(str(row[0]) for row in rows)


def _validate_schema(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    for table in _TABLES:
        _validate_columns(inspector, table)
        _require_names(
            table=table,
            kind="indexes",
            actual=_names(inspector.get_indexes(table)),
            expected=_REQUIRED_INDEXES[table],
        )
        _require_names(
            table=table,
            kind="unique constraints",
            actual=_names(inspector.get_unique_constraints(table)),
            expected=_REQUIRED_UNIQUES[table],
        )
        _require_names(
            table=table,
            kind="check constraints",
            actual=_names(inspector.get_check_constraints(table)),
            expected=_REQUIRED_CHECKS[table],
        )
        _require_names(
            table=table,
            kind="foreign keys",
            actual=_names(inspector.get_foreign_keys(table)),
            expected=_REQUIRED_FOREIGN_KEYS[table],
        )
        _require_names(
            table=table,
            kind="triggers",
            actual=_trigger_names(bind, table),
            expected=_REQUIRED_TRIGGERS[table],
        )


def _create_complete_coin_schema() -> None:
    # Reuse the immutable historical DDL in its original order.  This path is
    # reached only for a fa0 database on which all three coin tables are absent.
    from migrations.versions import b2d4e6f8a0c2_add_coin_intelligence_market_outbox
    from migrations.versions import d3f7a1c9e4b5_add_coin_intelligence_inference_audit
    from migrations.versions import d4e8a2b6c1f0_add_coin_intelligence_inference_outcomes
    from migrations.versions import e5a1c4d7b2f9_add_coin_inference_audit_market_context

    b2d4e6f8a0c2_add_coin_intelligence_market_outbox.upgrade()
    d3f7a1c9e4b5_add_coin_intelligence_inference_audit.upgrade()
    d4e8a2b6c1f0_add_coin_intelligence_inference_outcomes.upgrade()
    e5a1c4d7b2f9_add_coin_inference_audit_market_context.upgrade()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = frozenset(_TABLES) & frozenset(inspector.get_table_names())
    if present and present != frozenset(_TABLES):
        missing = sorted(frozenset(_TABLES) - present)
        raise RuntimeError(
            "coin schema reconciliation refused: partial schema; missing "
            + ", ".join(missing)
        )
    if not present:
        _create_complete_coin_schema()
    _validate_schema(bind)


def downgrade() -> None:
    raise RuntimeError(
        "coin schema reconciliation downgrade is intentionally blocked; "
        "the revision may have repaired a queue-only fa0 database"
    )
