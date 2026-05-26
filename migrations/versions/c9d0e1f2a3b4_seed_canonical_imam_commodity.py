"""seed canonical imam commodity

Revision ID: c9d0e1f2a3b4
Revises: e1f2a3b4c5d6
Create Date: 2026-05-26 11:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


IMAM_COMMODITY_NAME = "امام"
IMAM_COMMODITY_ALIASES = (
    "امامی",
    "سکه امام",
    "سکه امامی",
    "سکه جدید",
    "سکه بانکی",
)


def upgrade() -> None:
    bind = op.get_bind()

    imam_id = bind.execute(
        sa.text("SELECT id FROM commodities WHERE name = :name"),
        {"name": IMAM_COMMODITY_NAME},
    ).scalar()

    if imam_id is None:
        imam_id = bind.execute(
            sa.text("INSERT INTO commodities (name) VALUES (:name) RETURNING id"),
            {"name": IMAM_COMMODITY_NAME},
        ).scalar_one()

    existing_aliases = set(
        bind.execute(sa.text("SELECT alias FROM commodity_aliases")).scalars().all()
    )
    for alias in IMAM_COMMODITY_ALIASES:
        if alias in existing_aliases:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO commodity_aliases (alias, commodity_id) VALUES (:alias, :commodity_id)"
            ),
            {"alias": alias, "commodity_id": imam_id},
        )


def downgrade() -> None:
    bind = op.get_bind()

    imam_id = bind.execute(
        sa.text("SELECT id FROM commodities WHERE name = :name"),
        {"name": IMAM_COMMODITY_NAME},
    ).scalar()
    if imam_id is None:
        return

    for alias in IMAM_COMMODITY_ALIASES:
        bind.execute(
            sa.text(
                "DELETE FROM commodity_aliases WHERE commodity_id = :commodity_id AND alias = :alias"
            ),
            {"commodity_id": imam_id, "alias": alias},
        )

    remaining_alias_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM commodity_aliases WHERE commodity_id = :commodity_id"),
        {"commodity_id": imam_id},
    ).scalar_one()
    offer_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM offers WHERE commodity_id = :commodity_id"),
        {"commodity_id": imam_id},
    ).scalar_one()
    trade_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM trades WHERE commodity_id = :commodity_id"),
        {"commodity_id": imam_id},
    ).scalar_one()

    if remaining_alias_count == 0 and offer_count == 0 and trade_count == 0:
        bind.execute(
            sa.text("DELETE FROM commodities WHERE id = :commodity_id AND name = :name"),
            {"commodity_id": imam_id, "name": IMAM_COMMODITY_NAME},
        )