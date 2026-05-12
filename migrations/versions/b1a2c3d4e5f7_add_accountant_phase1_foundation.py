"""add accountant phase1 foundation

Revision ID: b1a2c3d4e5f7
Revises: a9b8c7d6e5f4
Create Date: 2026-05-11 21:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision: str = 'b1a2c3d4e5f7'
down_revision: Union[str, None] = 'a9b8c7d6e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    accountant_relation_status = ENUM(
        'pending',
        'active',
        'expired',
        'revoked',
        'deleted',
        name='accountantrelationstatus',
        create_type=False,
    )
    accountant_relation_status.create(op.get_bind(), checkfirst=True)

    op.add_column('users', sa.Column('max_accountants', sa.Integer(), server_default='3', nullable=False))

    op.create_table(
        'accountant_relations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('accountant_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('invitation_token', sa.String(), nullable=False),
        sa.Column('global_account_name', sa.String(), nullable=False),
        sa.Column('relation_display_name', sa.String(), nullable=False),
        sa.Column('duty_description', sa.String(length=255), nullable=True),
        sa.Column('mobile_number', sa.String(), nullable=False),
        sa.Column('status', accountant_relation_status, server_default='pending', nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['accountant_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invitation_token'),
    )
    op.create_index(op.f('ix_accountant_relations_accountant_user_id'), 'accountant_relations', ['accountant_user_id'], unique=False)
    op.create_index(op.f('ix_accountant_relations_created_by_user_id'), 'accountant_relations', ['created_by_user_id'], unique=False)
    op.create_index(op.f('ix_accountant_relations_expires_at'), 'accountant_relations', ['expires_at'], unique=False)
    op.create_index(op.f('ix_accountant_relations_global_account_name'), 'accountant_relations', ['global_account_name'], unique=False)
    op.create_index(op.f('ix_accountant_relations_mobile_number'), 'accountant_relations', ['mobile_number'], unique=False)
    op.create_index(op.f('ix_accountant_relations_owner_status'), 'accountant_relations', ['owner_user_id', 'status'], unique=False)
    op.create_index(op.f('ix_accountant_relations_status'), 'accountant_relations', ['status'], unique=False)
    op.create_index(
        'ux_accountant_relations_owner_display_active',
        'accountant_relations',
        ['owner_user_id', 'relation_display_name'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ux_accountant_relations_accountant_active',
        'accountant_relations',
        ['accountant_user_id'],
        unique=True,
        postgresql_where=sa.text('accountant_user_id IS NOT NULL AND deleted_at IS NULL'),
    )

    op.add_column('offers', sa.Column('actor_user_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_offers_actor_user_id_users',
        'offers',
        'users',
        ['actor_user_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(op.f('ix_offers_actor_user_id'), 'offers', ['actor_user_id'], unique=False)

    op.add_column('trades', sa.Column('actor_user_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_trades_actor_user_id_users',
        'trades',
        'users',
        ['actor_user_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(op.f('ix_trades_actor_user_id'), 'trades', ['actor_user_id'], unique=False)

    op.add_column('messages', sa.Column('actor_user_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_messages_actor_user_id_users',
        'messages',
        'users',
        ['actor_user_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(op.f('ix_messages_actor_user_id'), 'messages', ['actor_user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_messages_actor_user_id'), table_name='messages')
    op.drop_constraint('fk_messages_actor_user_id_users', 'messages', type_='foreignkey')
    op.drop_column('messages', 'actor_user_id')

    op.drop_index(op.f('ix_trades_actor_user_id'), table_name='trades')
    op.drop_constraint('fk_trades_actor_user_id_users', 'trades', type_='foreignkey')
    op.drop_column('trades', 'actor_user_id')

    op.drop_index(op.f('ix_offers_actor_user_id'), table_name='offers')
    op.drop_constraint('fk_offers_actor_user_id_users', 'offers', type_='foreignkey')
    op.drop_column('offers', 'actor_user_id')

    op.drop_index('ux_accountant_relations_accountant_active', table_name='accountant_relations')
    op.drop_index('ux_accountant_relations_owner_display_active', table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_status'), table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_owner_status'), table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_mobile_number'), table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_global_account_name'), table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_expires_at'), table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_created_by_user_id'), table_name='accountant_relations')
    op.drop_index(op.f('ix_accountant_relations_accountant_user_id'), table_name='accountant_relations')
    op.drop_table('accountant_relations')

    op.drop_column('users', 'max_accountants')

    accountant_relation_status = ENUM(
        'pending',
        'active',
        'expired',
        'revoked',
        'deleted',
        name='accountantrelationstatus',
        create_type=False,
    )
    accountant_relation_status.drop(op.get_bind(), checkfirst=True)