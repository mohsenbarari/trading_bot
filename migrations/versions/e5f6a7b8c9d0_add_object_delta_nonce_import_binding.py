"""bind receiver delivery nonce rows to the full immutable import identity

Revision ID: 0deltanoncebind01
Revises: 0deltacutover01

The prior nonce-to-import foreign key protected only Object key and VersionId.
This revision makes the database prove that a consumed controller nonce is
attached to the exact source/destination stream, Writer Witness term, sequence
range, batch digest, and immutable Object-version receipt it claims.  It is
schema-only and does not enable a receiver or touch Object Storage.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0deltanoncebind01"
down_revision: Union[str, Sequence[str], None] = "0deltacutover01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMPORT_BINDING_COLUMNS = [
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "stream_generation_id",
    "writer_epoch",
    "writer_lease_id",
    "first_sequence",
    "last_sequence",
    "batch_sha256",
    "object_key",
    "object_version_id",
]


def upgrade() -> None:
    # PostgreSQL requires an exact candidate key for the composite child FK.
    # Object-version uniqueness already prevents duplicate rows, but does not
    # prove the nonce row's stream/term/batch claims agree with that receipt.
    op.create_unique_constraint(
        "ux_object_delta_import_receipts_nonce_binding",
        "object_delta_import_receipts",
        _IMPORT_BINDING_COLUMNS,
    )
    op.drop_constraint(
        "fk_od_rdnr_import_object",
        "object_delta_receiver_delivery_nonce_receipts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_od_rdnr_import_binding",
        "object_delta_receiver_delivery_nonce_receipts",
        "object_delta_import_receipts",
        _IMPORT_BINDING_COLUMNS,
        _IMPORT_BINDING_COLUMNS,
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Losing the composite relation after a nonce was consumed would silently
    # weaken durable anti-replay/audit evidence.  Refuse that downgrade rather
    # than retaining rows under a less specific historical constraint.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_receiver_delivery_nonce_receipts) THEN
                RAISE EXCEPTION
                    'refusing destructive object-delta nonce import-binding downgrade: durable nonce rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        "fk_od_rdnr_import_binding",
        "object_delta_receiver_delivery_nonce_receipts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_od_rdnr_import_object",
        "object_delta_receiver_delivery_nonce_receipts",
        "object_delta_import_receipts",
        ["object_key", "object_version_id"],
        ["object_key", "object_version_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ux_object_delta_import_receipts_nonce_binding",
        "object_delta_import_receipts",
        type_="unique",
    )
