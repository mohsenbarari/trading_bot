"""add append-only coin intelligence inference audit

Revision ID: d3f7a1c9e4b5
Revises: b2d4e6f8a0c2
Create Date: 2026-08-04 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3f7a1c9e4b5"
down_revision: Union[str, Sequence[str], None] = "b2d4e6f8a0c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coin_intelligence_inference_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("decision_key", sa.String(length=64), nullable=False),
        sa.Column("source_surface", sa.String(length=16), nullable=False),
        sa.Column("decision_status", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=True),
        sa.Column("settlement_term", sa.String(length=16), nullable=False),
        sa.Column("submitted_project_price", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("selected_commodity_id", sa.Integer(), nullable=True),
        sa.Column("selected_commodity_code", sa.String(length=32), nullable=True),
        sa.Column("selected_commodity_name", sa.String(length=96), nullable=True),
        sa.Column("inference_version", sa.String(length=64), nullable=False),
        sa.Column("catalog_resolution_version", sa.String(length=64), nullable=False),
        sa.Column("snapshot_receipt", sa.String(length=64), nullable=True),
        sa.Column("snapshot_generated_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "source_surface IN ('WEBAPP', 'TELEGRAM_BOT', 'INTERNAL')",
            name="ck_coin_intelligence_inference_audit_source_surface",
        ),
        sa.CheckConstraint(
            "decision_status IN ('AUTO_SELECT', 'CONFIRM', 'ABSTAIN')",
            name="ck_coin_intelligence_inference_audit_decision_status",
        ),
        sa.CheckConstraint(
            "settlement_term IN ('CASH', 'TOMORROW')",
            name="ck_coin_intelligence_inference_audit_settlement_term",
        ),
        sa.CheckConstraint(
            "submitted_project_price > 0",
            name="ck_coin_intelligence_inference_audit_price_positive",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0",
            name="ck_coin_intelligence_inference_audit_candidate_count",
        ),
        sa.CheckConstraint(
            "selected_commodity_id IS NULL OR selected_commodity_id > 0",
            name="ck_coin_intelligence_inference_audit_selected_commodity_positive",
        ),
        sa.CheckConstraint(
            "(decision_status = 'AUTO_SELECT' AND candidate_count = 1 "
            "AND selected_commodity_id IS NOT NULL "
            "AND selected_commodity_code IS NOT NULL "
            "AND selected_commodity_name IS NOT NULL) "
            "OR (decision_status = 'CONFIRM' AND candidate_count >= 1 "
            "AND selected_commodity_id IS NULL "
            "AND selected_commodity_code IS NULL "
            "AND selected_commodity_name IS NULL) "
            "OR (decision_status = 'ABSTAIN' AND candidate_count = 0 "
            "AND selected_commodity_id IS NULL "
            "AND selected_commodity_code IS NULL "
            "AND selected_commodity_name IS NULL)",
            name="ck_coin_intelligence_inference_audit_decision_shape",
        ),
        sa.UniqueConstraint(
            "decision_key",
            name="uq_coin_intelligence_inference_audit_decision_key",
        ),
    )
    op.create_index(
        "ix_coin_intelligence_inference_audit_created_status",
        "coin_intelligence_inference_audits",
        ["created_at", "decision_status"],
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION coin_intelligence_inference_audit_immutable()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'coin intelligence inference audit is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_coin_intelligence_inference_audit_immutable
            BEFORE UPDATE OR DELETE ON coin_intelligence_inference_audits
            FOR EACH ROW EXECUTE FUNCTION coin_intelligence_inference_audit_immutable();
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM coin_intelligence_inference_audits) THEN
                    RAISE EXCEPTION
                        'coin intelligence inference audit must be archived before downgrade';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute(sa.text("DROP TRIGGER trg_coin_intelligence_inference_audit_immutable ON coin_intelligence_inference_audits"))
    op.execute(sa.text("DROP FUNCTION coin_intelligence_inference_audit_immutable()"))
    op.drop_index(
        "ix_coin_intelligence_inference_audit_created_status",
        table_name="coin_intelligence_inference_audits",
    )
    op.drop_table("coin_intelligence_inference_audits")
