"""FastAPI dependencies for shared admin-write authority."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, status

from core.admin_authority import check_shared_admin_write_authority


def require_shared_admin_write_authority(
    table_name: str,
    *,
    operation: str = "write",
    surface: str = "webapp_admin",
) -> Callable[[], None]:
    def dependency() -> None:
        decision = check_shared_admin_write_authority(
            table_name,
            operation=operation,
            surface=surface,
        )
        if not decision.ok:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=decision.as_error_detail(),
            )

    return dependency
