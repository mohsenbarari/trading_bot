"""add a global Gen1/Gen2 V2 Witness-attestation consumption registry

Revision ID: 0v2consreg01
Revises: 0v2strictbind01

The Gen1 and bridge-bound Gen2 strict-writer tables remain separate immutable
schemas.  Their table-local unique ``attestation_sha256`` constraints cannot,
by themselves, prevent one signed Witness attestation from being consumed by
both generations.  This child revision introduces one append-only registry
whose primary key is that canonical digest.

Both source tables claim the registry from a ``BEFORE INSERT`` trigger in the
same PostgreSQL transaction.  A unique conflict aborts the source insert and
its V1-parent/head work together.  Existing source rows are locked, checked
for cross-generation overlap, and backfilled fail-closed before either trigger
is installed.  No nullable hybrid table, transport, signer, worker, or live
writer behavior is introduced here.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0v2consreg01"
down_revision: Union[str, Sequence[str], None] = "0v2strictbind01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ZERO_SHA256 = "0" * 64
_SHA256 = "^[0-9a-f]{64}$"
_GEN1 = "strict_writer_gen1"
_GEN2 = "strict_writer_gen2"
_GEN1_COMMIT_ID = "^v2-witness-strict-writer-[0-9a-f]{64}$"
_GEN2_COMMIT_ID = "^v2-witness-strict-writer-g2-[0-9a-f]{64}$"
_GEN1_TABLE = "physical_wal_v2_witness_roundtrip_strict_writer_commits"
_GEN2_TABLE = "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits"
_REGISTRY_TABLE = "physical_wal_v2_witness_roundtrip_attestation_consumptions"
_CLAIM_FUNCTION = "trading_bot_v2wsrc_claim_global_attestation"
_MUTATION_FUNCTION = "trading_bot_v2wsrc_registry_reject_mutation"
_GEN1_CLAIM_TRIGGER = "trg_v2wsrc_claim_global_attestation"
_GEN2_CLAIM_TRIGGER = "trg_v2wsrcb_claim_global_attestation"
_REGISTRY_ROW_TRIGGER = "trg_v2wsrc_registry_append_only_row"
_REGISTRY_TRUNCATE_TRIGGER = "trg_v2wsrc_registry_append_only_truncate"


def upgrade() -> None:
    # Hold both source relations through the overlap check, backfill, and
    # trigger installation.  Without these locks, a source insert could race
    # the one-time backfill window and evade the global identity claim.
    op.execute(
        f"LOCK TABLE {_GEN1_TABLE}, {_GEN2_TABLE} IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM {_GEN1_TABLE} AS gen1
                INNER JOIN {_GEN2_TABLE} AS gen2
                    USING (attestation_sha256)
            ) THEN
                RAISE EXCEPTION
                    'refusing V2 global attestation registry migration: Gen1/Gen2 attestation overlap exists';
            END IF;
        END
        $$;
        """
    )
    op.create_table(
        _REGISTRY_TABLE,
        sa.Column("attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_generation", sa.String(length=32), nullable=False),
        sa.Column("source_commit_id", sa.String(length=128), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("attestation_sha256"),
        sa.CheckConstraint(
            f"attestation_sha256 ~ '{_SHA256}' "
            f"AND attestation_sha256 <> '{_ZERO_SHA256}'",
            name="ck_v2wsrc_registry_attestation",
        ),
        sa.CheckConstraint(
            f"(source_generation = '{_GEN1}' "
            f"AND source_commit_id ~ '{_GEN1_COMMIT_ID}') "
            f"OR (source_generation = '{_GEN2}' "
            f"AND source_commit_id ~ '{_GEN2_COMMIT_ID}')",
            name="ck_v2wsrc_registry_source",
        ),
        sa.CheckConstraint(
            "consumed_at IS NOT NULL",
            name="ck_v2wsrc_registry_consumed_at",
        ),
    )
    # Backfill Gen1 first so an existing Gen1 consumption retains precedence.
    # The overlap guard above makes the Gen2 insert deterministic and ensures
    # any unexpected duplicate is an error, never a silent ON CONFLICT skip.
    op.execute(
        f"""
        INSERT INTO {_REGISTRY_TABLE} (
            attestation_sha256,
            source_generation,
            source_commit_id,
            consumed_at
        )
        SELECT
            attestation_sha256,
            '{_GEN1}',
            commit_id,
            committed_at
        FROM {_GEN1_TABLE}
        ORDER BY attestation_sha256
        """
    )
    op.execute(
        f"""
        INSERT INTO {_REGISTRY_TABLE} (
            attestation_sha256,
            source_generation,
            source_commit_id,
            consumed_at
        )
        SELECT
            attestation_sha256,
            '{_GEN2}',
            commit_id,
            committed_at
        FROM {_GEN2_TABLE}
        ORDER BY attestation_sha256
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_CLAIM_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            generation text;
        BEGIN
            IF TG_TABLE_NAME = '{_GEN1_TABLE}' THEN
                generation := '{_GEN1}';
            ELSIF TG_TABLE_NAME = '{_GEN2_TABLE}' THEN
                generation := '{_GEN2}';
            ELSE
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'V2 Witness global attestation claim called from an unknown source table';
            END IF;

            -- Do not catch unique_violation: PostgreSQL must make a repeated
            -- claim fail the same transaction as its attempted source row.
            INSERT INTO {_REGISTRY_TABLE} (
                attestation_sha256,
                source_generation,
                source_commit_id,
                consumed_at
            ) VALUES (
                NEW.attestation_sha256,
                generation,
                NEW.commit_id,
                NEW.committed_at
            );
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_MUTATION_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'V2 Witness global attestation consumption registry rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_GEN1_CLAIM_TRIGGER}
        BEFORE INSERT ON {_GEN1_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_CLAIM_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_GEN2_CLAIM_TRIGGER}
        BEFORE INSERT ON {_GEN2_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_CLAIM_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_REGISTRY_ROW_TRIGGER}
        BEFORE UPDATE OR DELETE ON {_REGISTRY_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_REGISTRY_TRUNCATE_TRIGGER}
        BEFORE TRUNCATE ON {_REGISTRY_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )


def downgrade() -> None:
    # Never remove the cross-generation one-time fence after any source or
    # registry evidence exists.  This includes pre-existing Gen1 source rows:
    # silently rolling back only the registry would reopen double-consumption.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_REGISTRY_TABLE})
                OR EXISTS (SELECT 1 FROM {_GEN1_TABLE})
                OR EXISTS (SELECT 1 FROM {_GEN2_TABLE}) THEN
                RAISE EXCEPTION
                    'refusing destructive V2 global attestation registry downgrade: durable registry or source rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_REGISTRY_TRUNCATE_TRIGGER} ON {_REGISTRY_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {_REGISTRY_ROW_TRIGGER} ON {_REGISTRY_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {_GEN2_CLAIM_TRIGGER} ON {_GEN2_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {_GEN1_CLAIM_TRIGGER} ON {_GEN1_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_MUTATION_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_CLAIM_FUNCTION}()")
    op.drop_table(_REGISTRY_TABLE)
