"""add session management tables and max_sessions

Revision ID: b5f8a2c3d4e6
Revises: 0094d3227c25
Create Date: 2026-04-04 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'b5f8a2c3d4e6'
down_revision: Union[str, None] = '0094d3227c25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===== 1. Add max_sessions to users =====
    op.add_column('users', sa.Column('max_sessions', sa.Integer(), nullable=False, server_default='1'))

    # ===== 2. Drop old user_sessions table (was basic integer PK, replacing with UUID-based) =====
    op.drop_index('ix_user_sessions_device_fingerprint', table_name='user_sessions', if_exists=True)
    op.drop_index('ix_user_sessions_id', table_name='user_sessions', if_exists=True)
    op.drop_table('user_sessions')

    # Drop old enum type
    op.execute("DROP TYPE IF EXISTS platform CASCADE;")

    # ===== 3. Create new user_sessions table =====
    op.create_table(
        'user_sessions',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('device_name', sa.String(255), nullable=False, server_default='Unknown Device'),
        sa.Column('device_ip', sa.String(45), nullable=True),
        sa.Column('platform', sa.Enum('telegram_mini_app', 'web', 'android', name='platform'), nullable=False, server_default='web'),
        sa.Column('refresh_token_hash', sa.String(255), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_index('ix_user_sessions_is_active', 'user_sessions', ['is_active'])
    op.create_index('ix_user_sessions_refresh_token_hash', 'user_sessions', ['refresh_token_hash'])

    # ===== 4. Create session_login_requests table =====
    op.create_table(
        'session_login_requests',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('requester_device_name', sa.String(255), nullable=False, server_default='Unknown Device'),
        sa.Column('requester_ip', sa.String(45), nullable=True),
        sa.Column('status', sa.Enum('pending', 'approved', 'rejected', 'expired', name='loginrequeststatus'), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_by_session_id', UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['resolved_by_session_id'], ['user_sessions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_session_login_requests_user_id', 'session_login_requests', ['user_id'])
    op.create_index('ix_session_login_requests_status', 'session_login_requests', ['status'])


def downgrade() -> None:
    # Drop session_login_requests
    op.drop_index('ix_session_login_requests_status', table_name='session_login_requests')
    op.drop_index('ix_session_login_requests_user_id', table_name='session_login_requests')
    op.drop_table('session_login_requests')
    op.execute("DROP TYPE IF EXISTS loginrequeststatus;")

    # Drop new user_sessions
    op.drop_index('ix_user_sessions_refresh_token_hash', table_name='user_sessions')
    op.drop_index('ix_user_sessions_is_active', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')
    op.execute("DROP TYPE IF EXISTS platform;")

    # Remove max_sessions from users
    op.drop_column('users', 'max_sessions')
