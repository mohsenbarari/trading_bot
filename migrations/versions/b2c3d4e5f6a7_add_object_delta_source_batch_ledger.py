"""add source batch ledger and outbound acknowledgement cursor

Revision ID: 0deltasource01
Revises: 0deltadelta01

This migration is schema-only.  It creates durable source evidence needed by
a future encrypted Object-Storage publisher, without enabling a worker,
network client, acknowledgement path, or data import.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0deltasource01"
down_revision: Union[str, Sequence[str], None] = "0deltadelta01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GENESIS_SHA256 = "0" * 64


def upgrade() -> None:
    op.create_table(
        "object_delta_source_batch_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("first_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("prior_chain_sha256", sa.String(length=64), nullable=False),
        sa.Column("batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("object_version_id", sa.String(length=1024), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column("ciphertext_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["stream_id"], ["object_delta_streams.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("first_sequence >= 1", name="ck_object_delta_source_batch_ledger_first_sequence"),
        sa.CheckConstraint("last_sequence >= first_sequence", name="ck_object_delta_source_batch_ledger_sequence_range"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_object_delta_source_batch_ledger_writer_epoch"),
        sa.CheckConstraint("char_length(writer_lease_id) BETWEEN 1 AND 128", name="ck_object_delta_source_batch_ledger_writer_lease"),
        sa.CheckConstraint(
            "char_length(prior_chain_sha256) = 64 AND char_length(batch_sha256) = 64 AND char_length(payload_sha256) = 64 AND char_length(ciphertext_sha256) = 64",
            name="ck_object_delta_source_batch_ledger_hashes",
        ),
        sa.CheckConstraint("payload_bytes >= 1 AND ciphertext_bytes >= 1", name="ck_object_delta_source_batch_ledger_bytes"),
        sa.CheckConstraint("char_length(object_key) BETWEEN 3 AND 1024", name="ck_object_delta_source_batch_ledger_object_key"),
        sa.CheckConstraint("char_length(object_version_id) BETWEEN 1 AND 1024", name="ck_object_delta_source_batch_ledger_object_version"),
        sa.UniqueConstraint("stream_id", "first_sequence", name="ux_object_delta_source_batch_ledger_stream_first_sequence"),
        sa.UniqueConstraint("stream_id", "batch_sha256", name="ux_object_delta_source_batch_ledger_stream_batch_hash"),
        sa.UniqueConstraint("object_key", "object_version_id", name="ux_object_delta_source_batch_ledger_object_version"),
    )
    op.create_index("ix_object_delta_source_batch_ledger_id", "object_delta_source_batch_ledger", ["id"], unique=False)
    op.create_index(
        "ix_object_delta_source_batch_ledger_stream_last_sequence",
        "object_delta_source_batch_ledger",
        ["stream_id", "last_sequence"],
        unique=False,
    )

    op.create_table(
        "object_delta_outbound_ack_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("last_acknowledged_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "last_acknowledged_batch_sha256",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text(f"'{_GENESIS_SHA256}'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["stream_id"], ["object_delta_streams.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("last_acknowledged_sequence >= 0", name="ck_object_delta_outbound_ack_cursors_last_sequence"),
        sa.CheckConstraint("char_length(last_acknowledged_batch_sha256) = 64", name="ck_object_delta_outbound_ack_cursors_last_batch_hash"),
        sa.UniqueConstraint("stream_id", name="ux_object_delta_outbound_ack_cursors_stream"),
    )
    op.create_index("ix_object_delta_outbound_ack_cursors_id", "object_delta_outbound_ack_cursors", ["id"], unique=False)


def downgrade() -> None:
    # Source ledger rows are recovery and audit evidence.  Refuse a downgrade
    # that would silently discard them or weaken a live acknowledgement trail.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_source_batch_ledger)
                OR EXISTS (SELECT 1 FROM object_delta_outbound_ack_cursors) THEN
                RAISE EXCEPTION
                    'refusing destructive object-delta source ledger downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_object_delta_outbound_ack_cursors_id", table_name="object_delta_outbound_ack_cursors")
    op.drop_table("object_delta_outbound_ack_cursors")
    op.drop_index("ix_object_delta_source_batch_ledger_stream_last_sequence", table_name="object_delta_source_batch_ledger")
    op.drop_index("ix_object_delta_source_batch_ledger_id", table_name="object_delta_source_batch_ledger")
    op.drop_table("object_delta_source_batch_ledger")
