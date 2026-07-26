"""Add local coin-intelligence Shadow evaluation ledger.

Revision ID: ca17d8e9f0a2
Revises: b986c7d8e0f1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ca17d8e9f0a2"
down_revision = "b986c7d8e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coin_intelligence_shadow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "mode",
            sa.String(length=16),
            server_default="shadow",
            nullable=False,
        ),
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source_surface", sa.String(length=32), nullable=False),
        sa.Column("subject_kind", sa.String(length=24), nullable=True),
        sa.Column("subject_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "as_of_utc",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("physical_site", sa.String(length=32), nullable=True),
        sa.Column("primary_version", sa.String(length=160), nullable=True),
        sa.Column("candidate_version", sa.String(length=160), nullable=True),
        sa.Column(
            "feature_schema_version",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("snapshot_version", sa.String(length=255), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column(
            "training_eligible",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode = 'shadow'",
            name="ck_coin_intelligence_shadow_runs_mode",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_coin_intelligence_shadow_runs_latency",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_coin_intelligence_shadow_runs_idempotency_key",
        ),
    )
    op.create_index(
        "ix_coin_intelligence_shadow_runs_component_time",
        "coin_intelligence_shadow_runs",
        ["component", "as_of_utc"],
    )
    op.create_index(
        "ix_coin_intelligence_shadow_runs_subject",
        "coin_intelligence_shadow_runs",
        ["subject_kind", "subject_fingerprint", "as_of_utc"],
    )

    op.create_table(
        "coin_intelligence_shadow_feature_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=160), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("source_ages", sa.JSON(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("source_vintages", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["coin_intelligence_shadow_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            name="uq_coin_intelligence_shadow_feature_snapshots_run_id",
        ),
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_feature_snapshots_run_id"),
        "coin_intelligence_shadow_feature_snapshots",
        ["run_id"],
    )

    op.create_table(
        "coin_intelligence_shadow_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("model_role", sa.String(length=24), nullable=False),
        sa.Column("candidate_name", sa.String(length=64), nullable=False),
        sa.Column("commodity_id", sa.Integer(), nullable=True),
        sa.Column("canonical_commodity", sa.String(length=80), nullable=True),
        sa.Column("settlement", sa.String(length=16), nullable=False),
        sa.Column("trade_form", sa.String(length=16), nullable=False),
        sa.Column("center_project_price", sa.BigInteger(), nullable=True),
        sa.Column("lower_project_price", sa.BigInteger(), nullable=True),
        sa.Column("upper_project_price", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_label", sa.String(length=24), nullable=True),
        sa.Column("method", sa.String(length=160), nullable=True),
        sa.Column("gate_reason", sa.String(length=160), nullable=True),
        sa.Column("anchor_kind", sa.String(length=64), nullable=True),
        sa.Column("anchor_age_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "is_authoritative",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "lower_project_price IS NULL OR center_project_price IS NULL "
            "OR lower_project_price <= center_project_price",
            name="ck_coin_intelligence_shadow_predictions_lower",
        ),
        sa.CheckConstraint(
            "upper_project_price IS NULL OR center_project_price IS NULL "
            "OR upper_project_price >= center_project_price",
            name="ck_coin_intelligence_shadow_predictions_upper",
        ),
        sa.CheckConstraint(
            "center_project_price IS NULL OR center_project_price > 0",
            name="ck_coin_intelligence_shadow_predictions_center_positive",
        ),
        sa.CheckConstraint(
            "lower_project_price IS NULL OR lower_project_price > 0",
            name="ck_coin_intelligence_shadow_predictions_lower_positive",
        ),
        sa.CheckConstraint(
            "upper_project_price IS NULL OR upper_project_price > 0",
            name="ck_coin_intelligence_shadow_predictions_upper_positive",
        ),
        sa.CheckConstraint(
            "is_authoritative = false",
            name="ck_coin_intelligence_shadow_predictions_non_authoritative",
        ),
        sa.ForeignKeyConstraint(
            ["commodity_id"],
            ["commodities.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["coin_intelligence_shadow_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "model_role",
            "candidate_name",
            name="uq_coin_intelligence_shadow_predictions_role",
        ),
    )
    op.create_index(
        "ix_coin_intelligence_shadow_predictions_market",
        "coin_intelligence_shadow_predictions",
        ["canonical_commodity", "settlement", "trade_form"],
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_predictions_run_id"),
        "coin_intelligence_shadow_predictions",
        ["run_id"],
    )

    op.create_table(
        "coin_intelligence_shadow_parser_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("current_commodity", sa.String(length=80), nullable=False),
        sa.Column("candidate_commodity", sa.String(length=80), nullable=True),
        sa.Column("agrees_with_current", sa.Boolean(), nullable=True),
        sa.Column("requires_user_confirmation", sa.Boolean(), nullable=False),
        sa.Column("decision_reason", sa.String(length=160), nullable=False),
        sa.Column("validator_status", sa.String(length=40), nullable=False),
        sa.Column("disagreement_fields", sa.JSON(), nullable=False),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["coin_intelligence_shadow_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            name="uq_coin_intelligence_shadow_parser_results_run_id",
        ),
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_parser_results_run_id"),
        "coin_intelligence_shadow_parser_results",
        ["run_id"],
    )

    op.create_table(
        "coin_intelligence_shadow_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=False),
        sa.Column(
            "outcome_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("subject_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("actual_commodity_id", sa.Integer(), nullable=True),
        sa.Column("settlement", sa.String(length=16), nullable=False),
        sa.Column("trade_form", sa.String(length=16), nullable=False),
        sa.Column("actual_project_price", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("label_status", sa.String(length=32), nullable=False),
        sa.Column("review_reason", sa.String(length=160), nullable=True),
        sa.Column("absolute_percent_error", sa.Float(), nullable=True),
        sa.Column("signed_percent_error", sa.Float(), nullable=True),
        sa.Column("interval_covered", sa.Boolean(), nullable=True),
        sa.Column(
            "scoring_policy_version",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome_version > 0",
            name="ck_coin_intelligence_shadow_outcomes_version_positive",
        ),
        sa.CheckConstraint(
            "actual_project_price > 0",
            name="ck_coin_intelligence_shadow_outcomes_price_positive",
        ),
        sa.ForeignKeyConstraint(
            ["actual_commodity_id"],
            ["commodities.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["coin_intelligence_shadow_predictions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prediction_id",
            "outcome_version",
            name="uq_coin_intelligence_shadow_outcomes_prediction_version",
        ),
    )
    op.create_index(
        "ix_coin_intelligence_shadow_outcomes_market_time",
        "coin_intelligence_shadow_outcomes",
        [
            "actual_commodity_id",
            "settlement",
            "trade_form",
            "occurred_at_utc",
        ],
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_outcomes_prediction_id"),
        "coin_intelligence_shadow_outcomes",
        ["prediction_id"],
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_outcomes_subject_fingerprint"),
        "coin_intelligence_shadow_outcomes",
        ["subject_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_coin_intelligence_shadow_outcomes_subject_fingerprint"),
        table_name="coin_intelligence_shadow_outcomes",
    )
    op.drop_index(
        op.f("ix_coin_intelligence_shadow_outcomes_prediction_id"),
        table_name="coin_intelligence_shadow_outcomes",
    )
    op.drop_index(
        "ix_coin_intelligence_shadow_outcomes_market_time",
        table_name="coin_intelligence_shadow_outcomes",
    )
    op.drop_table("coin_intelligence_shadow_outcomes")

    op.drop_index(
        op.f("ix_coin_intelligence_shadow_parser_results_run_id"),
        table_name="coin_intelligence_shadow_parser_results",
    )
    op.drop_table("coin_intelligence_shadow_parser_results")

    op.drop_index(
        op.f("ix_coin_intelligence_shadow_predictions_run_id"),
        table_name="coin_intelligence_shadow_predictions",
    )
    op.drop_index(
        "ix_coin_intelligence_shadow_predictions_market",
        table_name="coin_intelligence_shadow_predictions",
    )
    op.drop_table("coin_intelligence_shadow_predictions")

    op.drop_index(
        op.f("ix_coin_intelligence_shadow_feature_snapshots_run_id"),
        table_name="coin_intelligence_shadow_feature_snapshots",
    )
    op.drop_table("coin_intelligence_shadow_feature_snapshots")

    op.drop_index(
        "ix_coin_intelligence_shadow_runs_subject",
        table_name="coin_intelligence_shadow_runs",
    )
    op.drop_index(
        "ix_coin_intelligence_shadow_runs_component_time",
        table_name="coin_intelligence_shadow_runs",
    )
    op.drop_table("coin_intelligence_shadow_runs")
