"""add dual-platform registration stage 1 foundation

Revision ID: a8d9e0f1b2c3
Revises: f7c8d9e0a1b2
Create Date: 2026-07-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from core.registration_identity import (
    NORMALIZED_ACCOUNT_NAME_SQL,
    NORMALIZED_MOBILE_NUMBER_SQL,
    canonical_account_name_sql,
    canonical_mobile_number_sql,
)


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

normalized_account_sql = NORMALIZED_ACCOUNT_NAME_SQL
normalized_mobile_sql = NORMALIZED_MOBILE_NUMBER_SQL
normalized_invitation_account_sql = canonical_account_name_sql("i.account_name")
normalized_invitation_mobile_sql = canonical_mobile_number_sql("i.mobile_number")
normalized_accountant_account_sql = canonical_account_name_sql("ar.global_account_name")
normalized_accountant_mobile_sql = canonical_mobile_number_sql("ar.mobile_number")


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
    op.add_column(
        "users",
        sa.Column("counter_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint(
        "ck_users_counter_epoch_positive",
        "users",
        "counter_epoch >= 1",
    )


def _add_user_canonical_identity() -> None:
    # Raw unique keys do not catch Persian/Arabic digit, case, or surrounding
    # whitespace variants. Abort without exposing values rather than choosing a
    # historical winner that could bind registration to the wrong User.
    op.execute(
        f"""
        DO $$
        DECLARE
            collision_group_count bigint;
        BEGIN
            SELECT COUNT(*)
            INTO collision_group_count
            FROM (
                SELECT {normalized_mobile_sql} AS canonical_value
                FROM users
                GROUP BY canonical_value
                HAVING COUNT(*) > 1
                UNION ALL
                SELECT {normalized_account_sql} AS canonical_value
                FROM users
                GROUP BY canonical_value
                HAVING COUNT(*) > 1
            ) collisions;

            IF collision_group_count > 0 THEN
                RAISE EXCEPTION
                    'canonical User identity audit found % collision groups',
                    collision_group_count;
            END IF;
        END
        $$;
        """
    )
    op.add_column(
        "users",
        sa.Column(
            "normalized_account_name",
            sa.String(),
            sa.Computed(normalized_account_sql, persisted=True),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "normalized_mobile_number",
            sa.String(),
            sa.Computed(normalized_mobile_sql, persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ux_users_normalized_account_name",
        "users",
        ["normalized_account_name"],
        unique=True,
    )
    op.create_index(
        "ux_users_normalized_mobile_number",
        "users",
        ["normalized_mobile_number"],
        unique=True,
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

    # A used flag and matching natural keys do not prove completion: account
    # deletion historically invalidated invitations by setting is_used. Only a
    # relation row linked to the same User plus an in-window activation timestamp
    # is sufficiently strong evidence. Legacy standard invitations stay
    # intentionally ambiguous because they have no equivalent durable relation.
    op.execute(
        f"""
        UPDATE invitations AS i
        SET registered_user_id = u.id,
            completed_at = ar.activated_at,
            completed_via = 'web'::invitationcompletionsurface,
            updated_at = COALESCE(i.updated_at, ar.activated_at)
        FROM accountant_relations AS ar
        JOIN users AS u ON u.id = ar.accountant_user_id
        WHERE COALESCE(i.is_used, false) = true
          AND i.kind = 'accountant'::invitationkind
          AND ar.invitation_token = i.token
          AND ar.status::text IN ('active', 'expired')
          AND ar.deleted_at IS NULL
          AND ar.activated_at IS NOT NULL
          AND u.normalized_account_name = {normalized_invitation_account_sql}
          AND u.normalized_mobile_number = {normalized_invitation_mobile_sql}
          AND u.normalized_account_name = {normalized_accountant_account_sql}
          AND u.normalized_mobile_number = {normalized_accountant_mobile_sql}
          AND COALESCE(u.is_deleted, false) = false
          AND u.account_status::text = 'active'
          AND (i.created_at IS NULL OR ar.activated_at >= i.created_at)
          AND ar.activated_at <= i.expires_at
        """
    )
    op.execute(
        f"""
        UPDATE invitations AS i
        SET registered_user_id = u.id,
            completed_at = cr.activated_at,
            completed_via = 'web'::invitationcompletionsurface,
            updated_at = COALESCE(i.updated_at, cr.activated_at)
        FROM customer_relations AS cr
        JOIN users AS u ON u.id = cr.customer_user_id
        WHERE COALESCE(i.is_used, false) = true
          AND i.kind = 'customer'::invitationkind
          AND cr.invitation_token = i.token
          AND cr.status::text IN ('active', 'expired')
          AND cr.deleted_at IS NULL
          AND cr.activated_at IS NOT NULL
          AND u.normalized_account_name = {normalized_invitation_account_sql}
          AND u.normalized_mobile_number = {normalized_invitation_mobile_sql}
          AND COALESCE(u.is_deleted, false) = false
          AND u.account_status::text = 'active'
          AND (i.created_at IS NULL OR cr.activated_at >= i.created_at)
          AND cr.activated_at <= i.expires_at
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
        sa.CheckConstraint(
            "((outcome_code IS NULL AND completed_at IS NULL AND authoritative_user_id IS NULL) "
            "OR (outcome_code IS NOT NULL AND completed_at IS NOT NULL))",
            name="ck_telegram_registration_receipts_terminal_atomic",
        ),
        sa.CheckConstraint(
            "((outcome_code IN ('created', 'linked_existing', 'already_linked') "
            "AND authoritative_user_id IS NOT NULL) "
            "OR (outcome_code IS NULL AND authoritative_user_id IS NULL) "
            "OR (outcome_code NOT IN ('created', 'linked_existing', 'already_linked') "
            "AND authoritative_user_id IS NULL))",
            name="ck_telegram_registration_receipts_user_outcome",
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

    op.create_table(
        "user_counter_event_receipts",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_server", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("event_kind", sa.String(length=16), nullable=False),
        sa.Column("event_epoch", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deltas", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default=sa.text("'applied'")),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "source_server IN ('iran', 'foreign')",
            name="ck_user_counter_event_receipts_known_source",
        ),
        sa.CheckConstraint(
            "length(event_hash) = 64",
            name="ck_user_counter_event_receipts_event_hash",
        ),
        sa.CheckConstraint(
            "event_kind IN ('increment', 'reset')",
            name="ck_user_counter_event_receipts_known_kind",
        ),
        sa.CheckConstraint(
            "event_epoch >= 1",
            name="ck_user_counter_event_receipts_epoch_positive",
        ),
        sa.CheckConstraint(
            "outcome IN ('applied', 'excluded_pre_boundary')",
            name="ck_user_counter_event_receipts_known_outcome",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_user_counter_event_receipts_user_id",
        "user_counter_event_receipts",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_counter_event_receipts_user_period",
        "user_counter_event_receipts",
        ["user_id", "event_kind", "occurred_at", "event_epoch"],
        unique=False,
    )
    op.create_index(
        "ux_user_counter_event_receipts_user_reset_epoch",
        "user_counter_event_receipts",
        ["user_id", "event_epoch"],
        unique=True,
        postgresql_where=sa.text("event_kind = 'reset'"),
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
    _add_user_canonical_identity()
    _add_invitation_metadata()
    _create_identity_reservations()
    _create_registration_local_state()


def downgrade() -> None:
    op.drop_index(
        "ux_user_counter_event_receipts_user_reset_epoch",
        table_name="user_counter_event_receipts",
    )
    op.drop_index(
        "ix_user_counter_event_receipts_user_period",
        table_name="user_counter_event_receipts",
    )
    op.drop_index(
        "ix_user_counter_event_receipts_user_id",
        table_name="user_counter_event_receipts",
    )
    op.drop_table("user_counter_event_receipts")

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

    op.drop_index("ux_users_normalized_mobile_number", table_name="users")
    op.drop_index("ux_users_normalized_account_name", table_name="users")
    op.drop_column("users", "normalized_mobile_number")
    op.drop_column("users", "normalized_account_name")

    op.drop_constraint("ck_users_counter_epoch_positive", "users", type_="check")
    op.drop_column("users", "counter_epoch")

    for table_name in ("accountant_relations", "customer_relations", "invitations", "users"):
        op.drop_constraint(f"ck_{table_name}_sync_version_positive", table_name, type_="check")
        op.drop_column(table_name, "sync_version")

    telegram_registration_intent_status.drop(op.get_bind(), checkfirst=True)
    invitation_completion_surface.drop(op.get_bind(), checkfirst=True)
    invitation_kind.drop(op.get_bind(), checkfirst=True)
