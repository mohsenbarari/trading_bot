"""Fail-closed feature dependencies for dual-platform registration."""

from __future__ import annotations

from core.config import settings


def registration_reconciliation_runtime_ready(settings_obj=settings) -> bool:
    return bool(
        getattr(settings_obj, "telegram_registration_reconciliation_enabled", False)
        and getattr(settings_obj, "registration_sync_v2_enabled", False)
    )


def direct_registration_runtime_ready(settings_obj=settings) -> bool:
    return bool(
        getattr(settings_obj, "telegram_direct_registration_enabled", False)
        and registration_reconciliation_runtime_ready(settings_obj)
    )
