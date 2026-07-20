"""Add transaction-bound durable external-effect fanout intents.

Revision ID: e5a0b1c2d3e4
Revises: e4f9a0b1c2d3
"""

import sqlalchemy as sa
from alembic import op


revision = "e5a0b1c2d3e4"
down_revision = "e4f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dr_effect_fanouts",
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("dr_events.event_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_db_id", sa.String(64), nullable=False),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("fanout_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("recipient_count", sa.Integer()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "fanout_type IN ('market_offer_webpush', 'notification_webpush')",
            name="ck_dr_effect_fanouts_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'expanded', 'skipped')",
            name="ck_dr_effect_fanouts_status",
        ),
    )
    op.create_index(
        "ix_dr_effect_fanouts_ready", "dr_effect_fanouts", ["status", "created_at"]
    )
    # Fanout intent and expansion state are local Writer-term control data.
    # A projected business event must never manufacture provider effects on a
    # standby or on Bot-FI, so this table is deliberately absent from the
    # projection allowlists.
    op.execute(
        "CREATE TRIGGER trg_three_site_writer_term BEFORE INSERT OR UPDATE OR DELETE "
        "ON dr_effect_fanouts FOR EACH ROW EXECUTE FUNCTION trading_bot_enforce_writer_term()"
    )
    op.execute(
        """
        CREATE FUNCTION trading_bot_dr_effect_fanout_intent_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
               OR NEW.aggregate_db_id IS DISTINCT FROM OLD.aggregate_db_id
               OR NEW.origin_physical_site IS DISTINCT FROM OLD.origin_physical_site
               OR NEW.writer_epoch IS DISTINCT FROM OLD.writer_epoch
               OR NEW.fanout_type IS DISTINCT FROM OLD.fanout_type
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'dr_effect_fanout immutable intent fields changed';
            END IF;
            RETURN NEW;
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_dr_effect_fanout_intent_immutable BEFORE UPDATE ON dr_effect_fanouts "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_effect_fanout_intent_immutable()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION trading_bot_dr_effect_fanout_intent_immutable() FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_three_site_writer_term ON dr_effect_fanouts")
    op.execute("DROP TRIGGER IF EXISTS trg_dr_effect_fanout_intent_immutable ON dr_effect_fanouts")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_effect_fanout_intent_immutable()")
    op.drop_index("ix_dr_effect_fanouts_ready", table_name="dr_effect_fanouts")
    op.drop_table("dr_effect_fanouts")
