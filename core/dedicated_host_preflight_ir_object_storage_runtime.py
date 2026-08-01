"""Tombstone for the retired WA-IR Object-Storage receipt runtime.

This former runtime is not the FI-to-WA-IR request-provisioning receiver and
is never assembled by the preflight controller or its transport runtime.
The active WA-IR receipt path is Witness dual-signed evidence.  Keeping a
small explicit tombstone is safer than leaving a dormant S3/age/controller
bridge that a future caller could accidentally enable.

There are intentionally no configuration paths, credentials, locators,
provisioners, client factories, decryptors, or controller delivery methods in
this module.  It only returns a redacted blocked record for audit tooling.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, NoReturn

from core import dedicated_host_preflight_ir_object_storage_pull_delivery as _retired


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_IR_OBJECT_STORAGE_RUNTIME_DEFAULT_ENABLED",
    "RETIRED_IR_OBJECT_STORAGE_RUNTIME_ERROR",
    "RETIRED_IR_OBJECT_STORAGE_RUNTIME_SCHEMA",
    "RetiredIrObjectStorageRuntimeError",
    "RetiredIrObjectStorageRuntimeResult",
    "reject_retired_ir_object_storage_runtime",
    "retired_ir_object_storage_runtime_blocked_result",
)


DEDICATED_HOST_PREFLIGHT_IR_OBJECT_STORAGE_RUNTIME_DEFAULT_ENABLED: Final = False
RETIRED_IR_OBJECT_STORAGE_RUNTIME_SCHEMA: Final = (
    "three-site-dedicated-host-preflight-retired-object-storage-receipt-runtime-v1"
)
RETIRED_IR_OBJECT_STORAGE_RUNTIME_ERROR: Final = (
    "IR_OBJECT_STORAGE_RUNTIME_RETIRED_NO_DIRECT_OR_BYPASS_ROUTE"
)


class RetiredIrObjectStorageRuntimeError(ValueError):
    """The former concrete receipt runtime is permanently unavailable."""

    def __init__(self) -> None:
        super().__init__(RETIRED_IR_OBJECT_STORAGE_RUNTIME_ERROR)
        self.code = RETIRED_IR_OBJECT_STORAGE_RUNTIME_ERROR


@dataclass(frozen=True)
class RetiredIrObjectStorageRuntimeResult:
    """Fixed, redacted runtime status; never a transport result or approval."""

    schema: str = RETIRED_IR_OBJECT_STORAGE_RUNTIME_SCHEMA
    status: str = "blocked"
    error: str = RETIRED_IR_OBJECT_STORAGE_RUNTIME_ERROR
    reason: str = "no-direct-or-bypass-route"
    retired_delivery_route: str = _retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE
    retired_delivery_phase: str = _retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE

    def as_mapping(self) -> Mapping[str, str]:
        return {
            "schema": self.schema,
            "status": self.status,
            "error": self.error,
            "reason": self.reason,
            "retired_delivery_route": self.retired_delivery_route,
            "retired_delivery_phase": self.retired_delivery_phase,
        }


def retired_ir_object_storage_runtime_blocked_result() -> Mapping[str, str]:
    """Return the only permitted result from the former concrete runtime."""

    return RetiredIrObjectStorageRuntimeResult().as_mapping()


def reject_retired_ir_object_storage_runtime() -> NoReturn:
    """Fail closed without reading a historical config, locator, or secret."""

    raise RetiredIrObjectStorageRuntimeError()
