"""Tombstone for the retired WA-IR Object-Storage *receipt* route.

The old ``private-versioned-object-storage-pull-agent`` path used to make a
WA-IR preflight receipt look like a generic controller delivery result.  It
is deliberately not a fallback for the active three-site architecture:

* the controller obtains WA-IR preflight evidence only from Witness;
* FI-to-WA-IR request provisioning has its own signed, encrypted,
  version-pinned receiver boundary; and
* no direct or bypass receipt route is permitted.

This module intentionally has no client, credential, age, filesystem,
subprocess, network, controller, manifest, target, request, or delivery
interface.  It exists solely so an audit can identify the former route and
obtain one redacted, non-authorizing blocked record.  Importing it performs no
I/O, and it cannot be used as a controller delivery implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, NoReturn


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_IR_OBJECT_STORAGE_PULL_DELIVERY_DEFAULT_ENABLED",
    "RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ERROR",
    "RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE",
    "RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE",
    "RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_SCHEMA",
    "RetiredIrObjectStoragePullDeliveryError",
    "RetiredIrObjectStoragePullDeliveryResult",
    "reject_retired_ir_object_storage_pull_delivery",
    "retired_ir_object_storage_pull_delivery_blocked_result",
)


# This is a permanent negative gate, not an operator-controlled feature flag.
DEDICATED_HOST_PREFLIGHT_IR_OBJECT_STORAGE_PULL_DELIVERY_DEFAULT_ENABLED: Final = False
RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_SCHEMA: Final = (
    "three-site-dedicated-host-preflight-retired-object-storage-receipt-route-v1"
)
RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE: Final = (
    "private-versioned-object-storage-pull-agent"
)
RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE: Final = (
    "object-storage-pull-readonly-receipt"
)
RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ERROR: Final = (
    "IR_OBJECT_STORAGE_PULL_RETIRED_NO_DIRECT_OR_BYPASS_ROUTE"
)


class RetiredIrObjectStoragePullDeliveryError(ValueError):
    """The former receipt route is permanently fenced from active preflight."""

    def __init__(self) -> None:
        super().__init__(RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ERROR)
        self.code = RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ERROR


@dataclass(frozen=True)
class RetiredIrObjectStoragePullDeliveryResult:
    """Fixed, redacted status for audit/reporting; it conveys no authority."""

    schema: str = RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_SCHEMA
    status: str = "blocked"
    error: str = RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ERROR
    reason: str = "no-direct-or-bypass-route"
    retired_delivery_route: str = RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE
    retired_delivery_phase: str = RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE

    def as_mapping(self) -> Mapping[str, str]:
        return {
            "schema": self.schema,
            "status": self.status,
            "error": self.error,
            "reason": self.reason,
            "retired_delivery_route": self.retired_delivery_route,
            "retired_delivery_phase": self.retired_delivery_phase,
        }


def retired_ir_object_storage_pull_delivery_blocked_result() -> Mapping[str, str]:
    """Return the only allowed result for the former direct receipt route."""

    return RetiredIrObjectStoragePullDeliveryResult().as_mapping()


def reject_retired_ir_object_storage_pull_delivery() -> NoReturn:
    """Fail closed before any former receipt-pull input can be inspected."""

    raise RetiredIrObjectStoragePullDeliveryError()
