"""add dual-platform registration stage 1 foundation

Revision ID: a8d9e0f1b2c3
Revises: f7c8d9e0a1b2
Create Date: 2026-07-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a8d9e0f1b2c3"
down_revision: Union[str, Sequence[str], None] = "f7c8d9e0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


invitation_kind = postgresql.ENUM(
    "standard",
    "accountant",
    "customer",
    "legacy_unknown",
    name="invitationkind",
    create_type=False,
)
invitation_completion_surface = postgresql.ENUM(
    "web",
    "telegram",
    name="invitationcompletionsurface",
    create_type=False,
)
telegram_registration_intent_status = postgresql.ENUM(
    "collecting",
    "ready",
    "forwarding",
    "retry_wait",
    "reconciled_created",
    "reconciled_linked_existing",
    "reconciled_already_linked",
    "rejected",
    "expired",
    name="telegramregistrationintentstatus",
    create_type=False,
)


def _create_enum_types() -> None:
    bind = op.get_bind()
    invitation_kind.create(bind, checkfirst=True)
    invitation_completion_surface.create(bind, checkfirst=True)
    telegram_registration_intent_status.create(bind, checkfirst=True)


def _add_version_columns() -> None:
    for table_name in ("users", "invitations", "customer_relations", "accountant_relations"):
        op.add_column(
            table_name,
            sa.Column("sync_version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        )
        op.create_check_constraint(
            f"ck_{table_name}_sync_version_positive",
            table_name,
            "sync_version >= 1",
        )


def _add_invitation_metadata() -> None:
    op.add_column("invitations", sa.Column("kind", invitation_kind, nullable=True))
    op.add_column("invitations", sa.Column("registered_user_id", sa.Integer(), nullable=True))
    op.add_column("invitations", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invitations", sa.Column("completed_via", invitation_completion_surface, nullable=True))
    op.add_column("invitations", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invitations", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        WITH evidence AS (
            SELECT
                i.id,
                EXISTS (
                    SELECT 1 FROM accountant_relations ar
                    WHERE ar.invitation_token = i.token
                ) AS has_accountant_relation,
                EXISTS (
                    SELECT 1 FROM customer_relations cr
                    WHERE cr.invitation_token = i.token
                ) AS has_customer_relation,
                CASE
                    WHEN i.token LIKE 'INV-%' THEN 'standard'
                    WHEN i.token LIKE 'ACCT-%' THEN 'accountant'
                    WHEN i.token LIKE 'CUST-%' THEN 'customer'
                    ELSE NULL
                END AS prefix_kind
            FROM invitations i
        )
        UPDATE invitations AS i
        SET kind = (
            CASE
                WHEN e.has_accountant_relation
                     AND NOT e.has_customer_relation
                     AND (e.prefix_kind IS NULL OR e.prefix_kind = 'accountant')
                    THEN 'accountant'::invitationkind
                WHEN e.has_customer_relation
                     AND NOT e.has_accountant_relation
                     AND (e.prefix_kind IS NULL OR e.prefix_kind = 'customer')
                    THEN 'customer'::invitationkind
                WHEN NOT e.has_accountant_relation
                     AND NOT e.has_customer_relation
                     AND e.prefix_kind IS NOT NULL
                    THEN e.prefix_kind::invitationkind
                ELSE 'legacy_unknown'::invitationkind
            END
        )
        FROM evidence e
        WHERE e.id = i.id
        """
    )

    # All historical completion paths were Web-only. Backfill only when one active,
    # non-deleted User matches both natural keys and has a trustworthy created_at.
    op.execute(
        """
        UPDATE invitations AS i
        SET registered_user_id = u.id,
            completed_at = u.created_at,
            completed_via = 'web'::invitationcompletionsurface,
            updated_at = COALESCE(i.updated_at, u.created_at)
        FROM users AS u
        WHERE COALESCE(i.is_used, false) = true
          AND i.kind <> 'legacy_unknown'::invitationkind
          AND u.account_name = i.account_name
          AND u.mobile_number = i.mobile_number
          AND COALESCE(u.is_deleted, false) = false
          AND u.account_status::text = 'active'
          AND u.created_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM users conflict_user
              WHERE conflict_user.id <> u.id
                AND (
                    conflict_user.account_name = i.account_name
                    OR conflict_user.mobile_number = i.mobile_number
                )
          )
        """
    )

    op.alter_column(
        "invitations",
        "kind",
        existing_type=invitation_kind,
        nullable=False,
        server_default=sa.text("'legacy_unknown'"),
    )
    op.create_foreign_key(
        "fk_invitations_registered_user_id_users",
        "invitations",
        "users",
        ["registered_user_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_invitations_completion_metadata_atomic",
        "invitations",
        "((registered_user_id IS NULL AND completed_at IS NULL AND completed_via IS NULL) "
        "OR (registered_user_id IS NOT NULL AND completed_at IS NOT NULL "
        "AND completed_via IS NOT NULL AND is_used = true))",
    )
    op.create_check_constraint(
        "ck_invitations_not_completed_and_revoked",
        "invitations",
        "NOT (revoked_at IS NOT NULL AND completed_at IS NOT NULL)",
    )
    op.create_index("ix_invitations_kind", "invitations", ["kind"], unique=False)
    op.create_index("ix_invitations_registered_user_id", "invitations", ["registered_user_id"], unique=False)
    op.create_index("ix_invitations_revoked_at", "invitations", ["revoked_at"], unique=False)


def _create_identity_reservations() -> None:
    op.create_table(
        "invitation_identity_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invitation_id", sa.Integer(), nullable=False),
        sa.Column("normalized_mobile", sa.String(length=32), nullable=False),
        sa.Column("normalized_account_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(btrim(normalized_mobile)) > 0",
            name="ck_invitation_identity_reservations_mobile_not_blank",
        ),
        sa.CheckConstraint(
            "length(btrim(normalized_account_name)) > 0",
            name="ck_invitation_identity_reservations_account_not_blank",
        ),
        sa.ForeignKeyConstraint(["invitation_id"], ["invitations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_id", name="ux_invitation_identity_reservations_invitation_id"),
        sa.UniqueConstraint("normalized_mobile", name="ux_invitation_identity_reservations_mobile"),
        sa.UniqueConstraint("normalized_account_name", name="ux_invitation_identity_reservations_account"),
    )
    op.create_index(
        op.f("ix_invitation_identity_reservations_id"),
        "invitation_identity_reservations",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_identity_reservations_created_at",
        "invitation_identity_reservations",
        ["created_at"],
        unique=False,
    )

    # Do not choose a winner by row order. A collision aborts the migration with
    # a bounded count so operators can resolve the source data before retrying.
    op.execute(
        r"""
        DO $$
        DECLARE
            collision_count bigint;
        BEGIN
            WITH eligible AS (
                SELECT
                    i.id,
                    translate(
                        btrim(i.mobile_number),
                        U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669',
                        '01234567890123456789'
                    ) AS normalized_mobile,
                    lower(translate(
                        btrim(i.account_name),
                        U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669',
                        '01234567890123456789'
                    )) AS normalized_account_name
                FROM invitations i
                WHERE COALESCE(i.is_used, false) = false
                  AND i.revoked_at IS NULL
                  AND i.expires_at > now()
                  AND i.kind <> 'legacy_unknown'::invitationkind
            ),
            conflicting_invitations AS (
                SELECT id
                FROM eligible e
                WHERE EXISTS (
                    SELECT 1 FROM eligible other
                    WHERE other.id <> e.id
                      AND (
                          other.normalized_mobile = e.normalized_mobile
                          OR other.normalized_account_name = e.normalized_account_name
                      )
                )
            ),
            conflicting_users AS (
                SELECT e.id
                FROM eligible e
                WHERE EXISTS (
                    SELECT 1 FROM users u
                    WHERE COALESCE(u.is_deleted, false) = false
                      AND (
                          translate(
                              btrim(u.mobile_number),
                              U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669',
                              '01234567890123456789'
                          ) = e.normalized_mobile
                          OR lower(translate(
                              btrim(u.account_name),
                              U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669',
                              '01234567890123456789'
                          )) = e.normalized_account_name
                      )
                )
            )
            SELECT COUNT(DISTINCT id)
            INTO collision_count
            FROM (
                SELECT id FROM conflicting_invitations
                UNION ALL
                SELECT id FROM conflicting_users
            ) conflicts;

            IF collision_count > 0 THEN
                RAISE EXCEPTION
                    'registration reservation backfill found % conflicting pending invitations',
                    collision_count;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        r"""
        INSERT INTO invitation_identity_reservations (
            invitation_id,
            normalized_mobile,
            normalized_account_name,
            created_at,
            updated_at
        )
        SELECT
            i.id,
            translate(
                btrim(i.mobile_number),
                U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669',
                '01234567890123456789'
            ),
            lower(translate(
                btrim(i.account_name),
                U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669',
                '01234567890123456789'
            )),
            COALESCE(i.created_at, now()),
            i.updated_at
        FROM invitations i
        WHERE COALESCE(i.is_used, false) = false
          AND i.revoked_at IS NULL
          AND i.expires_at > now()
          AND i.kind <> 'legacy_unknown'::invitationkind
        """
    )


def _create_registration_local_state() -> None:
    op.create_table(
        "telegram_registration_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=192), nullable=False),
        sa.Column("invitation_token", sa.String(length=192), nullable=False),
        sa.Column("normalized_mobile", sa.String(length=32), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("telegram_full_name", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("contact_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invitation_expires_at_snapshot", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            telegram_registration_intent_status,
            nullable=False,
            server_default=sa.text("'collecting'"),
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=96), nullable=True),
        sa.Column("authoritative_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "telegram_id > 0",
            name="ck_telegram_registration_intents_telegram_id_positive",
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name="ck_telegram_registration_intents_retry_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="ux_telegram_registration_intents_idempotency_key"),
    )
    op.create_index(
        "ix_telegram_registration_intents_due",
        "telegram_registration_intents",
        ["status", "next_retry_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_registration_intents_telegram_id",
        "telegram_registration_intents",
        ["telegram_id"],
        unique=False,
    )

    op.create_table(
        "telegram_registration_command_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=192), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome_code", sa.String(length=96), nullable=True),
        sa.Column("authoritative_user_id", sa.Integer(), nullable=True),
        sa.Column("invitation_token_hash", sa.String(length=64), nullable=False),
        sa.Column("source_server", sa.String(length=16), nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_telegram_registration_receipts_request_hash",
        ),
        sa.CheckConstraint(
            "length(invitation_token_hash) = 64",
            name="ck_telegram_registration_receipts_token_hash",
        ),
        sa.CheckConstraint(
            "source_server = 'foreign'",
            name="ck_telegram_registration_receipts_source_foreign",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="ux_telegram_registration_receipts_command_id"),
        sa.UniqueConstraint("idempotency_key", name="ux_telegram_registration_receipts_idempotency_key"),
    )
    op.create_index(
        op.f("ix_telegram_registration_command_receipts_id"),
        "telegram_registration_command_receipts",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_registration_receipts_completed_at",
        "telegram_registration_command_receipts",
        ["completed_at"],
        unique=False,
    )


def upgrade() -> None:
    _create_enum_types()
    _add_version_columns()
    _add_invitation_metadata()
    _create_identity_reservations()
    _create_registration_local_state()


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_registration_receipts_completed_at",
        table_name="telegram_registration_command_receipts",
    )
    op.drop_index(
        op.f("ix_telegram_registration_command_receipts_id"),
        table_name="telegram_registration_command_receipts",
    )
    op.drop_table("telegram_registration_command_receipts")

    op.drop_index(
        "ix_telegram_registration_intents_telegram_id",
        table_name="telegram_registration_intents",
    )
    op.drop_index(
        "ix_telegram_registration_intents_due",
        table_name="telegram_registration_intents",
    )
    op.drop_table("telegram_registration_intents")

    op.drop_index(
        "ix_invitation_identity_reservations_created_at",
        table_name="invitation_identity_reservations",
    )
    op.drop_index(
        op.f("ix_invitation_identity_reservations_id"),
        table_name="invitation_identity_reservations",
    )
    op.drop_table("invitation_identity_reservations")

    op.drop_index("ix_invitations_revoked_at", table_name="invitations")
    op.drop_index("ix_invitations_registered_user_id", table_name="invitations")
    op.drop_index("ix_invitations_kind", table_name="invitations")
    op.drop_constraint("ck_invitations_not_completed_and_revoked", "invitations", type_="check")
    op.drop_constraint("ck_invitations_completion_metadata_atomic", "invitations", type_="check")
    op.drop_constraint("fk_invitations_registered_user_id_users", "invitations", type_="foreignkey")
    op.drop_column("invitations", "updated_at")
    op.drop_column("invitations", "revoked_at")
    op.drop_column("invitations", "completed_via")
    op.drop_column("invitations", "completed_at")
    op.drop_column("invitations", "registered_user_id")
    op.drop_column("invitations", "kind")

    for table_name in ("accountant_relations", "customer_relations", "invitations", "users"):
        op.drop_constraint(f"ck_{table_name}_sync_version_positive", table_name, type_="check")
        op.drop_column(table_name, "sync_version")

    telegram_registration_intent_status.drop(op.get_bind(), checkfirst=True)
    invitation_completion_surface.drop(op.get_bind(), checkfirst=True)
    invitation_kind.drop(op.get_bind(), checkfirst=True)
