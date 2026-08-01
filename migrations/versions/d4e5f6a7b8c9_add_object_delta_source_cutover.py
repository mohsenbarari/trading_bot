"""add durable Object-delta source cutover evidence

Revision ID: 0deltacutover01
Revises: 0deltanonce01

This migration is schema-only.  It records the identity and immutable
snapshot evidence a future root-only source coordinator must bind before a
baseline can be published.  It does not acquire a write gate, export a
snapshot, publish an Object, activate an outbox, or start a worker.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0deltacutover01"
down_revision: Union[str, Sequence[str], None] = "0deltanonce01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SITES = (
    "source_site IN ('webapp_fi', 'webapp_ir') "
    "AND destination_site IN ('webapp_fi', 'webapp_ir') "
    "AND source_site <> destination_site"
)


def upgrade() -> None:
    # The composite key lets the child FK prove that ``stream_id`` and every
    # duplicated stream identity component refer to the same stream row.
    op.create_unique_constraint(
        "ux_object_delta_streams_id_identity",
        "object_delta_streams",
        [
            "id",
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
        ],
    )
    op.create_table(
        "object_delta_source_cutovers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=False),
        sa.Column("release_sha", sa.String(length=40), nullable=False),
        sa.Column("stream_generation_id", sa.String(length=128), nullable=False),
        # No database default: the root-only coordinator supplies a fresh
        # write-gate UUID as part of its future cutover transaction.
        sa.Column("write_gate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("registry_fingerprint", sa.String(length=16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("source_generation", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("alembic_revision", sa.String(length=64), nullable=False),
        sa.Column("snapshot_manifest_object_key", sa.String(length=1024), nullable=True),
        sa.Column("snapshot_manifest_object_version_id", sa.String(length=1024), nullable=True),
        sa.Column("snapshot_manifest_ciphertext_sha256", sa.String(length=64), nullable=True),
        sa.Column("snapshot_manifest_ciphertext_bytes", sa.BigInteger(), nullable=True),
        sa.Column("baseline_manifest_object_key", sa.String(length=1024), nullable=True),
        sa.Column("baseline_manifest_object_version_id", sa.String(length=1024), nullable=True),
        sa.Column("baseline_manifest_ciphertext_sha256", sa.String(length=64), nullable=True),
        sa.Column("baseline_manifest_ciphertext_bytes", sa.BigInteger(), nullable=True),
        sa.Column("database_sha256", sa.String(length=64), nullable=False),
        sa.Column("uploads_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
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
        sa.CheckConstraint(_SITES, name="ck_object_delta_source_cutovers_sites"),
        sa.CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_object_delta_source_cutovers_campaign_id",
        ),
        sa.CheckConstraint(
            "char_length(release_sha) = 40",
            name="ck_object_delta_source_cutovers_release_sha",
        ),
        sa.CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_object_delta_source_cutovers_generation_id",
        ),
        sa.CheckConstraint(
            "char_length(registry_fingerprint) = 16",
            name="ck_object_delta_source_cutovers_registry_fingerprint",
        ),
        sa.CheckConstraint(
            "writer_epoch >= 1",
            name="ck_object_delta_source_cutovers_writer_epoch",
        ),
        sa.CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_object_delta_source_cutovers_writer_lease",
        ),
        sa.CheckConstraint(
            "char_length(source_generation) BETWEEN 1 AND 128",
            name="ck_object_delta_source_cutovers_source_generation",
        ),
        sa.CheckConstraint(
            "char_length(snapshot_id) BETWEEN 1 AND 128",
            name="ck_object_delta_source_cutovers_snapshot_id",
        ),
        sa.CheckConstraint(
            "char_length(alembic_revision) BETWEEN 8 AND 64",
            name="ck_object_delta_source_cutovers_alembic_revision",
        ),
        sa.CheckConstraint(
            "snapshot_manifest_object_key IS NULL OR char_length(snapshot_manifest_object_key) BETWEEN 3 AND 1024",
            name="ck_object_delta_source_cutovers_snapshot_manifest_object_key",
        ),
        sa.CheckConstraint(
            "snapshot_manifest_object_version_id IS NULL OR char_length(snapshot_manifest_object_version_id) BETWEEN 1 AND 1024",
            name="ck_object_delta_source_cutovers_snapshot_manifest_version",
        ),
        sa.CheckConstraint(
            "snapshot_manifest_ciphertext_sha256 IS NULL OR char_length(snapshot_manifest_ciphertext_sha256) = 64",
            name="ck_object_delta_source_cutovers_snapshot_manifest_hash",
        ),
        sa.CheckConstraint(
            "snapshot_manifest_ciphertext_bytes IS NULL OR snapshot_manifest_ciphertext_bytes >= 1",
            name="ck_object_delta_source_cutovers_snapshot_manifest_bytes",
        ),
        sa.CheckConstraint(
            "baseline_manifest_object_key IS NULL OR char_length(baseline_manifest_object_key) BETWEEN 3 AND 1024",
            name="ck_object_delta_source_cutovers_baseline_manifest_object_key",
        ),
        sa.CheckConstraint(
            "baseline_manifest_object_version_id IS NULL OR char_length(baseline_manifest_object_version_id) BETWEEN 1 AND 1024",
            name="ck_object_delta_source_cutovers_baseline_manifest_version",
        ),
        sa.CheckConstraint(
            "baseline_manifest_ciphertext_sha256 IS NULL OR char_length(baseline_manifest_ciphertext_sha256) = 64",
            name="ck_object_delta_source_cutovers_baseline_manifest_hash",
        ),
        sa.CheckConstraint(
            "baseline_manifest_ciphertext_bytes IS NULL OR baseline_manifest_ciphertext_bytes >= 1",
            name="ck_object_delta_source_cutovers_baseline_manifest_bytes",
        ),
        sa.CheckConstraint(
            "char_length(database_sha256) = 64 AND char_length(uploads_sha256) = 64",
            name="ck_object_delta_source_cutovers_local_snapshot_hashes",
        ),
        sa.CheckConstraint(
            "state IN ('outbox_active_baseline_pending', 'baseline_published')",
            name="ck_object_delta_source_cutovers_state",
        ),
        sa.CheckConstraint(
            "state <> 'baseline_published' OR ("
            "snapshot_manifest_object_key IS NOT NULL AND "
            "snapshot_manifest_object_version_id IS NOT NULL AND "
            "snapshot_manifest_ciphertext_sha256 IS NOT NULL AND "
            "snapshot_manifest_ciphertext_bytes IS NOT NULL AND "
            "baseline_manifest_object_key IS NOT NULL AND "
            "baseline_manifest_object_version_id IS NOT NULL AND "
            "baseline_manifest_ciphertext_sha256 IS NOT NULL AND "
            "baseline_manifest_ciphertext_bytes IS NOT NULL)",
            name="ck_object_delta_source_cutovers_published_object_evidence",
        ),
        sa.ForeignKeyConstraint(
            [
                "stream_id",
                "source_site",
                "destination_site",
                "campaign_id",
                "release_sha",
                "stream_generation_id",
            ],
            [
                "object_delta_streams.id",
                "object_delta_streams.source_site",
                "object_delta_streams.destination_site",
                "object_delta_streams.campaign_id",
                "object_delta_streams.release_sha",
                "object_delta_streams.stream_generation_id",
            ],
            name="fk_object_delta_source_cutovers_stream_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("stream_id", name="ux_object_delta_source_cutovers_stream"),
        sa.UniqueConstraint(
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            name="ux_object_delta_source_cutovers_identity",
        ),
        sa.UniqueConstraint("write_gate_id", name="ux_object_delta_source_cutovers_write_gate"),
    )
    op.create_index(
        "ix_object_delta_source_cutovers_id",
        "object_delta_source_cutovers",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    # A cutover row binds a write fence to the exact snapshot and stream that
    # follow it.  Dropping that evidence would make recovery/audit ambiguous.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_source_cutovers) THEN
                RAISE EXCEPTION
                    'refusing destructive object-delta source cutover downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index(
        "ix_object_delta_source_cutovers_id",
        table_name="object_delta_source_cutovers",
    )
    op.drop_table("object_delta_source_cutovers")
    op.drop_constraint(
        "ux_object_delta_streams_id_identity",
        "object_delta_streams",
        type_="unique",
    )
