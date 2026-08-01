"""Fail closed before superseded direct FI<->IR transport can be used.

The physical three-site topology has exactly two data routes: a normal
``WA-FI -> private versioned Object Storage -> WA-IR`` pull route and its
promoted reverse.  Historical production helpers that SSH, SCP, rsync, or
directly drive the peer's Docker/database/sync state are not a compatible
control plane.  This module is deliberately tiny so a caller can refuse that
legacy authority before it reads a manifest, resolves a host, or creates a
command argv.

It does not govern host-local operations, read-only pinned-host evidence
adapters, or Object-Storage pull controllers with independently constrained
inputs.
"""

from __future__ import annotations

from typing import Final, NoReturn


DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON: Final = (
    "legacy direct FI-to-IR transport is retired; private versioned Object "
    "Storage pull and witnessed single-writer control are required"
)


class LegacyDirectFiIrTransportRetiredError(RuntimeError):
    """Raised before a legacy peer data/control route can be constructed."""


def assert_legacy_direct_fi_ir_transport_retired(
    *, component: str, operation: str
) -> NoReturn:
    """Unconditionally deny an obsolete direct FI<->IR operation."""

    raise LegacyDirectFiIrTransportRetiredError(
        f"{DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON}: {component} ({operation})"
    )


def blocked_legacy_direct_fi_ir_transport_payload(*, component: str) -> dict[str, str]:
    """Return the stable redacted CLI result for a denied legacy route."""

    return {
        "status": "blocked_legacy_direct_fi_ir_transport_retired",
        "component": component,
        "error": DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON,
        "error_class": LegacyDirectFiIrTransportRetiredError.__name__,
    }
