"""add durable promotion authentication epoch

Revision ID: 0promauth01
Revises: 0deltaattempt01

The row is intentionally inert until a separately authorized local promotion
coordinator invokes the transaction-scoped invalidation primitive.  This
migration neither invalidates sessions nor changes traffic or writer state.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0promauth01"
down_revision: Union[str, Sequence[str], None] = "0deltaattempt01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotion_auth_epochs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("writer_site", sa.String(length=16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("witness_transition_id", sa.String(length=128), nullable=False),
        sa.Column("cutover_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minimum_token_iat", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_promotion_auth_epochs_singleton"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_promotion_auth_epochs_writer_epoch"),
        sa.CheckConstraint(
            "minimum_token_iat >= 0",
            name="ck_promotion_auth_epochs_minimum_token_iat",
        ),
        sa.CheckConstraint(
            "writer_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_promotion_auth_epochs_writer_site",
        ),
        sa.CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epochs_writer_lease",
        ),
        sa.CheckConstraint(
            "char_length(witness_transition_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epochs_witness_transition",
        ),
        sa.CheckConstraint(
            "char_length(operation_id) = 36",
            name="ck_promotion_auth_epochs_operation_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("promotion_auth_epochs")
