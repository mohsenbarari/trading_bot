"""expand Telegram notification queue for Offer success preview edits

Revision ID: fae5f6a7b8c9
Revises: fad4e5f6a7b8
Create Date: 2026-07-18 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fae5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "fad4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACTION_SOURCES = (
    "'queue_action:account_status', "
    "'queue_action:general_announcement', "
    "'queue_action:general_immediate', "
    "'queue_action:offer_validation_response', "
    "'queue_action:targeted_admin_message', "
    "'queue_action:trade_alternative', "
    "'queue_action:trade_noncritical', "
    "'queue_action:trade_response', "
    "'queue_action:trade_unavailable'"
)


def _predicate(*, include_offer_success: bool) -> sa.TextClause:
    sources = "'project_user_joined', 'offer_repeat_response', "
    if include_offer_success:
        sources += "'offer_success_preview', "
    sources += _ACTION_SOURCES
    return sa.text(
        "status IN ('pending', 'retryable_failed') "
        f"AND source_type IN ({sources}) "
        "AND queue_job_id IS NULL AND worker_id IS NULL "
        "AND lease_until IS NULL"
    )


def _replace_index(*, include_offer_success: bool) -> None:
    op.drop_index(
        "ix_telegram_notification_outbox_queue_handoff",
        table_name="telegram_notification_outbox",
    )
    op.create_index(
        "ix_telegram_notification_outbox_queue_handoff",
        "telegram_notification_outbox",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=_predicate(
            include_offer_success=include_offer_success
        ),
    )


def upgrade() -> None:
    _replace_index(include_offer_success=True)


def downgrade() -> None:
    _replace_index(include_offer_success=False)
