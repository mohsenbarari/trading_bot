"""add level and category to notifications

Revision ID: add_notif_details
Revises: b390fe788606
Create Date: 2025-11-20 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_notif_details'
down_revision: Union[str, None] = 'b390fe788606' 
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # تعریف آبجکت‌های Enum برای دیتابیس
    # نکته: مقادیر اینجا باید دقیقاً با core/enums.py یکی باشند
    notification_level = sa.Enum('INFO', 'SUCCESS', 'WARNING', 'ERROR', name='notificationlevel')
    notification_category = sa.Enum('SYSTEM', 'USER', 'TRADE', name='notificationcategory')
    
    # ساخت تایپ‌ها در پستگرس (اگر وجود ندارند)
    notification_level.create(op.get_bind(), checkfirst=True)
    notification_category.create(op.get_bind(), checkfirst=True)

    op.add_column('notifications', sa.Column('level', notification_level, nullable=False, server_default='INFO'))
    op.add_column('notifications', sa.Column('category', notification_category, nullable=False, server_default='SYSTEM'))

def downgrade() -> None:
    op.drop_column('notifications', 'category')
    op.drop_column('notifications', 'level')
    
    # حذف تایپ‌ها در صورت بازگشت به عقب
    sa.Enum(name='notificationlevel').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='notificationcategory').drop(op.get_bind(), checkfirst=True)