"""add durable Object-delta receiver delivery nonce receipts

Revision ID: 0deltanonce01
Revises: 0deltasource01

This schema alone does not enable a receiver, create a worker, or contact
Object Storage.  A future dedicated receiver consumes the nonce only in its
same database transaction as import receipt and cursor updates.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0deltanonce01"
down_revision: Union[str, Sequence[str], None] = "0deltasource01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SITES = (
    "source_site IN ('webapp_fi', 'webapp_ir') "
    "AND destination_site IN ('webapp_fi', 'webapp_ir') "
    "AND source_site <> destination_site"
)


def upgrade() -> None:
    op.create_table(
        "object_delta_receiver_delivery_nonce_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("controller_key_id", sa.String(length=79), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("packet_claim_sha256", sa.String(length=64), nullable=False),
        sa.Column("bucket", sa.String(length=63), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("destination_age_recipient", sa.String(length=132), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=False),
        sa.Column("release_sha", sa.String(length=40), nullable=False),
        sa.Column("stream_generation_id", sa.String(length=128), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("first_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("object_version_id", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_SITES, name="ck_od_rdnr_sites"),
        sa.CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_od_rdnr_campaign",
        ),
        sa.CheckConstraint(
            "char_length(release_sha) = 40",
            name="ck_od_rdnr_release",
        ),
        sa.CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_od_rdnr_generation",
        ),
        sa.CheckConstraint(
            "char_length(controller_key_id) = 79",
            name="ck_od_rdnr_controller_key",
        ),
        sa.CheckConstraint(
            "char_length(nonce) BETWEEN 32 AND 128",
            name="ck_od_rdnr_nonce",
        ),
        sa.CheckConstraint(
            "char_length(bucket) BETWEEN 3 AND 63",
            name="ck_od_rdnr_bucket",
        ),
        sa.CheckConstraint(
            "char_length(destination_age_recipient) BETWEEN 24 AND 132",
            name="ck_od_rdnr_destination_recipient",
        ),
        sa.CheckConstraint(
            "char_length(packet_claim_sha256) = 64 AND char_length(batch_sha256) = 64",
            name="ck_od_rdnr_hashes",
        ),
        sa.CheckConstraint(
            "writer_epoch >= 1",
            name="ck_od_rdnr_writer_epoch",
        ),
        sa.CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_od_rdnr_writer_lease",
        ),
        sa.CheckConstraint(
            "first_sequence >= 1 AND last_sequence >= first_sequence",
            name="ck_od_rdnr_sequence_range",
        ),
        sa.CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024",
            name="ck_od_rdnr_object_key",
        ),
        sa.CheckConstraint(
            "char_length(object_version_id) BETWEEN 1 AND 1024",
            name="ck_od_rdnr_object_version",
        ),
        sa.ForeignKeyConstraint(
            ["object_key", "object_version_id"],
            [
                "object_delta_import_receipts.object_key",
                "object_delta_import_receipts.object_version_id",
            ],
            name="fk_od_rdnr_import_object",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "controller_key_id",
            "nonce",
            name="ux_od_rdnr_controller_nonce",
        ),
    )
    op.create_index(
        "ix_object_delta_receiver_delivery_nonce_receipts_id",
        "object_delta_receiver_delivery_nonce_receipts",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_od_rdnr_stream_sequence",
        "object_delta_receiver_delivery_nonce_receipts",
        [
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            "first_sequence",
        ],
        unique=False,
    )


def downgrade() -> None:
    # Nonce rows are anti-replay and audit evidence.  Removing them would
    # make a previously consumed controller packet appear fresh after an
    # upgrade, so no destructive downgrade is valid once a row exists.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_receiver_delivery_nonce_receipts) THEN
                RAISE EXCEPTION
                    'refusing destructive object-delta receiver delivery nonce downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index(
        "ix_od_rdnr_stream_sequence",
        table_name="object_delta_receiver_delivery_nonce_receipts",
    )
    op.drop_index(
        "ix_object_delta_receiver_delivery_nonce_receipts_id",
        table_name="object_delta_receiver_delivery_nonce_receipts",
    )
    op.drop_table("object_delta_receiver_delivery_nonce_receipts")
