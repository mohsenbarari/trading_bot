"""Canonical commodity defaults shared by runtime code."""

IMAM_COMMODITY_NAME = "امام"
IMAM_COMMODITY_ALIASES = (
    "امامی",
    "سکه امام",
    "سکه امامی",
    "سکه جدید",
    "سکه بانکی",
)


def is_locked_imam_commodity_name(name: str | None) -> bool:
    return (name or "").strip() == IMAM_COMMODITY_NAME