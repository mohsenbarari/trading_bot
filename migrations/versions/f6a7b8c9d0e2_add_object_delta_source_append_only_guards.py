"""enforce append-only Object-delta source evidence at the database boundary

Revision ID: 0deltaguard01
Revises: 0deltanoncebind01

This PostgreSQL-only, schema-only migration closes the application-only
allocator boundary for the durable source evidence tables.  Each *new* source
cutover must be inserted as one complete ``baseline_published`` record,
cutover and outbox rows cannot subsequently be changed or removed, and an
outbox insert must match the immutable published cutover, its exact stream
identity, and its Writer Witness term.

The migration deliberately contains no role names or GRANT/REVOKE statements:
role ownership and least-privilege deployment are environment-specific and
must be applied separately.  It also does not contact Object Storage, inspect
credentials, start a worker, or apply this migration to any database.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0deltaguard01"
down_revision: Union[str, Sequence[str], None] = "0deltanoncebind01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PUBLISHED_STATE = "baseline_published"


def upgrade() -> None:
    # Freeze the source evidence tables before checking their historical rows
    # and installing the guards.  This makes the preflight and trigger install
    # one closed migration boundary instead of allowing an invalid INSERT to
    # race between them.
    op.execute(
        """
        LOCK TABLE
            object_delta_streams,
            object_delta_source_cutovers,
            object_delta_outbox
        IN SHARE ROW EXCLUSIVE MODE;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM object_delta_source_cutovers AS cutover_row
                WHERE cutover_row.state = '{_PUBLISHED_STATE}'
                  AND (
                       cutover_row.snapshot_manifest_object_key IS NULL
                   OR cutover_row.snapshot_manifest_object_version_id IS NULL
                   OR cutover_row.snapshot_manifest_ciphertext_sha256 IS NULL
                   OR cutover_row.snapshot_manifest_ciphertext_bytes IS NULL
                   OR cutover_row.baseline_manifest_object_key IS NULL
                   OR cutover_row.baseline_manifest_object_version_id IS NULL
                   OR cutover_row.baseline_manifest_ciphertext_sha256 IS NULL
                   OR cutover_row.baseline_manifest_ciphertext_bytes IS NULL
                  )
            ) THEN
                RAISE EXCEPTION
                    'refusing Object-delta append-only guard upgrade: published source cutover evidence is incomplete';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM object_delta_outbox AS outbox_row
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM object_delta_streams AS stream_row
                    JOIN object_delta_source_cutovers AS cutover_row
                      ON cutover_row.stream_id = stream_row.id
                     AND cutover_row.source_site = stream_row.source_site
                     AND cutover_row.destination_site = stream_row.destination_site
                     AND cutover_row.campaign_id = stream_row.campaign_id
                     AND cutover_row.release_sha = stream_row.release_sha
                     AND cutover_row.stream_generation_id = stream_row.stream_generation_id
                    WHERE stream_row.id = outbox_row.stream_id
                      AND cutover_row.state = '{_PUBLISHED_STATE}'
                      AND cutover_row.writer_epoch = outbox_row.writer_epoch
                      AND cutover_row.writer_lease_id = outbox_row.writer_lease_id
                      AND cutover_row.snapshot_manifest_object_key IS NOT NULL
                      AND cutover_row.snapshot_manifest_object_version_id IS NOT NULL
                      AND cutover_row.snapshot_manifest_ciphertext_sha256 IS NOT NULL
                      AND cutover_row.snapshot_manifest_ciphertext_bytes IS NOT NULL
                      AND cutover_row.baseline_manifest_object_key IS NOT NULL
                      AND cutover_row.baseline_manifest_object_version_id IS NOT NULL
                      AND cutover_row.baseline_manifest_ciphertext_sha256 IS NOT NULL
                      AND cutover_row.baseline_manifest_ciphertext_bytes IS NOT NULL
                )
            ) THEN
                RAISE EXCEPTION
                    'refusing Object-delta append-only guard upgrade: outbox evidence lacks matching baseline_published source cutover';
            END IF;
        END
        $$;
        """
    )

    # A historical ``outbox_active_baseline_pending`` row has never been valid
    # outbox authority.  Preserve it as inert append-only audit evidence rather
    # than rewriting it; a future active stream must use a fresh, final
    # baseline-published generation.

    # These triggers prove relational database facts only.  They cannot prove
    # Object Storage existence/cryptographic verification, a currently live
    # Writer Witness, or that a SQL caller is the reviewed allocator rather
    # than a role with equivalent table privileges.  Those boundaries require
    # separate witness/attestation logic and environment-specific least
    # privilege (or a controlled database API) at deployment time.
    #
    # Do not add an immediate ``NEW.logical_sequence = stream.next_sequence``
    # predicate here.  The synchronous allocator intentionally inserts the
    # outbox row before advancing the stream, while the async allocator changes
    # both in one ORM flush whose cross-mapper statement order is not an API
    # contract.  A frontier invariant therefore belongs in a separately
    # reviewed deferred cross-table constraint trigger (or after an explicit
    # staged async flush), not in this relationship/term guard.

    op.execute(
        f"""
        CREATE FUNCTION object_delta_guard_source_cutover_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.state IS DISTINCT FROM '{_PUBLISHED_STATE}'
               OR NEW.snapshot_manifest_object_key IS NULL
               OR NEW.snapshot_manifest_object_version_id IS NULL
               OR NEW.snapshot_manifest_ciphertext_sha256 IS NULL
               OR NEW.snapshot_manifest_ciphertext_bytes IS NULL
               OR NEW.baseline_manifest_object_key IS NULL
               OR NEW.baseline_manifest_object_version_id IS NULL
               OR NEW.baseline_manifest_ciphertext_sha256 IS NULL
               OR NEW.baseline_manifest_ciphertext_bytes IS NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'object-delta source cutover must be inserted as complete baseline_published evidence';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION object_delta_guard_append_only_source_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = format(
                    'object-delta source evidence is append-only: %s on %s is forbidden',
                    TG_OP,
                    TG_TABLE_NAME
                );
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION object_delta_guard_outbox_published_cutover()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM 1
            FROM object_delta_streams AS stream_row
            JOIN object_delta_source_cutovers AS cutover_row
              ON cutover_row.stream_id = stream_row.id
             AND cutover_row.source_site = stream_row.source_site
             AND cutover_row.destination_site = stream_row.destination_site
             AND cutover_row.campaign_id = stream_row.campaign_id
             AND cutover_row.release_sha = stream_row.release_sha
             AND cutover_row.stream_generation_id = stream_row.stream_generation_id
            WHERE stream_row.id = NEW.stream_id
              AND cutover_row.state = '{_PUBLISHED_STATE}'
              AND cutover_row.writer_epoch = NEW.writer_epoch
              AND cutover_row.writer_lease_id = NEW.writer_lease_id
              AND cutover_row.snapshot_manifest_object_key IS NOT NULL
              AND cutover_row.snapshot_manifest_object_version_id IS NOT NULL
              AND cutover_row.snapshot_manifest_ciphertext_sha256 IS NOT NULL
              AND cutover_row.snapshot_manifest_ciphertext_bytes IS NOT NULL
              AND cutover_row.baseline_manifest_object_key IS NOT NULL
              AND cutover_row.baseline_manifest_object_version_id IS NOT NULL
              AND cutover_row.baseline_manifest_ciphertext_sha256 IS NOT NULL
              AND cutover_row.baseline_manifest_ciphertext_bytes IS NOT NULL;

            IF NOT FOUND THEN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'object-delta outbox insert requires matching immutable baseline_published source cutover and Writer Witness term';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_object_delta_source_cutover_final_insert
        BEFORE INSERT ON object_delta_source_cutovers
        FOR EACH ROW
        EXECUTE FUNCTION object_delta_guard_source_cutover_insert();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_object_delta_source_cutovers_append_only_row
        BEFORE UPDATE OR DELETE ON object_delta_source_cutovers
        FOR EACH ROW
        EXECUTE FUNCTION object_delta_guard_append_only_source_evidence();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_object_delta_source_cutovers_append_only_truncate
        BEFORE TRUNCATE ON object_delta_source_cutovers
        FOR EACH STATEMENT
        EXECUTE FUNCTION object_delta_guard_append_only_source_evidence();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_object_delta_outbox_published_cutover
        BEFORE INSERT ON object_delta_outbox
        FOR EACH ROW
        EXECUTE FUNCTION object_delta_guard_outbox_published_cutover();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_object_delta_outbox_append_only_row
        BEFORE UPDATE OR DELETE ON object_delta_outbox
        FOR EACH ROW
        EXECUTE FUNCTION object_delta_guard_append_only_source_evidence();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_object_delta_outbox_append_only_truncate
        BEFORE TRUNCATE ON object_delta_outbox
        FOR EACH STATEMENT
        EXECUTE FUNCTION object_delta_guard_append_only_source_evidence();
        """
    )


def downgrade() -> None:
    # Removing these guards would re-open direct writes against a live stream.
    # Refuse the downgrade whenever any source stream or durable source
    # evidence exists; this is intentionally stronger than merely preserving
    # the two directly guarded tables.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM object_delta_streams)
                OR EXISTS (SELECT 1 FROM object_delta_source_cutovers)
                OR EXISTS (SELECT 1 FROM object_delta_outbox) THEN
                RAISE EXCEPTION
                    'refusing destructive Object-delta append-only guard downgrade: durable source rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER trg_object_delta_outbox_append_only_truncate ON object_delta_outbox;"
    )
    op.execute(
        "DROP TRIGGER trg_object_delta_outbox_append_only_row ON object_delta_outbox;"
    )
    op.execute(
        "DROP TRIGGER trg_object_delta_outbox_published_cutover ON object_delta_outbox;"
    )
    op.execute(
        "DROP TRIGGER trg_object_delta_source_cutovers_append_only_truncate ON object_delta_source_cutovers;"
    )
    op.execute(
        "DROP TRIGGER trg_object_delta_source_cutovers_append_only_row ON object_delta_source_cutovers;"
    )
    op.execute(
        "DROP TRIGGER trg_object_delta_source_cutover_final_insert ON object_delta_source_cutovers;"
    )
    op.execute("DROP FUNCTION object_delta_guard_outbox_published_cutover();")
    op.execute("DROP FUNCTION object_delta_guard_append_only_source_evidence();")
    op.execute("DROP FUNCTION object_delta_guard_source_cutover_insert();")
