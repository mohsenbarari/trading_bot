"""add durable append-only Object-delta source publication attempt evidence

Revision ID: 0deltaattempt01
Revises: 0deltaguard01

This schema-only revision persists the future source publisher's immutable
attempt facts around the non-transactional Object-Storage boundary.  It does
not enable a publisher, access a spool, encrypt, contact Object Storage,
verify signatures, or run a worker.

An attempt advances by inserting immutable dependent evidence rows rather
than updating a mutable status: reservation, sealed ciphertext, exact
Object-VersionId receipt, source attestation, then a terminal source-ledger
binding.  The final binding trigger proves the referenced ledger row matches
the full stream/term/range/payload/Object/ciphertext/batch identity.  A later
root-only adapter must still lock the required rows, perform Object Storage
reconciliation, and insert the source ledger plus terminal binding in one
database transaction.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0deltaattempt01"
down_revision: Union[str, Sequence[str], None] = "0deltaguard01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SITES = (
    "source_site IN ('webapp_fi', 'webapp_ir') "
    "AND destination_site IN ('webapp_fi', 'webapp_ir') "
    "AND source_site <> destination_site"
)


def upgrade() -> None:
    op.create_table(
        "object_delta_source_publication_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=72), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=False),
        sa.Column("release_sha", sa.String(length=40), nullable=False),
        sa.Column("stream_generation_id", sa.String(length=128), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("first_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("prior_chain_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("destination_age_recipient", sa.String(length=132), nullable=False),
        sa.Column("transport_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_cutover_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_cutover_artifact_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_SITES, name="ck_od_spa_sites"),
        sa.CheckConstraint(
            "attempt_id ~ '^odsp-v1:[0-9a-f]{64}$'",
            name="ck_od_spa_attempt_id",
        ),
        sa.CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_od_spa_campaign",
        ),
        sa.CheckConstraint("release_sha ~ '^[0-9a-f]{40}$'", name="ck_od_spa_release"),
        sa.CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_od_spa_generation",
        ),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_od_spa_writer_epoch"),
        sa.CheckConstraint(
            "writer_lease_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_od_spa_writer_lease",
        ),
        sa.CheckConstraint(
            "first_sequence >= 1 AND last_sequence >= first_sequence "
            "AND last_sequence - first_sequence <= 99999",
            name="ck_od_spa_sequence_range",
        ),
        sa.CheckConstraint(
            "prior_chain_sha256 ~ '^[0-9a-f]{64}$' "
            "AND payload_sha256 ~ '^[0-9a-f]{64}$' "
            "AND transport_policy_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_cutover_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_spa_hashes",
        ),
        sa.CheckConstraint(
            "payload_bytes BETWEEN 1 AND 107374182400",
            name="ck_od_spa_payload_bytes",
        ),
        sa.CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024 "
            "AND object_key ~ '^[A-Za-z0-9][A-Za-z0-9._/=-]*$' "
            "AND object_key NOT LIKE '%/../%'",
            name="ck_od_spa_object_key",
        ),
        sa.CheckConstraint(
            "destination_age_recipient ~ '^age1[ac-hj-np-z02-9]{20,128}$'",
            name="ck_od_spa_destination_recipient",
        ),
        sa.CheckConstraint(
            "source_cutover_artifact_bytes BETWEEN 1 AND 131072",
            name="ck_od_spa_cutover_artifact",
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
            name="fk_od_spa_stream_identity",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("attempt_id", name="ux_od_spa_attempt_id"),
        sa.UniqueConstraint("object_key", name="ux_od_spa_object_key"),
        sa.UniqueConstraint(
            "attempt_id",
            "object_key",
            name="ux_od_spa_attempt_object_key",
        ),
        sa.UniqueConstraint(
            "stream_id",
            "first_sequence",
            name="ux_od_spa_stream_first_sequence",
        ),
    )
    op.create_index(
        "ix_od_spa_stream_first",
        "object_delta_source_publication_attempts",
        ["stream_id", "first_sequence"],
        unique=False,
    )

    op.create_table(
        "object_delta_source_publication_seals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=72), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column("ciphertext_bytes", sa.BigInteger(), nullable=False),
        sa.Column("spool_sha256", sa.String(length=64), nullable=False),
        sa.Column("spool_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "ciphertext_sha256 ~ '^[0-9a-f]{64}$' "
            "AND spool_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_sps_hashes",
        ),
        sa.CheckConstraint(
            "ciphertext_bytes BETWEEN 1 AND 107375230976 "
            "AND spool_bytes BETWEEN 1 AND 107375230976",
            name="ck_od_sps_bytes",
        ),
        sa.CheckConstraint(
            "ciphertext_sha256 = spool_sha256 AND ciphertext_bytes = spool_bytes",
            name="ck_od_sps_exact_spool",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["object_delta_source_publication_attempts.attempt_id"],
            name="fk_od_sps_attempt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("attempt_id", name="ux_od_sps_attempt"),
        sa.UniqueConstraint(
            "attempt_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            name="ux_od_sps_attempt_ciphertext",
        ),
    )

    op.create_table(
        "object_delta_source_publication_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=72), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("object_version_id", sa.String(length=1024), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column("ciphertext_bytes", sa.BigInteger(), nullable=False),
        sa.Column("transport_receipt_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("transport_receipt_artifact_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024 "
            "AND object_key ~ '^[A-Za-z0-9][A-Za-z0-9._/=-]*$' "
            "AND object_key NOT LIKE '%/../%'",
            name="ck_od_spr_object_key",
        ),
        sa.CheckConstraint(
            "char_length(object_version_id) BETWEEN 1 AND 1024 "
            "AND object_version_id ~ '^[A-Za-z0-9._~+/=-]+$' "
            "AND lower(object_version_id) <> 'null'",
            name="ck_od_spr_object_version",
        ),
        sa.CheckConstraint(
            "ciphertext_sha256 ~ '^[0-9a-f]{64}$' "
            "AND transport_receipt_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_spr_hashes",
        ),
        sa.CheckConstraint(
            "ciphertext_bytes BETWEEN 1 AND 107375230976 "
            "AND transport_receipt_artifact_bytes BETWEEN 1 AND 32768",
            name="ck_od_spr_bytes",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "object_key"],
            [
                "object_delta_source_publication_attempts.attempt_id",
                "object_delta_source_publication_attempts.object_key",
            ],
            name="fk_od_spr_attempt_key",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "ciphertext_sha256", "ciphertext_bytes"],
            [
                "object_delta_source_publication_seals.attempt_id",
                "object_delta_source_publication_seals.ciphertext_sha256",
                "object_delta_source_publication_seals.ciphertext_bytes",
            ],
            name="fk_od_spr_seal_ciphertext",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("attempt_id", name="ux_od_spr_attempt"),
        sa.UniqueConstraint(
            "object_key",
            "object_version_id",
            name="ux_od_spr_object_version",
        ),
    )

    op.create_table(
        "object_delta_source_publication_attestations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=72), nullable=False),
        sa.Column("source_key_id", sa.String(length=79), nullable=False),
        sa.Column("batch_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_attestation_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_attestation_artifact_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source_key_id ~ '^ed25519-sha256:[0-9a-f]{64}$'",
            name="ck_od_spat_source_key",
        ),
        sa.CheckConstraint(
            "batch_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_attestation_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_spat_hashes",
        ),
        sa.CheckConstraint(
            "source_attestation_artifact_bytes BETWEEN 1 AND 8454144",
            name="ck_od_spat_artifact_bytes",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["object_delta_source_publication_receipts.attempt_id"],
            name="fk_od_spat_receipt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("attempt_id", name="ux_od_spat_attempt"),
    )

    op.create_table(
        "object_delta_source_publication_ledger_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=72), nullable=False),
        sa.Column("source_batch_ledger_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["object_delta_source_publication_attestations.attempt_id"],
            name="fk_od_splb_attestation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_batch_ledger_id"],
            ["object_delta_source_batch_ledger.id"],
            name="fk_od_splb_source_ledger",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("attempt_id", name="ux_od_splb_attempt"),
        sa.UniqueConstraint("source_batch_ledger_id", name="ux_od_splb_source_ledger"),
    )

    # All phases are immutable.  The status visible to a future adapter is
    # inferred from which dependent evidence row exists, so it can only move
    # forward by INSERT and cannot be rolled back through an UPDATE.
    op.execute(
        """
        CREATE FUNCTION object_delta_guard_append_only_source_pub_attempt()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = format(
                    'object-delta source publication evidence is append-only: %s on %s is forbidden',
                    TG_OP,
                    TG_TABLE_NAME
                );
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION object_delta_guard_source_pub_ledger_binding()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM object_delta_source_publication_attempts AS attempt_row
                JOIN object_delta_source_publication_seals AS seal_row
                  ON seal_row.attempt_id = attempt_row.attempt_id
                JOIN object_delta_source_publication_receipts AS receipt_row
                  ON receipt_row.attempt_id = attempt_row.attempt_id
                JOIN object_delta_source_publication_attestations AS attestation_row
                  ON attestation_row.attempt_id = attempt_row.attempt_id
                JOIN object_delta_source_batch_ledger AS ledger_row
                  ON ledger_row.id = NEW.source_batch_ledger_id
                WHERE attempt_row.attempt_id = NEW.attempt_id
                  AND ledger_row.stream_id = attempt_row.stream_id
                  AND ledger_row.first_sequence = attempt_row.first_sequence
                  AND ledger_row.last_sequence = attempt_row.last_sequence
                  AND ledger_row.writer_epoch = attempt_row.writer_epoch
                  AND ledger_row.writer_lease_id = attempt_row.writer_lease_id
                  AND ledger_row.prior_chain_sha256 = attempt_row.prior_chain_sha256
                  AND ledger_row.payload_sha256 = attempt_row.payload_sha256
                  AND ledger_row.payload_bytes = attempt_row.payload_bytes
                  AND ledger_row.object_key = attempt_row.object_key
                  AND ledger_row.object_key = receipt_row.object_key
                  AND ledger_row.object_version_id = receipt_row.object_version_id
                  AND ledger_row.ciphertext_sha256 = seal_row.ciphertext_sha256
                  AND ledger_row.ciphertext_sha256 = receipt_row.ciphertext_sha256
                  AND ledger_row.ciphertext_bytes = seal_row.ciphertext_bytes
                  AND ledger_row.ciphertext_bytes = receipt_row.ciphertext_bytes
                  AND ledger_row.batch_sha256 = attestation_row.batch_sha256
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'object-delta source publication terminal binding requires an exact immutable source ledger match';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    for table_name, prefix in (
        ("object_delta_source_publication_attempts", "spa"),
        ("object_delta_source_publication_seals", "sps"),
        ("object_delta_source_publication_receipts", "spr"),
        ("object_delta_source_publication_attestations", "spat"),
        ("object_delta_source_publication_ledger_bindings", "splb"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_od_{prefix}_append_only_row
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION object_delta_guard_append_only_source_pub_attempt();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_od_{prefix}_append_only_truncate
            BEFORE TRUNCATE ON {table_name}
            FOR EACH STATEMENT
            EXECUTE FUNCTION object_delta_guard_append_only_source_pub_attempt();
            """
        )

    op.execute(
        """
        CREATE TRIGGER trg_od_splb_validate_ledger
        BEFORE INSERT ON object_delta_source_publication_ledger_bindings
        FOR EACH ROW
        EXECUTE FUNCTION object_delta_guard_source_pub_ledger_binding();
        """
    )


def downgrade() -> None:
    # Each table contains recovery/audit evidence.  Do not weaken a live
    # publisher's durable replay state by silently dropping it.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_source_publication_attempts)
                OR EXISTS (SELECT 1 FROM object_delta_source_publication_seals)
                OR EXISTS (SELECT 1 FROM object_delta_source_publication_receipts)
                OR EXISTS (SELECT 1 FROM object_delta_source_publication_attestations)
                OR EXISTS (SELECT 1 FROM object_delta_source_publication_ledger_bindings) THEN
                RAISE EXCEPTION
                    'refusing destructive object-delta source publication attempt downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER trg_od_splb_validate_ledger ON object_delta_source_publication_ledger_bindings;"
    )
    for table_name, prefix in (
        ("object_delta_source_publication_ledger_bindings", "splb"),
        ("object_delta_source_publication_attestations", "spat"),
        ("object_delta_source_publication_receipts", "spr"),
        ("object_delta_source_publication_seals", "sps"),
        ("object_delta_source_publication_attempts", "spa"),
    ):
        op.execute(f"DROP TRIGGER trg_od_{prefix}_append_only_truncate ON {table_name};")
        op.execute(f"DROP TRIGGER trg_od_{prefix}_append_only_row ON {table_name};")
    op.execute("DROP FUNCTION object_delta_guard_source_pub_ledger_binding();")
    op.execute("DROP FUNCTION object_delta_guard_append_only_source_pub_attempt();")
    op.drop_table("object_delta_source_publication_ledger_bindings")
    op.drop_table("object_delta_source_publication_attestations")
    op.drop_table("object_delta_source_publication_receipts")
    op.drop_table("object_delta_source_publication_seals")
    op.drop_index("ix_od_spa_stream_first", table_name="object_delta_source_publication_attempts")
    op.drop_table("object_delta_source_publication_attempts")
