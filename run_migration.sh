#!/bin/bash
set -euo pipefail

# This one-off root-Compose migration writes a historical schema patch into a
# running app container. It has no release identity, writer term, or
# Object-Storage provenance, so it must never be used on a three-site host.
echo "ERROR: run_migration.sh is retired for the three-site architecture; schema changes must run through the reviewed Writer-Witness release controller. No local or remote bypass is available." >&2
exit 2

# Historical forensic source below; intentionally unreachable.
docker compose exec -T app bash -c 'cat << "INNER_EOF" > migrations/versions/352da8093009_add_voice_to_messagetype.py
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "352da8093009"
down_revision: Union[str, None] = "e2b3c4d5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS '"'"'voice'"'"'")

def downgrade() -> None:
    pass
INNER_EOF
alembic upgrade head'
