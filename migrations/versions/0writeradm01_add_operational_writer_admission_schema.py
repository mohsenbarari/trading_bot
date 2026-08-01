"""add durable V1 operational writer-admission heads and commit receipts

Revision ID: 0writeradm01
Revises: 0promauthop01

This revision is schema-only.  It does not enable V1 writer admission, grant a
Witness term, start/stop a writer, change traffic, run a worker, or connect to
Object Storage or a remote site.  It gives a future *local* transaction
adapter a PostgreSQL CAS head plus an append-only receipt chain to couple with
its own database transaction.

``control_boundary`` / ``control_role_label`` are policy metadata, not a
claim that a PostgreSQL role is Unix root or an independently authorized
controller.  Host-local authority must be established outside this schema.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0writeradm01"
down_revision: Union[str, Sequence[str], None] = "0promauthop01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ZERO_SHA256 = "0" * 64
_CONTROL_BOUNDARY = "gold-trade-operational-writer-admission-local-transaction-adapter-v1"
_COMMIT_MUTATION_FUNCTION = "trading_bot_owa_reject_commit_mutation"
_HEAD_DELETE_FUNCTION = "trading_bot_owa_reject_head_deletion"
_COMMIT_INSERT_FUNCTION = "trading_bot_owa_validate_commit_insert"
_HEAD_UPDATE_FUNCTION = "trading_bot_owa_validate_head_update"
_HEAD_ASSERT_FUNCTION = "trading_bot_owa_assert_head_current"

_BINDING_CHECK = (
    "cluster_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$' "
    "AND local_site IN ('webapp_fi', 'webapp_ir') "
    "AND release_sha ~ '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' "
    "AND generation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'"
)
_TERM_CHECK = """
(
    holder_site IS NULL
    AND writer_epoch IS NULL
    AND writer_lease_id IS NULL
    AND evidence_id IS NULL
    AND revalidation_id IS NULL
    AND term_issued_at IS NULL
    AND term_expires_at IS NULL
)
OR
(
    holder_site = local_site
    AND writer_epoch >= 1
    AND writer_epoch = highest_writer_epoch
    AND writer_lease_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
    AND evidence_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
    AND revalidation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
    AND term_issued_at IS NOT NULL
    AND term_expires_at > term_issued_at
)
"""
_FENCE_CHECK = """
(
    (fenced = true AND fence_reason ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$')
    OR (fenced = false AND fence_reason IS NULL)
)
"""
_CONTROL_CHECK = (
    f"control_boundary = '{_CONTROL_BOUNDARY}' "
    "AND control_role_label ~ '^[a-z][a-z0-9-]{2,127}$' "
    "AND control_policy_sha256 ~ '^[0-9a-f]{64}$'"
)


def upgrade() -> None:
    # A head is mutable only through a same-transaction receipt-chain advance.
    # It contains the full state projection so a future adapter can use one
    # row lock and an exact SQL conditional UPDATE at the guarded commit.
    op.create_table(
        "operational_writer_admission_heads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_id", sa.String(length=128), nullable=False),
        sa.Column("local_site", sa.String(length=16), nullable=False),
        sa.Column("release_sha", sa.String(length=64), nullable=False),
        sa.Column("generation_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("prior_revision", sa.BigInteger(), nullable=False),
        sa.Column("highest_writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("holder_site", sa.String(length=16), nullable=True),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=True),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_id", sa.String(length=128), nullable=True),
        sa.Column("revalidation_id", sa.String(length=128), nullable=True),
        sa.Column("term_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("term_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revalidated_runtime_instance_id", sa.String(length=128), nullable=True),
        sa.Column("clock_floor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence_generation", sa.BigInteger(), nullable=False),
        sa.Column("fenced", sa.Boolean(), nullable=False),
        sa.Column("fence_reason", sa.String(length=128), nullable=True),
        sa.Column("requires_fresh_witness_revalidation", sa.Boolean(), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("current_commit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_commit_sha256", sa.String(length=64), nullable=False),
        sa.Column("control_boundary", sa.String(length=128), nullable=False),
        sa.Column("control_role_label", sa.String(length=128), nullable=False),
        sa.Column("control_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_BINDING_CHECK, name="ck_owa_heads_binding"),
        sa.CheckConstraint(
            "revision >= 0 AND prior_revision = revision - 1",
            name="ck_owa_heads_revision_chain",
        ),
        sa.CheckConstraint(
            "highest_writer_epoch >= 0 AND fence_generation >= 0",
            name="ck_owa_heads_nonnegative_counters",
        ),
        sa.CheckConstraint(_TERM_CHECK, name="ck_owa_heads_term"),
        sa.CheckConstraint(_FENCE_CHECK, name="ck_owa_heads_fence"),
        sa.CheckConstraint(
            "revalidated_runtime_instance_id IS NULL "
            "OR revalidated_runtime_instance_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'",
            name="ck_owa_heads_runtime_instance",
        ),
        sa.CheckConstraint(
            "state_sha256 ~ '^[0-9a-f]{64}$' "
            "AND receipt_sha256 ~ '^[0-9a-f]{64}$' "
            "AND current_commit_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_owa_heads_digests",
        ),
        sa.CheckConstraint(_CONTROL_CHECK, name="ck_owa_heads_control_metadata"),
        sa.UniqueConstraint(
            "cluster_id",
            "local_site",
            "release_sha",
            "generation_id",
            name="ux_owa_heads_binding",
        ),
        sa.UniqueConstraint(
            "id",
            "cluster_id",
            "local_site",
            "release_sha",
            "generation_id",
            name="ux_owa_heads_id_binding",
        ),
    )

    # Every commit repeats the complete next-state projection, not merely an
    # opaque status.  The paired head/commit FK is deliberately DEFERRABLE so
    # bootstrap can insert its head and immutable receipt in one transaction.
    op.create_table(
        "operational_writer_admission_commits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("head_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_id", sa.String(length=128), nullable=False),
        sa.Column("local_site", sa.String(length=16), nullable=False),
        sa.Column("release_sha", sa.String(length=64), nullable=False),
        sa.Column("generation_id", sa.String(length=128), nullable=False),
        sa.Column("transition_kind", sa.String(length=32), nullable=False),
        sa.Column("prior_revision", sa.BigInteger(), nullable=False),
        sa.Column("next_revision", sa.BigInteger(), nullable=False),
        sa.Column("prior_fence_generation", sa.BigInteger(), nullable=False),
        sa.Column("next_fence_generation", sa.BigInteger(), nullable=False),
        sa.Column("prior_state_sha256", sa.String(length=64), nullable=False),
        sa.Column("previous_commit_sha256", sa.String(length=64), nullable=False),
        sa.Column("highest_writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("holder_site", sa.String(length=16), nullable=True),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=True),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_id", sa.String(length=128), nullable=True),
        sa.Column("revalidation_id", sa.String(length=128), nullable=True),
        sa.Column("term_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("term_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revalidated_runtime_instance_id", sa.String(length=128), nullable=True),
        sa.Column("clock_floor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fenced", sa.Boolean(), nullable=False),
        sa.Column("fence_reason", sa.String(length=128), nullable=True),
        sa.Column("requires_fresh_witness_revalidation", sa.Boolean(), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("commit_sha256", sa.String(length=64), nullable=False),
        sa.Column("operation_kind", sa.String(length=32), nullable=True),
        sa.Column("operation_opened_state_revision", sa.BigInteger(), nullable=True),
        sa.Column("operation_fence_generation", sa.BigInteger(), nullable=True),
        sa.Column("operation_evidence_id", sa.String(length=128), nullable=True),
        sa.Column("operation_writer_epoch", sa.BigInteger(), nullable=True),
        sa.Column("operation_writer_lease_id", sa.String(length=128), nullable=True),
        sa.Column("operation_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("control_boundary", sa.String(length=128), nullable=False),
        sa.Column("control_role_label", sa.String(length=128), nullable=False),
        sa.Column("control_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_BINDING_CHECK, name="ck_owa_commits_binding"),
        sa.CheckConstraint(
            "transition_kind IN "
            "('bootstrap', 'runtime_restore', 'witness_revalidation', 'local_fence', 'writer_admission')",
            name="ck_owa_commits_transition_kind",
        ),
        sa.CheckConstraint(
            "prior_revision >= -1 AND next_revision = prior_revision + 1",
            name="ck_owa_commits_revision_chain",
        ),
        sa.CheckConstraint(
            "prior_fence_generation >= 0 AND next_fence_generation >= 0 "
            "AND highest_writer_epoch >= 0",
            name="ck_owa_commits_nonnegative_counters",
        ),
        sa.CheckConstraint(_TERM_CHECK, name="ck_owa_commits_term"),
        sa.CheckConstraint(_FENCE_CHECK, name="ck_owa_commits_fence"),
        sa.CheckConstraint(
            "revalidated_runtime_instance_id IS NULL "
            "OR revalidated_runtime_instance_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'",
            name="ck_owa_commits_runtime_instance",
        ),
        sa.CheckConstraint(
            # Avoid ``(?:0...)`` because SQLAlchemy treats ``:0`` in a
            # textual check as a bind-marker candidate when rendering DDL.
            "prior_state_sha256 ~ '^(0{64}|[0-9a-f]{64})$' "
            "AND previous_commit_sha256 ~ '^(0{64}|[0-9a-f]{64})$' "
            "AND state_sha256 ~ '^[0-9a-f]{64}$' "
            "AND receipt_sha256 ~ '^[0-9a-f]{64}$' "
            "AND commit_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_owa_commits_digests",
        ),
        sa.CheckConstraint(_CONTROL_CHECK, name="ck_owa_commits_control_metadata"),
        sa.CheckConstraint(
            "(transition_kind = 'writer_admission' "
            "AND operation_kind IN ('transaction_commit', 'external_effect') "
            "AND operation_opened_state_revision >= 0 "
            "AND operation_opened_state_revision <= prior_revision "
            "AND operation_fence_generation = prior_fence_generation "
            "AND operation_evidence_id = evidence_id "
            "AND operation_writer_epoch = writer_epoch "
            "AND operation_writer_lease_id = writer_lease_id "
            "AND operation_opened_at IS NOT NULL "
            "AND admitted_at IS NOT NULL "
            "AND clock_floor = admitted_at) "
            "OR "
            "(transition_kind <> 'writer_admission' "
            "AND operation_kind IS NULL "
            "AND operation_opened_state_revision IS NULL "
            "AND operation_fence_generation IS NULL "
            "AND operation_evidence_id IS NULL "
            "AND operation_writer_epoch IS NULL "
            "AND operation_writer_lease_id IS NULL "
            "AND operation_opened_at IS NULL "
            "AND admitted_at IS NULL)",
            name="ck_owa_commits_writer_operation",
        ),
        sa.ForeignKeyConstraint(
            ["head_id", "cluster_id", "local_site", "release_sha", "generation_id"],
            [
                "operational_writer_admission_heads.id",
                "operational_writer_admission_heads.cluster_id",
                "operational_writer_admission_heads.local_site",
                "operational_writer_admission_heads.release_sha",
                "operational_writer_admission_heads.generation_id",
            ],
            name="fk_owa_commits_head_binding",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("head_id", "id", name="ux_owa_commits_head_id"),
        sa.UniqueConstraint("head_id", "next_revision", name="ux_owa_commits_head_revision"),
        sa.UniqueConstraint("receipt_sha256", name="ux_owa_commits_receipt_sha256"),
        sa.UniqueConstraint("commit_sha256", name="ux_owa_commits_commit_sha256"),
        sa.UniqueConstraint("head_id", "receipt_sha256", name="ux_owa_commits_head_receipt_sha256"),
    )
    op.create_index(
        "ux_owa_commits_head_revalidation_once",
        "operational_writer_admission_commits",
        ["head_id", "revalidation_id"],
        unique=True,
        postgresql_where=sa.text("transition_kind = 'witness_revalidation'"),
    )
    op.create_index(
        "ux_owa_commits_head_evidence_once",
        "operational_writer_admission_commits",
        ["head_id", "evidence_id"],
        unique=True,
        postgresql_where=sa.text("transition_kind = 'witness_revalidation'"),
    )
    op.create_index(
        "ix_owa_commits_head_committed_at",
        "operational_writer_admission_commits",
        ["head_id", "committed_at"],
        unique=False,
    )

    # The composite FK prevents a mutable head from naming a commit belonging
    # to another head.  It is deferred to permit a single transaction to make
    # the mutually-referencing bootstrap head and receipt visible together.
    op.create_foreign_key(
        "fk_owa_heads_current_commit",
        "operational_writer_admission_heads",
        "operational_writer_admission_commits",
        ["id", "current_commit_id"],
        ["head_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    # Commit rows are immutable evidence.  A database superuser can always
    # bypass DDL; this trigger therefore does not assert host/root authority.
    op.execute(
        f"""
        CREATE FUNCTION {_COMMIT_MUTATION_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'operational writer-admission commit rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_HEAD_DELETE_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'operational writer-admission heads cannot be deleted or truncated';
        END;
        $$;
        """
    )

    # Insert locks the current binding head before allowing an immutable
    # successor.  It proves every normal receipt extends the exact current
    # revision/fence/state/commit head; the later head UPDATE is checked below.
    op.execute(
        f"""
        CREATE FUNCTION {_COMMIT_INSERT_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            current_head operational_writer_admission_heads%ROWTYPE;
        BEGIN
            SELECT * INTO current_head
            FROM operational_writer_admission_heads
            WHERE id = NEW.head_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission commit requires an existing locked head';
            END IF;
            IF NEW.cluster_id <> current_head.cluster_id
                OR NEW.local_site <> current_head.local_site
                OR NEW.release_sha <> current_head.release_sha
                OR NEW.generation_id <> current_head.generation_id
                OR NEW.control_boundary <> current_head.control_boundary
                OR NEW.control_role_label <> current_head.control_role_label
                OR NEW.control_policy_sha256 <> current_head.control_policy_sha256 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission commit binding/control metadata mismatch';
            END IF;

            IF NEW.id = current_head.current_commit_id THEN
                -- Bootstrap is the only allowed head insert shape.  It
                -- materializes the pure V1 fenced startup state at revision 0.
                IF NEW.transition_kind <> 'bootstrap'
                    OR current_head.revision <> 0
                    OR current_head.prior_revision <> -1
                    OR NEW.prior_revision <> -1
                    OR NEW.next_revision <> 0
                    OR NEW.prior_fence_generation <> 0
                    OR NEW.next_fence_generation <> current_head.fence_generation
                    OR current_head.highest_writer_epoch <> 0
                    OR current_head.holder_site IS NOT NULL
                    OR current_head.writer_epoch IS NOT NULL
                    OR current_head.writer_lease_id IS NOT NULL
                    OR current_head.evidence_id IS NOT NULL
                    OR current_head.revalidation_id IS NOT NULL
                    OR current_head.term_issued_at IS NOT NULL
                    OR current_head.term_expires_at IS NOT NULL
                    OR current_head.revalidated_runtime_instance_id IS NOT NULL
                    OR current_head.clock_floor IS NOT NULL
                    OR current_head.fence_generation <> 0
                    OR current_head.fenced IS NOT TRUE
                    OR current_head.fence_reason <> 'startup_requires_fresh_witness'
                    OR current_head.requires_fresh_witness_revalidation IS NOT TRUE
                    OR NEW.prior_state_sha256 <> '{_ZERO_SHA256}'
                    OR NEW.previous_commit_sha256 <> '{_ZERO_SHA256}'
                    OR NEW.state_sha256 <> current_head.state_sha256
                    OR NEW.receipt_sha256 <> current_head.receipt_sha256
                    OR NEW.commit_sha256 <> current_head.current_commit_sha256 THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'operational writer-admission bootstrap commit is inconsistent with head';
                END IF;
            ELSE
                IF NEW.transition_kind = 'bootstrap'
                    OR NEW.prior_revision <> current_head.revision
                    OR NEW.next_revision <> current_head.revision + 1
                    OR NEW.prior_fence_generation <> current_head.fence_generation
                    OR NEW.prior_state_sha256 <> current_head.state_sha256
                    OR NEW.previous_commit_sha256 <> current_head.current_commit_sha256 THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'operational writer-admission commit does not extend the current head';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    # A head cannot be advanced unless the already-inserted immutable receipt
    # is its exact successor.  The adapter still supplies a conditional UPDATE
    # (and locks the row) so a failed CAS fails closed before application work
    # is committed.
    op.execute(
        f"""
        CREATE FUNCTION {_HEAD_UPDATE_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            successor operational_writer_admission_commits%ROWTYPE;
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.cluster_id <> OLD.cluster_id
                OR NEW.local_site <> OLD.local_site
                OR NEW.release_sha <> OLD.release_sha
                OR NEW.generation_id <> OLD.generation_id
                OR NEW.control_boundary <> OLD.control_boundary
                OR NEW.control_role_label <> OLD.control_role_label
                OR NEW.control_policy_sha256 <> OLD.control_policy_sha256 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission binding/control metadata is immutable';
            END IF;
            IF NEW.revision <> OLD.revision + 1
                OR NEW.prior_revision <> OLD.revision
                OR NEW.current_commit_id = OLD.current_commit_id
                OR NEW.highest_writer_epoch < OLD.highest_writer_epoch
                OR NEW.fence_generation < OLD.fence_generation
                OR (
                    OLD.clock_floor IS NOT NULL
                    AND (NEW.clock_floor IS NULL OR NEW.clock_floor < OLD.clock_floor)
                ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission head update is not a one-step CAS advance';
            END IF;

            SELECT * INTO successor
            FROM operational_writer_admission_commits
            WHERE id = NEW.current_commit_id AND head_id = OLD.id;
            IF NOT FOUND
                OR successor.transition_kind = 'bootstrap'
                OR successor.prior_revision <> OLD.revision
                OR successor.next_revision <> NEW.revision
                OR successor.prior_fence_generation <> OLD.fence_generation
                OR successor.next_fence_generation <> NEW.fence_generation
                OR successor.prior_state_sha256 <> OLD.state_sha256
                OR successor.previous_commit_sha256 <> OLD.current_commit_sha256
                OR successor.state_sha256 <> NEW.state_sha256
                OR successor.receipt_sha256 <> NEW.receipt_sha256
                OR successor.commit_sha256 <> NEW.current_commit_sha256
                OR successor.highest_writer_epoch <> NEW.highest_writer_epoch
                OR successor.holder_site IS DISTINCT FROM NEW.holder_site
                OR successor.writer_epoch IS DISTINCT FROM NEW.writer_epoch
                OR successor.writer_lease_id IS DISTINCT FROM NEW.writer_lease_id
                OR successor.evidence_id IS DISTINCT FROM NEW.evidence_id
                OR successor.revalidation_id IS DISTINCT FROM NEW.revalidation_id
                OR successor.term_issued_at IS DISTINCT FROM NEW.term_issued_at
                OR successor.term_expires_at IS DISTINCT FROM NEW.term_expires_at
                OR successor.revalidated_runtime_instance_id IS DISTINCT FROM NEW.revalidated_runtime_instance_id
                OR successor.clock_floor IS DISTINCT FROM NEW.clock_floor
                OR successor.fenced <> NEW.fenced
                OR successor.fence_reason IS DISTINCT FROM NEW.fence_reason
                OR successor.requires_fresh_witness_revalidation <> NEW.requires_fresh_witness_revalidation
                OR successor.committed_at <> NEW.committed_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission head successor receipt mismatch';
            END IF;

            -- The database does not replace the pure V1 state-machine
            -- validation, but it does close the high-impact rollback and
            -- fence-bypass shapes even if a future adapter is miswired.
            IF successor.transition_kind = 'writer_admission' AND (
                NEW.highest_writer_epoch <> OLD.highest_writer_epoch
                OR NEW.fence_generation <> OLD.fence_generation
                OR NEW.holder_site IS DISTINCT FROM OLD.holder_site
                OR NEW.writer_epoch IS DISTINCT FROM OLD.writer_epoch
                OR NEW.writer_lease_id IS DISTINCT FROM OLD.writer_lease_id
                OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.revalidation_id IS DISTINCT FROM OLD.revalidation_id
                OR NEW.term_issued_at IS DISTINCT FROM OLD.term_issued_at
                OR NEW.term_expires_at IS DISTINCT FROM OLD.term_expires_at
                OR NEW.revalidated_runtime_instance_id IS DISTINCT FROM OLD.revalidated_runtime_instance_id
                OR NEW.fenced IS NOT FALSE
                OR NEW.fence_reason IS NOT NULL
                OR NEW.requires_fresh_witness_revalidation IS NOT FALSE
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission receipt cannot change its active term or fence';
            END IF;

            IF successor.transition_kind = 'local_fence' AND (
                NEW.highest_writer_epoch <> OLD.highest_writer_epoch
                OR NEW.fence_generation <> OLD.fence_generation + 1
                OR NEW.holder_site IS DISTINCT FROM OLD.holder_site
                OR NEW.writer_epoch IS DISTINCT FROM OLD.writer_epoch
                OR NEW.writer_lease_id IS DISTINCT FROM OLD.writer_lease_id
                OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.revalidation_id IS DISTINCT FROM OLD.revalidation_id
                OR NEW.term_issued_at IS DISTINCT FROM OLD.term_issued_at
                OR NEW.term_expires_at IS DISTINCT FROM OLD.term_expires_at
                OR NEW.revalidated_runtime_instance_id IS DISTINCT FROM OLD.revalidated_runtime_instance_id
                OR NEW.fenced IS NOT TRUE
                OR NEW.requires_fresh_witness_revalidation IS NOT TRUE
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission local fence is not monotonic';
            END IF;

            IF successor.transition_kind = 'runtime_restore' AND (
                NEW.highest_writer_epoch <> OLD.highest_writer_epoch
                OR NEW.fence_generation <> OLD.fence_generation + 1
                OR NEW.holder_site IS DISTINCT FROM OLD.holder_site
                OR NEW.writer_epoch IS DISTINCT FROM OLD.writer_epoch
                OR NEW.writer_lease_id IS DISTINCT FROM OLD.writer_lease_id
                OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.revalidation_id IS DISTINCT FROM OLD.revalidation_id
                OR NEW.term_issued_at IS DISTINCT FROM OLD.term_issued_at
                OR NEW.term_expires_at IS DISTINCT FROM OLD.term_expires_at
                OR NEW.revalidated_runtime_instance_id IS NOT NULL
                OR NEW.fenced IS DISTINCT FROM OLD.fenced
                OR NEW.fence_reason IS DISTINCT FROM (
                    CASE WHEN OLD.fenced THEN OLD.fence_reason ELSE NULL END
                )
                OR NEW.requires_fresh_witness_revalidation IS NOT TRUE
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission runtime restore is not a fresh-only fence advance';
            END IF;

            IF successor.transition_kind = 'witness_revalidation' AND (
                NEW.fence_generation <> OLD.fence_generation
                OR NEW.fenced IS NOT FALSE
                OR NEW.requires_fresh_witness_revalidation IS NOT FALSE
                OR NEW.holder_site IS NULL
                OR NEW.revalidated_runtime_instance_id IS NULL
                OR (OLD.fenced IS TRUE AND NEW.highest_writer_epoch <= OLD.highest_writer_epoch)
                OR (
                    OLD.holder_site IS NOT NULL
                    AND (
                        NEW.evidence_id = OLD.evidence_id
                        OR NEW.revalidation_id = OLD.revalidation_id
                        OR NEW.term_issued_at <= OLD.term_issued_at
                        OR (
                            NEW.highest_writer_epoch = OLD.highest_writer_epoch
                            AND NEW.writer_lease_id <> OLD.writer_lease_id
                        )
                        OR (
                            NEW.highest_writer_epoch > OLD.highest_writer_epoch
                            AND NEW.writer_lease_id = OLD.writer_lease_id
                        )
                    )
                )
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission witness revalidation violates term monotonicity';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    # The bootstrap head references its receipt before that receipt exists.
    # This deferred check validates the same complete projection at the end of
    # the transaction, preserving all-or-nothing initial materialization.
    op.execute(
        f"""
        CREATE FUNCTION {_HEAD_ASSERT_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM operational_writer_admission_commits AS commit_row
                WHERE commit_row.id = NEW.current_commit_id
                  AND commit_row.head_id = NEW.id
                  AND commit_row.next_revision = NEW.revision
                  AND commit_row.next_fence_generation = NEW.fence_generation
                  AND commit_row.state_sha256 = NEW.state_sha256
                  AND commit_row.receipt_sha256 = NEW.receipt_sha256
                  AND commit_row.commit_sha256 = NEW.current_commit_sha256
                  AND commit_row.highest_writer_epoch = NEW.highest_writer_epoch
                  AND commit_row.holder_site IS NOT DISTINCT FROM NEW.holder_site
                  AND commit_row.writer_epoch IS NOT DISTINCT FROM NEW.writer_epoch
                  AND commit_row.writer_lease_id IS NOT DISTINCT FROM NEW.writer_lease_id
                  AND commit_row.evidence_id IS NOT DISTINCT FROM NEW.evidence_id
                  AND commit_row.revalidation_id IS NOT DISTINCT FROM NEW.revalidation_id
                  AND commit_row.term_issued_at IS NOT DISTINCT FROM NEW.term_issued_at
                  AND commit_row.term_expires_at IS NOT DISTINCT FROM NEW.term_expires_at
                  AND commit_row.revalidated_runtime_instance_id IS NOT DISTINCT FROM NEW.revalidated_runtime_instance_id
                  AND commit_row.clock_floor IS NOT DISTINCT FROM NEW.clock_floor
                  AND commit_row.fenced = NEW.fenced
                  AND commit_row.fence_reason IS NOT DISTINCT FROM NEW.fence_reason
                  AND commit_row.requires_fresh_witness_revalidation = NEW.requires_fresh_witness_revalidation
                  AND commit_row.control_boundary = NEW.control_boundary
                  AND commit_row.control_role_label = NEW.control_role_label
                  AND commit_row.control_policy_sha256 = NEW.control_policy_sha256
                  AND commit_row.committed_at = NEW.committed_at
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'operational writer-admission head lacks its exact immutable current receipt';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )

    op.execute(
        f"""
        CREATE TRIGGER trg_owa_commits_validate_insert
        BEFORE INSERT ON operational_writer_admission_commits
        FOR EACH ROW EXECUTE FUNCTION {_COMMIT_INSERT_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_owa_commits_append_only_row
        BEFORE UPDATE OR DELETE ON operational_writer_admission_commits
        FOR EACH ROW EXECUTE FUNCTION {_COMMIT_MUTATION_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_owa_commits_append_only_truncate
        BEFORE TRUNCATE ON operational_writer_admission_commits
        FOR EACH STATEMENT EXECUTE FUNCTION {_COMMIT_MUTATION_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_owa_heads_validate_update
        BEFORE UPDATE ON operational_writer_admission_heads
        FOR EACH ROW EXECUTE FUNCTION {_HEAD_UPDATE_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_owa_heads_reject_delete
        BEFORE DELETE ON operational_writer_admission_heads
        FOR EACH ROW EXECUTE FUNCTION {_HEAD_DELETE_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_owa_heads_reject_truncate
        BEFORE TRUNCATE ON operational_writer_admission_heads
        FOR EACH STATEMENT EXECUTE FUNCTION {_HEAD_DELETE_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE CONSTRAINT TRIGGER trg_owa_heads_current_commit_consistent
        AFTER INSERT OR UPDATE ON operational_writer_admission_heads
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION {_HEAD_ASSERT_FUNCTION}();
        """
    )


def downgrade() -> None:
    # These records are a durable audit chain.  Refuse to erase them merely to
    # roll schema back; a deliberate empty-environment downgrade remains safe.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM operational_writer_admission_heads)
                OR EXISTS (SELECT 1 FROM operational_writer_admission_commits) THEN
                RAISE EXCEPTION
                    'refusing destructive operational writer-admission downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_owa_heads_current_commit_consistent "
        "ON operational_writer_admission_heads"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_owa_heads_reject_truncate ON operational_writer_admission_heads")
    op.execute("DROP TRIGGER IF EXISTS trg_owa_heads_reject_delete ON operational_writer_admission_heads")
    op.execute("DROP TRIGGER IF EXISTS trg_owa_heads_validate_update ON operational_writer_admission_heads")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_owa_commits_append_only_truncate "
        "ON operational_writer_admission_commits"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_owa_commits_append_only_row "
        "ON operational_writer_admission_commits"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_owa_commits_validate_insert "
        "ON operational_writer_admission_commits"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_HEAD_ASSERT_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_HEAD_UPDATE_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_COMMIT_INSERT_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_HEAD_DELETE_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_COMMIT_MUTATION_FUNCTION}()")
    op.drop_constraint(
        "fk_owa_heads_current_commit",
        "operational_writer_admission_heads",
        type_="foreignkey",
    )
    op.drop_index("ix_owa_commits_head_committed_at", table_name="operational_writer_admission_commits")
    op.drop_index("ux_owa_commits_head_evidence_once", table_name="operational_writer_admission_commits")
    op.drop_index("ux_owa_commits_head_revalidation_once", table_name="operational_writer_admission_commits")
    op.drop_table("operational_writer_admission_commits")
    op.drop_table("operational_writer_admission_heads")
