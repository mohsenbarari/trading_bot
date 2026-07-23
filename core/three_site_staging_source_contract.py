"""Closed project-name contract for pre-migration legacy staging sources."""

from __future__ import annotations

from collections.abc import Iterable


LEGACY_STAGING_PROJECTS_BY_SOURCE_ROLE = {
    "bot_fi": frozenset({"trading_bot_staging"}),
    "webapp_fi": frozenset(
        {
            "trading_bot_staging",
            "trading_bot_staging_iran",
        }
    ),
}
LEGACY_STAGING_PROJECTS = frozenset(
    project
    for projects in LEGACY_STAGING_PROJECTS_BY_SOURCE_ROLE.values()
    for project in projects
)


def legacy_staging_project_allowed(
    project_name: str,
    source_roles: Iterable[str],
) -> bool:
    """Return whether one exact project is approved for every selected role."""
    roles = tuple(source_roles)
    return bool(roles) and all(
        project_name
        in LEGACY_STAGING_PROJECTS_BY_SOURCE_ROLE.get(role, frozenset())
        for role in roles
    )
