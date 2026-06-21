"""add trade number sequence

Revision ID: e0f1a2b3c4d7
Revises: d0e1f2a3b4c6
Create Date: 2026-06-21 10:55:00.000000
"""

import os

from alembic import op


revision = "e0f1a2b3c4d7"
down_revision = "d0e1f2a3b4c6"
branch_labels = None
depends_on = None


def _server_trade_number_base() -> tuple[int, int]:
    server_mode = (os.environ.get("SERVER_MODE") or "iran").strip().lower()
    if server_mode == "foreign":
        return 10000, 0
    return 10001, 1


def upgrade() -> None:
    base_value, parity = _server_trade_number_base()
    fallback_max = base_value - 2
    op.execute(
        f"""
        CREATE SEQUENCE IF NOT EXISTS trade_number_seq
        AS integer
        INCREMENT BY 2
        MINVALUE 1
        START WITH {base_value}
        CACHE 1
        """
    )
    op.execute("ALTER SEQUENCE trade_number_seq INCREMENT BY 2")
    op.execute(
        f"""
        WITH current_max AS (
            SELECT COALESCE(MAX(trade_number), {fallback_max}) AS max_trade_number
            FROM trades
        ),
        next_value AS (
            SELECT
                CASE
                    WHEN ((max_trade_number + 1) % 2) = {parity}
                    THEN max_trade_number + 1
                    ELSE max_trade_number + 2
                END AS value
            FROM current_max
        )
        SELECT setval('trade_number_seq', (SELECT value FROM next_value), false)
        """
    )


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS trade_number_seq")
