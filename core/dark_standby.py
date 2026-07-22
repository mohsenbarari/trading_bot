"""Hard process boundary for the data-ready WebApp-IR dark standby."""

from __future__ import annotations

import os
from collections.abc import Mapping


class DarkStandbyRuntimeError(RuntimeError):
    """Raised when an application-capable process is started in dark mode."""


FORBIDDEN_DARK_SERVICES = frozenset(
    {
        "api",
        "app",
        "bot",
        "migration",
        "schema_init",
        "sync_worker",
        "redis_restore",
        "nginx",
        "background_worker",
        "connectivity_controller",
        "dr_blob_worker",
        "dr_delivery_worker",
        "dr_effect_worker",
        "dr_projection_worker",
        "recovery_manifest",
    }
)
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def dark_standby_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return str(values.get("DARK_STANDBY_MODE", "")).strip().lower() in TRUE_VALUES


def assert_not_dark_standby(
    service: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    normalized = str(service or "").strip().lower()
    if normalized not in FORBIDDEN_DARK_SERVICES:
        raise DarkStandbyRuntimeError(f"unknown application-capable service: {service!r}")
    if dark_standby_enabled(environ):
        raise DarkStandbyRuntimeError(
            f"DARK_STANDBY_MODE forbids starting {normalized}; use the standalone DB-only manifest"
        )
