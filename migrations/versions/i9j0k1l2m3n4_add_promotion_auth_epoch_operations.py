"""retain consumed promotion auth epoch operation IDs

Revision ID: 0promauthop01
Revises: 0promauth01

The singleton current epoch cannot by itself prove that an operation ID was
used by a past Writer Witness term.  This append-only ledger closes that
replay gap.  It is still inert until a separately authorized coordinator calls
the transaction-scoped invalidation primitive.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0promauthop01"
down_revision: Union[str, Sequence[str], None] = "0promauth01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TRIGGER_FUNCTION = "trading_bot_reject_promotion_auth_epoch_operation_mutation"
_TRIGGER = "trg_promotion_auth_epoch_operations_append_only"


def upgrade() -> None:
    op.create_table(
        "promotion_auth_epoch_operations",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="ux_promotion_auth_epoch_ops_operation"),
        sa.UniqueConstraint("writer_epoch", name="ux_promotion_auth_epoch_ops_writer_epoch"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_promotion_auth_epoch_ops_writer_epoch"),
        sa.CheckConstraint(
            "minimum_token_iat >= 0",
            name="ck_promotion_auth_epoch_ops_minimum_token_iat",
        ),
        sa.CheckConstraint(
            "writer_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_promotion_auth_epoch_ops_writer_site",
        ),
        sa.CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epoch_ops_writer_lease",
        ),
        sa.CheckConstraint(
            "char_length(witness_transition_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epoch_ops_witness_transition",
        ),
        sa.CheckConstraint(
            "char_length(operation_id) = 36",
            name="ck_promotion_auth_epoch_ops_operation_id",
        ),
    )
    op.execute(
        f"""
        CREATE FUNCTION {_TRIGGER_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'promotion auth epoch operation rows are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE UPDATE OR DELETE ON promotion_auth_epoch_operations
        FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON promotion_auth_epoch_operations")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
    op.drop_table("promotion_auth_epoch_operations")
