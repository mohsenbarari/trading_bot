"""persist the Gen2 strict-writer's complete opaque V2 base identity

Revision ID: 0v2basepin01
Revises: 0v2consreg01

The Gen2 V2 strict-writer table already retains the full bound V1 parent and
bridge certificate.  Its original shape did not retain the two identities of
the opaque Gen1 prepared V2 base instruction from which a Gen2 commit is
derived: the base configuration digest and deterministic base commit id.
Canonical receipt/certificate bytes are forensic material, not a sufficient
substitute for durable exact-row reconciliation.  This immutable child adds
both non-null pins after the global cross-generation attestation registry.

No historical migration is rewritten.  Existing durable Gen2 rows are
refused rather than guessed from opaque bytes, so this revision never creates
a nullable or unbound hybrid.  The table's existing append-only row/truncate
triggers remain in force for the new columns; no trigger is weakened or
replaced.  This revision does not authorize a writer, contact a peer/Witness,
or change application traffic.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0v2basepin01"
down_revision: Union[str, Sequence[str], None] = "0v2consreg01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits"
_ZERO_SHA256 = "0" * 64
_SHA256 = "^[0-9a-f]{64}$"
_GEN1_COMMIT_ID = "^v2-witness-strict-writer-[0-9a-f]{64}$"
_HASH_CONSTRAINT = "ck_v2wsrcb_base_configuration_sha256"
_IDENTITY_CONSTRAINT = "ck_v2wsrcb_base_commit_id"
_UNIQUE_CONSTRAINT = "ux_v2wsrcb_base_commit_id"


def upgrade() -> None:
    # Adding NOT NULL base pins to a nonempty Gen2 evidence relation would
    # require parsing historical opaque receipt bytes.  That is intentionally
    # forbidden: refuse the operation and preserve the durable generation.
    op.execute(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION
                    'refusing Gen2 V2 base-pin migration: durable bound rows exist';
            END IF;
        END
        $$;
        """
    )
    op.add_column(
        _TABLE,
        sa.Column("v2_base_configuration_sha256", sa.String(length=64), nullable=False),
    )
    op.add_column(
        _TABLE,
        sa.Column("v2_base_commit_id", sa.String(length=128), nullable=False),
    )
    op.create_check_constraint(
        _HASH_CONSTRAINT,
        _TABLE,
        f"v2_base_configuration_sha256 ~ '{_SHA256}' "
        f"AND v2_base_configuration_sha256 <> '{_ZERO_SHA256}'",
    )
    op.create_check_constraint(
        _IDENTITY_CONSTRAINT,
        _TABLE,
        f"v2_base_commit_id ~ '{_GEN1_COMMIT_ID}'",
    )
    op.create_unique_constraint(
        _UNIQUE_CONSTRAINT,
        _TABLE,
        ["v2_base_commit_id"],
    )


def downgrade() -> None:
    # A downgrade must not discard exact base identities after any Gen2 row
    # exists, even though the broader global consumption registry is retained
    # by its own fail-closed downgrade guard.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION
                    'refusing destructive Gen2 V2 base-pin downgrade: durable bound rows exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(_UNIQUE_CONSTRAINT, _TABLE, type_="unique")
    op.drop_constraint(_IDENTITY_CONSTRAINT, _TABLE, type_="check")
    op.drop_constraint(_HASH_CONSTRAINT, _TABLE, type_="check")
    op.drop_column(_TABLE, "v2_base_commit_id")
    op.drop_column(_TABLE, "v2_base_configuration_sha256")
