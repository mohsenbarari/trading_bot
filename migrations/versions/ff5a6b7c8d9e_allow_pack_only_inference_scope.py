"""allow pack-only coin inference audit scope

Revision ID: ff5a6b7c8d9e
Revises: fe4f5a6b7c8d
Create Date: 2026-08-22 08:15:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "ff5a6b7c8d9e"
down_revision: Union[str, Sequence[str], None] = "fe4f5a6b7c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_coin_intelligence_inference_audit_candidate_scope"
_TABLE = "coin_intelligence_inference_audits"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "candidate_scope IN ('ALL', 'LOW_DATE_ONLY', 'PACK_ONLY')",
    )


def downgrade() -> None:
    # PostgreSQL fails and rolls the migration back if PACK_ONLY audit evidence
    # exists.  Never delete append-only inference evidence to force a downgrade.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "candidate_scope IN ('ALL', 'LOW_DATE_ONLY')",
    )
