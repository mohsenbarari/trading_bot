"""add append-only coin intelligence inference outcomes

Revision ID: d4e8a2b6c1f0
Revises: d3f7a1c9e4b5
Create Date: 2026-08-05 08:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a2b6c1f0"
down_revision: Union[str, Sequence[str], None] = "d3f7a1c9e4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coin_intelligence_inference_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outcome_key", sa.String(length=64), nullable=False),
        sa.Column("decision_key", sa.String(length=64), nullable=False),
        sa.Column("source_surface", sa.String(length=16), nullable=False),
        sa.Column("outcome_kind", sa.String(length=32), nullable=False),
        sa.Column("selected_commodity_id", sa.Integer(), nullable=False),
        sa.Column("selected_commodity_code", sa.String(length=32), nullable=False),
        sa.Column("selected_commodity_name", sa.String(length=96), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "source_surface IN ('WEBAPP', 'TELEGRAM_BOT')",
            name="ck_coin_infer_outcome_source_surface",
        ),
        sa.CheckConstraint(
            "outcome_kind = 'OFFER_ACCEPTED_SELECTION'",
            name="ck_coin_infer_outcome_kind",
        ),
        sa.CheckConstraint(
            "selected_commodity_id > 0",
            name="ck_coin_infer_outcome_selected_positive",
        ),
        sa.ForeignKeyConstraint(
            ["decision_key"],
            ["coin_intelligence_inference_audits.decision_key"],
            name="fk_coin_infer_outcome_decision",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("outcome_key", name="uq_coin_infer_outcome_key"),
    )
    op.create_index(
        "ix_coin_infer_outcome_created_surface",
        "coin_intelligence_inference_outcomes",
        ["created_at", "source_surface"],
    )
    op.create_index(
        "ix_coin_infer_outcome_decision_key",
        "coin_intelligence_inference_outcomes",
        ["decision_key"],
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION coin_intelligence_inference_outcome_immutable()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'coin intelligence inference outcome is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_coin_intelligence_inference_outcome_immutable
            BEFORE UPDATE OR DELETE ON coin_intelligence_inference_outcomes
            FOR EACH ROW EXECUTE FUNCTION coin_intelligence_inference_outcome_immutable();
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM coin_intelligence_inference_outcomes) THEN
                    RAISE EXCEPTION
                        'coin intelligence inference outcomes must be archived before downgrade';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute(sa.text("DROP TRIGGER trg_coin_intelligence_inference_outcome_immutable ON coin_intelligence_inference_outcomes"))
    op.execute(sa.text("DROP FUNCTION coin_intelligence_inference_outcome_immutable()"))
    op.drop_index("ix_coin_infer_outcome_decision_key", table_name="coin_intelligence_inference_outcomes")
    op.drop_index("ix_coin_infer_outcome_created_surface", table_name="coin_intelligence_inference_outcomes")
    op.drop_table("coin_intelligence_inference_outcomes")
