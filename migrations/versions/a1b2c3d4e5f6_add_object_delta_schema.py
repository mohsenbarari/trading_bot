"""add append-only object delta schema primitives

Revision ID: 0deltadelta01
Revises: f2c7d8e9a0b1

This migration is intentionally limited to durable allocator/outbox,
receiver-cursor, and immutable import-receipt tables.  It does not create
workers, triggers, Object Storage clients, or any data-plane side effects.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0deltadelta01"
down_revision: Union[str, Sequence[str], None] = "f2c7d8e9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SITES = "source_site IN ('webapp_fi', 'webapp_ir') AND destination_site IN ('webapp_fi', 'webapp_ir') AND source_site <> destination_site"


def _common_identity_checks(prefix: str) -> list[sa.sql.elements.ClauseElement]:
    return [
        sa.CheckConstraint(_SITES, name=f"ck_{prefix}_sites"),
        sa.CheckConstraint(f"char_length(campaign_id) BETWEEN 8 AND 128", name=f"ck_{prefix}_campaign_id"),
        sa.CheckConstraint(f"char_length(release_sha) = 40", name=f"ck_{prefix}_release_sha"),
        sa.CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name=f"ck_{prefix}_generation_id",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "object_delta_streams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=False),
        sa.Column("release_sha", sa.String(length=40), nullable=False),
        sa.Column("stream_generation_id", sa.String(length=128), nullable=False),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        *_common_identity_checks("object_delta_streams"),
        sa.CheckConstraint("next_sequence >= 1", name="ck_object_delta_streams_next_sequence"),
        sa.UniqueConstraint(
            "source_site", "destination_site", "campaign_id", "release_sha", "stream_generation_id",
            name="ux_object_delta_streams_identity",
        ),
    )
    op.create_index("ix_object_delta_streams_id", "object_delta_streams", ["id"], unique=False)

    op.create_table(
        "object_delta_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("logical_sequence", sa.BigInteger(), nullable=False),
        sa.Column("change_log_id", sa.Integer(), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_sync_item", sa.JSON(), nullable=False),
        sa.Column("sync_item_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["stream_id"], ["object_delta_streams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["change_log_id"], ["change_log.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("logical_sequence >= 1", name="ck_object_delta_outbox_sequence"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_object_delta_outbox_writer_epoch"),
        sa.CheckConstraint("char_length(writer_lease_id) BETWEEN 1 AND 128", name="ck_object_delta_outbox_writer_lease"),
        sa.CheckConstraint("char_length(sync_item_sha256) = 64", name="ck_object_delta_outbox_sync_item_hash"),
        sa.UniqueConstraint("stream_id", "logical_sequence", name="ux_object_delta_outbox_stream_sequence"),
        sa.UniqueConstraint("stream_id", "change_log_id", name="ux_object_delta_outbox_stream_change_log"),
    )
    op.create_index("ix_object_delta_outbox_id", "object_delta_outbox", ["id"], unique=False)
    op.create_index("ix_object_delta_outbox_stream_sequence", "object_delta_outbox", ["stream_id", "logical_sequence"], unique=False)

    op.create_table(
        "object_delta_receiver_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=False),
        sa.Column("release_sha", sa.String(length=40), nullable=False),
        sa.Column("stream_generation_id", sa.String(length=128), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        *_common_identity_checks("object_delta_receiver_cursors"),
        sa.CheckConstraint("last_sequence >= 0", name="ck_object_delta_receiver_cursors_last_sequence"),
        sa.CheckConstraint("char_length(last_batch_sha256) = 64", name="ck_object_delta_receiver_cursors_last_batch_hash"),
        sa.UniqueConstraint(
            "source_site", "destination_site", "campaign_id", "release_sha", "stream_generation_id",
            name="ux_object_delta_receiver_cursors_identity",
        ),
    )
    op.create_index("ix_object_delta_receiver_cursors_id", "object_delta_receiver_cursors", ["id"], unique=False)

    op.create_table(
        "object_delta_import_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=False),
        sa.Column("release_sha", sa.String(length=40), nullable=False),
        sa.Column("stream_generation_id", sa.String(length=128), nullable=False),
        sa.Column("first_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("prior_chain_sha256", sa.String(length=64), nullable=False),
        sa.Column("batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("object_version_id", sa.String(length=1024), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column("ciphertext_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        *_common_identity_checks("object_delta_import_receipts"),
        sa.CheckConstraint("first_sequence >= 1", name="ck_object_delta_import_receipts_first_sequence"),
        sa.CheckConstraint("last_sequence >= first_sequence", name="ck_object_delta_import_receipts_sequence_range"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_object_delta_import_receipts_writer_epoch"),
        sa.CheckConstraint("char_length(writer_lease_id) BETWEEN 1 AND 128", name="ck_object_delta_import_receipts_writer_lease"),
        sa.CheckConstraint(
            "char_length(prior_chain_sha256) = 64 AND char_length(batch_sha256) = 64 AND char_length(payload_sha256) = 64 AND char_length(ciphertext_sha256) = 64",
            name="ck_object_delta_import_receipts_hashes",
        ),
        sa.CheckConstraint("char_length(object_key) BETWEEN 3 AND 1024", name="ck_object_delta_import_receipts_object_key"),
        sa.CheckConstraint("char_length(object_version_id) BETWEEN 1 AND 1024", name="ck_object_delta_import_receipts_object_version"),
        sa.CheckConstraint("ciphertext_bytes >= 1", name="ck_object_delta_import_receipts_ciphertext_bytes"),
        sa.UniqueConstraint("object_key", "object_version_id", name="ux_object_delta_import_receipts_object_version"),
        sa.UniqueConstraint(
            "source_site", "destination_site", "campaign_id", "release_sha", "stream_generation_id", "first_sequence",
            name="ux_object_delta_import_receipts_stream_first_sequence",
        ),
    )
    op.create_index("ix_object_delta_import_receipts_id", "object_delta_import_receipts", ["id"], unique=False)
    op.create_index(
        "ix_object_delta_import_receipts_stream_last_sequence",
        "object_delta_import_receipts",
        ["source_site", "destination_site", "campaign_id", "release_sha", "stream_generation_id", "last_sequence"],
        unique=False,
    )


def downgrade() -> None:
    # These tables hold append-only source evidence and receiver receipts.
    # There is no lossless downgrade once any row exists, so refuse to erase
    # a live stream rather than silently discarding recovery/audit evidence.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_streams)
                OR EXISTS (SELECT 1 FROM object_delta_outbox)
                OR EXISTS (SELECT 1 FROM object_delta_receiver_cursors)
                OR EXISTS (SELECT 1 FROM object_delta_import_receipts) THEN
                RAISE EXCEPTION
                    'refusing destructive object-delta schema downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_object_delta_import_receipts_stream_last_sequence", table_name="object_delta_import_receipts")
    op.drop_index("ix_object_delta_import_receipts_id", table_name="object_delta_import_receipts")
    op.drop_table("object_delta_import_receipts")
    op.drop_index("ix_object_delta_receiver_cursors_id", table_name="object_delta_receiver_cursors")
    op.drop_table("object_delta_receiver_cursors")
    op.drop_index("ix_object_delta_outbox_stream_sequence", table_name="object_delta_outbox")
    op.drop_index("ix_object_delta_outbox_id", table_name="object_delta_outbox")
    op.drop_table("object_delta_outbox")
    op.drop_index("ix_object_delta_streams_id", table_name="object_delta_streams")
    op.drop_table("object_delta_streams")
