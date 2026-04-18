from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "352da8093009"
down_revision: Union[str, None] = "e2b3c4d5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'voice'")

def downgrade() -> None:
    pass
