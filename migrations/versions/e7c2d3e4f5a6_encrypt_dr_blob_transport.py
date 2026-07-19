"""Encrypt the DR blob transport and persist immutable cipher identity.

Revision ID: e7c2d3e4f5a6
Revises: e6b1c2d3e4f5
"""

import sqlalchemy as sa
from alembic import op


revision = "e7c2d3e4f5a6"
down_revision = "e6b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("dr_blob_manifests", "object_key", existing_type=sa.String(512), nullable=True)
    op.add_column("dr_blob_manifests", sa.Column("object_ciphertext_hash", sa.String(64)))
    op.add_column("dr_blob_manifests", sa.Column("object_ciphertext_size", sa.BigInteger()))
    op.add_column("dr_blob_manifests", sa.Column("encryption_key_id", sa.String(64)))
    op.add_column("dr_blob_manifests", sa.Column("encryption_algorithm", sa.String(32)))
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM dr_blob_manifests WHERE state <> 'local') THEN "
        "RAISE EXCEPTION 'uploaded legacy DR blobs require an explicit encrypted backfill'; "
        "END IF; END $$"
    )
    op.execute(
        "UPDATE dr_blob_manifests SET object_key = NULL, object_version_id = NULL, "
        "object_etag = NULL WHERE state = 'local'"
    )
    op.create_check_constraint(
        "ck_dr_blob_manifest_ciphertext_size",
        "dr_blob_manifests",
        "object_ciphertext_size IS NULL OR object_ciphertext_size >= 36",
    )
    op.create_check_constraint(
        "ck_dr_blob_manifest_uploaded_cipher",
        "dr_blob_manifests",
        "state = 'local' OR (object_key IS NOT NULL AND object_ciphertext_hash IS NOT NULL "
        "AND object_ciphertext_size IS NOT NULL AND encryption_key_id IS NOT NULL "
        "AND encryption_algorithm = 'AES-256-GCM-v1')",
    )
    op.add_column("dr_blob_receipts", sa.Column("object_ciphertext_hash", sa.String(64)))
    op.add_column("dr_blob_receipts", sa.Column("object_ciphertext_size", sa.BigInteger()))
    op.add_column("dr_blob_receipts", sa.Column("encryption_key_id", sa.String(64)))
    op.add_column("dr_blob_receipts", sa.Column("encryption_algorithm", sa.String(32)))
    # No encrypted object has been emitted by the pre-integration branch.  A
    # non-empty legacy table must be explicitly backfilled before this migration.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM dr_blob_receipts) THEN "
        "RAISE EXCEPTION 'legacy DR blob receipts require an explicit encrypted backfill'; "
        "END IF; END $$"
    )
    for column in (
        "object_ciphertext_hash",
        "object_ciphertext_size",
        "encryption_key_id",
        "encryption_algorithm",
    ):
        op.alter_column("dr_blob_receipts", column, nullable=False)


def downgrade() -> None:
    op.drop_column("dr_blob_receipts", "encryption_algorithm")
    op.drop_column("dr_blob_receipts", "encryption_key_id")
    op.drop_column("dr_blob_receipts", "object_ciphertext_size")
    op.drop_column("dr_blob_receipts", "object_ciphertext_hash")
    op.drop_constraint("ck_dr_blob_manifest_uploaded_cipher", "dr_blob_manifests", type_="check")
    op.drop_constraint("ck_dr_blob_manifest_ciphertext_size", "dr_blob_manifests", type_="check")
    op.drop_column("dr_blob_manifests", "encryption_algorithm")
    op.drop_column("dr_blob_manifests", "encryption_key_id")
    op.drop_column("dr_blob_manifests", "object_ciphertext_size")
    op.drop_column("dr_blob_manifests", "object_ciphertext_hash")
    op.execute(
        "UPDATE dr_blob_manifests SET object_key = 'legacy-unavailable/' || content_hash "
        "WHERE object_key IS NULL"
    )
    op.alter_column("dr_blob_manifests", "object_key", existing_type=sa.String(512), nullable=False)
