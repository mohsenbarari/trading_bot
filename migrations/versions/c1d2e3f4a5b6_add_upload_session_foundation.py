"""add upload session foundation

Revision ID: c1d2e3f4a5b6
Revises: b1a2c3d4e5f7
Create Date: 2026-05-14 11:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b1a2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    upload_room_kind = ENUM('direct', 'group', name='uploadroomkind', create_type=False)
    upload_batch_message_kind = ENUM('single', 'album', name='uploadbatchmessagekind', create_type=False)
    upload_batch_status = ENUM(
        'collecting',
        'uploading',
        'uploaded',
        'committing',
        'committed',
        'failed',
        'cancelled',
        'expired',
        name='uploadbatchstatus',
        create_type=False,
    )
    upload_caption_policy = ENUM('none', 'first_item_only', name='uploadcaptionpolicy', create_type=False)
    upload_media_type = ENUM('image', 'video', 'voice', 'document', name='uploadmediatype', create_type=False)
    upload_session_status = ENUM(
        'created',
        'uploading',
        'uploaded',
        'finalizing',
        'ready',
        'committed',
        'failed',
        'cancelled',
        'expired',
        name='uploadsessionstatus',
        create_type=False,
    )

    for enum_type in (
        upload_room_kind,
        upload_batch_message_kind,
        upload_batch_status,
        upload_caption_policy,
        upload_media_type,
        upload_session_status,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'upload_batches',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('room_kind', upload_room_kind, nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('message_kind', upload_batch_message_kind, server_default='single', nullable=False),
        sa.Column('expected_items', sa.Integer(), server_default='1', nullable=False),
        sa.Column('committed_items', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', upload_batch_status, server_default='collecting', nullable=False),
        sa.Column('caption_policy', upload_caption_policy, server_default='none', nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name='fk_upload_batches_owner_user', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name='fk_upload_batches_actor_user', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_upload_batches_id'), 'upload_batches', ['id'], unique=False)
    op.create_index(op.f('ix_upload_batches_owner_user_id'), 'upload_batches', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_upload_batches_actor_user_id'), 'upload_batches', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_upload_batches_room_kind'), 'upload_batches', ['room_kind'], unique=False)
    op.create_index(op.f('ix_upload_batches_target_id'), 'upload_batches', ['target_id'], unique=False)
    op.create_index(op.f('ix_upload_batches_status'), 'upload_batches', ['status'], unique=False)
    op.create_index(op.f('ix_upload_batches_idempotency_key'), 'upload_batches', ['idempotency_key'], unique=False)
    op.create_index(op.f('ix_upload_batches_expires_at'), 'upload_batches', ['expires_at'], unique=False)
    op.create_index(op.f('ix_upload_batches_last_activity_at'), 'upload_batches', ['last_activity_at'], unique=False)
    op.create_index('ix_upload_batches_owner_status_activity', 'upload_batches', ['owner_user_id', 'status', 'last_activity_at'], unique=False)
    op.create_index('ix_upload_batches_target_room_status', 'upload_batches', ['room_kind', 'target_id', 'status'], unique=False)

    op.create_table(
        'upload_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('batch_id', sa.String(length=36), nullable=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('room_kind', upload_room_kind, nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('media_type', upload_media_type, nullable=False),
        sa.Column('original_file_name', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('total_bytes', sa.Integer(), nullable=False),
        sa.Column('chunk_size', sa.Integer(), nullable=False),
        sa.Column('received_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_offset', sa.Integer(), server_default='0', nullable=False),
        sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('sha256_full', sa.String(length=128), nullable=True),
        sa.Column('sha256_chunks', sa.JSON(), nullable=True),
        sa.Column('status', upload_session_status, server_default='created', nullable=False),
        sa.Column('temp_storage_path', sa.String(length=512), nullable=False),
        sa.Column('final_chat_file_id', sa.String(length=36), nullable=True),
        sa.Column('preview_metadata', sa.JSON(), nullable=True),
        sa.Column('resume_token', sa.String(length=128), nullable=False),
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['upload_batches.id'], name='fk_upload_sessions_batch', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name='fk_upload_sessions_owner_user', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name='fk_upload_sessions_actor_user', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['final_chat_file_id'], ['chat_files.id'], name='fk_upload_sessions_chat_file', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resume_token'),
    )
    op.create_index(op.f('ix_upload_sessions_id'), 'upload_sessions', ['id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_batch_id'), 'upload_sessions', ['batch_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_owner_user_id'), 'upload_sessions', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_actor_user_id'), 'upload_sessions', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_room_kind'), 'upload_sessions', ['room_kind'], unique=False)
    op.create_index(op.f('ix_upload_sessions_target_id'), 'upload_sessions', ['target_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_media_type'), 'upload_sessions', ['media_type'], unique=False)
    op.create_index(op.f('ix_upload_sessions_status'), 'upload_sessions', ['status'], unique=False)
    op.create_index(op.f('ix_upload_sessions_final_chat_file_id'), 'upload_sessions', ['final_chat_file_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_resume_token'), 'upload_sessions', ['resume_token'], unique=True)
    op.create_index(op.f('ix_upload_sessions_expires_at'), 'upload_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_upload_sessions_last_activity_at'), 'upload_sessions', ['last_activity_at'], unique=False)
    op.create_index('ix_upload_sessions_owner_status_activity', 'upload_sessions', ['owner_user_id', 'status', 'last_activity_at'], unique=False)
    op.create_index('ix_upload_sessions_batch_status', 'upload_sessions', ['batch_id', 'status'], unique=False)
    op.create_index('ix_upload_sessions_target_room_status', 'upload_sessions', ['room_kind', 'target_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_upload_sessions_target_room_status', table_name='upload_sessions')
    op.drop_index('ix_upload_sessions_batch_status', table_name='upload_sessions')
    op.drop_index('ix_upload_sessions_owner_status_activity', table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_last_activity_at'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_expires_at'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_resume_token'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_final_chat_file_id'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_status'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_media_type'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_target_id'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_room_kind'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_actor_user_id'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_owner_user_id'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_batch_id'), table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_id'), table_name='upload_sessions')
    op.drop_table('upload_sessions')

    op.drop_index('ix_upload_batches_target_room_status', table_name='upload_batches')
    op.drop_index('ix_upload_batches_owner_status_activity', table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_last_activity_at'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_expires_at'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_idempotency_key'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_status'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_target_id'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_room_kind'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_actor_user_id'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_owner_user_id'), table_name='upload_batches')
    op.drop_index(op.f('ix_upload_batches_id'), table_name='upload_batches')
    op.drop_table('upload_batches')

    upload_session_status = ENUM(
        'created', 'uploading', 'uploaded', 'finalizing', 'ready', 'committed', 'failed', 'cancelled', 'expired',
        name='uploadsessionstatus', create_type=False,
    )
    upload_media_type = ENUM('image', 'video', 'voice', 'document', name='uploadmediatype', create_type=False)
    upload_caption_policy = ENUM('none', 'first_item_only', name='uploadcaptionpolicy', create_type=False)
    upload_batch_status = ENUM(
        'collecting', 'uploading', 'uploaded', 'committing', 'committed', 'failed', 'cancelled', 'expired',
        name='uploadbatchstatus', create_type=False,
    )
    upload_batch_message_kind = ENUM('single', 'album', name='uploadbatchmessagekind', create_type=False)
    upload_room_kind = ENUM('direct', 'group', name='uploadroomkind', create_type=False)

    for enum_type in (
        upload_session_status,
        upload_media_type,
        upload_caption_policy,
        upload_batch_status,
        upload_batch_message_kind,
        upload_room_kind,
    ):
        enum_type.drop(op.get_bind(), checkfirst=True)