"""Fail closed before retired direct transport between WA-FI and WA-IR.

The three-site topology has two approved cross-site data directions, both
using private, versioned Object Storage pull: ``WA-FI -> Object Storage ->
WA-IR`` in the normal state and the promoted reverse direction after a
witnessed failover.  Direct HTTP, SSH, SCP, rsync, database, or sync-worker
transport between WA-FI and WA-IR is therefore retired in *both* directions.

This intentionally small module is imported at former direct-route factories
so they fail before resolving a peer, reading a peer credential, assembling a
payload, or constructing a network client.  It does not govern host-local
operations, a pinned read-only evidence adapter, or an Object-Storage pull
whose input and authority are independently constrained.
"""

from __future__ import annotations

from typing import Final, NoReturn


DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON: Final = (
    "legacy direct FI-to-IR and IR-to-FI transport is retired; private, "
    "versioned Object Storage pull and Witness-mediated single-writer control "
    "are required"
)


class LegacyDirectFiIrTransportRetiredError(RuntimeError):
    """Raised before a retired direct peer data/control route is constructed."""


def assert_legacy_direct_fi_ir_transport_retired(
    *, component: str, operation: str
) -> NoReturn:
    """Unconditionally deny a legacy direct WA-FI <-> WA-IR operation."""

    raise LegacyDirectFiIrTransportRetiredError(
        f"{DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON}: {component} ({operation})"
    )


def blocked_legacy_direct_fi_ir_transport_payload(*, component: str) -> dict[str, str]:
    """Return the stable, redacted CLI/API result for a denied legacy route."""

    return {
        "status": "blocked_legacy_direct_fi_ir_transport_retired",
        "component": component,
        "error": DIRECT_FI_IR_TRANSPORT_RETIREMENT_REASON,
        "error_class": LegacyDirectFiIrTransportRetiredError.__name__,
    }
