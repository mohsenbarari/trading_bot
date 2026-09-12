"""Characterization tests for retired register complete endpoint.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap Reference: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed Inventory: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-AUTH-RETIRED-REGISTER-COMPLETE-L1580

This unit provides bounded assertion evidence for inventory seed
API-AUTH-POST-AUTH-RETIRED-REGISTER-COMPLETE-L1580 only. This unit does not
close the gap, seed inventory, or Stage.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install fail-closed network fence prior to importing auth router
install_p1_00_network_fence()

import asyncio

import pytest
from fastapi import HTTPException, status

from api.routers.auth import (
    LEGACY_RAW_REGISTRATION_RETIRED_DETAIL,
    RegisterComplete,
    retired_register_complete,
)


def test_retired_register_complete_direct_contract() -> None:
    """Freeze direct 410 Gone contract and assert response headers and retirement detail."""
    req = RegisterComplete(
        token="opaque-characterization-invitation-token-20260912-abc123xyz",
        address="Tehran, Valid Registration Address 123",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(retired_register_complete(req))

    exc = exc_info.value
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_410_GONE
    assert exc.status_code == 410
    assert exc.detail == LEGACY_RAW_REGISTRATION_RETIRED_DETAIL
    assert exc.detail == "این مسیر ثبت‌نام بازنشسته شده است؛ صفحه را دوباره بارگذاری کنید"
    assert exc.headers == {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    assert "Set-Cookie" not in (exc.headers or {})
    assert len(exc.headers) == 2
