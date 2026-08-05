"""freeze safe market context on coin inference audits

Revision ID: e5a1c4d7b2f9
Revises: d4e8a2b6c1f0
Create Date: 2026-08-05 10:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1c4d7b2f9"
down_revision: Union[str, Sequence[str], None] = "d4e8a2b6c1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add bounded provenance labels without rewriting append-only audit rows."""

    op.add_column(
        "coin_intelligence_inference_audits",
        sa.Column("dominant_underlying_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "coin_intelligence_inference_audits",
        sa.Column(
            "market_regime",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
    )
    op.create_check_constraint(
        "ck_coin_infer_audit_market_regime",
        "coin_intelligence_inference_audits",
        "market_regime IN ('NORMAL', 'UP', 'DOWN', 'VOLATILE', 'UNKNOWN')",
    )


def downgrade() -> None:
    """Fail closed: never silently discard frozen rollout provenance."""

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM coin_intelligence_inference_audits
                    WHERE dominant_underlying_source IS NOT NULL
                       OR market_regime <> 'UNKNOWN'
                ) THEN
                    RAISE EXCEPTION
                        'coin inference audit market context must be archived before downgrade';
                END IF;
            END
            $$;
            """
        )
    )
    op.drop_constraint(
        "ck_coin_infer_audit_market_regime",
        "coin_intelligence_inference_audits",
        type_="check",
    )
    op.drop_column("coin_intelligence_inference_audits", "market_regime")
    op.drop_column("coin_intelligence_inference_audits", "dominant_underlying_source")
