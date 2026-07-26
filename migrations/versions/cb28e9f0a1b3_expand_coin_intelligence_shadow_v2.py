"""Expand the local coin-intelligence Shadow-v2 ledger.

Revision ID: cb28e9f0a1b3
Revises: ca17d8e9f0a2
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "cb28e9f0a1b3"
down_revision = "ca17d8e9f0a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coin_intelligence_shadow_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("job_kind", sa.String(length=32), nullable=False),
        sa.Column("local_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "requested_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default="5",
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("worker_token", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=96)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETE','FAILED')",
            name="ck_coin_intelligence_shadow_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_coin_intelligence_shadow_jobs_attempts",
        ),
        sa.CheckConstraint(
            "job_kind IN ('PROJECT_OFFER','PROJECT_TRADE')",
            name="ck_coin_intelligence_shadow_jobs_kind",
        ),
        sa.CheckConstraint(
            "local_id > 0",
            name="ck_coin_intelligence_shadow_jobs_local_id_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_coin_intelligence_shadow_jobs_idempotency_key",
        ),
    )
    op.create_index(
        "ix_coin_intelligence_shadow_jobs_claim",
        "coin_intelligence_shadow_jobs",
        ["status", "available_at", "lease_expires_at"],
    )

    op.create_table(
        "coin_intelligence_shadow_quality_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("realtime_weight", sa.Float(), nullable=False),
        sa.Column("training_weight", sa.Float(), nullable=False),
        sa.Column(
            "review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN "
            "('EXCLUDE','REVIEW_REQUIRED','INCLUDE_SHADOW')",
            name="ck_coin_intelligence_shadow_quality_decision",
        ),
        sa.CheckConstraint(
            "realtime_weight >= 0 AND realtime_weight <= 1 "
            "AND training_weight >= 0 AND training_weight <= 1",
            name="ck_coin_intelligence_shadow_quality_weights",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["coin_intelligence_shadow_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            name="uq_coin_intelligence_shadow_quality_run_id",
        ),
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_quality_decisions_run_id"),
        "coin_intelligence_shadow_quality_decisions",
        ["run_id"],
    )

    op.create_table(
        "coin_intelligence_shadow_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outcome_id", sa.Integer(), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reviewer_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("corrected_project_price", sa.BigInteger()),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        sa.Column("note_code", sa.String(length=96)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "review_version > 0",
            name="ck_coin_intelligence_shadow_reviews_version_positive",
        ),
        sa.CheckConstraint(
            "corrected_project_price IS NULL "
            "OR corrected_project_price > 0",
            name="ck_coin_intelligence_shadow_reviews_price_positive",
        ),
        sa.CheckConstraint(
            "action IN "
            "('ACCEPT_ORIGINAL','ACCEPT_CORRECTION','REJECT_LABEL',"
            "'KEEP_UNREVIEWED','AMBIGUOUS')",
            name="ck_coin_intelligence_shadow_reviews_action",
        ),
        sa.CheckConstraint(
            "(action = 'ACCEPT_CORRECTION' "
            "AND corrected_project_price IS NOT NULL) "
            "OR (action <> 'ACCEPT_CORRECTION' "
            "AND corrected_project_price IS NULL)",
            name="ck_coin_intelligence_shadow_reviews_correction_shape",
        ),
        sa.ForeignKeyConstraint(
            ["outcome_id"],
            ["coin_intelligence_shadow_outcomes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outcome_id",
            "review_version",
            name="uq_coin_intelligence_shadow_reviews_version",
        ),
    )
    op.create_index(
        op.f("ix_coin_intelligence_shadow_reviews_outcome_id"),
        "coin_intelligence_shadow_reviews",
        ["outcome_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_coin_intelligence_shadow_reviews_outcome_id"),
        table_name="coin_intelligence_shadow_reviews",
    )
    op.drop_table("coin_intelligence_shadow_reviews")
    op.drop_index(
        op.f("ix_coin_intelligence_shadow_quality_decisions_run_id"),
        table_name="coin_intelligence_shadow_quality_decisions",
    )
    op.drop_table("coin_intelligence_shadow_quality_decisions")
    op.drop_index(
        "ix_coin_intelligence_shadow_jobs_claim",
        table_name="coin_intelligence_shadow_jobs",
    )
    op.drop_table("coin_intelligence_shadow_jobs")
