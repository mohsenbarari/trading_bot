"""Fail-closed routing policy for three-site file and data transfers.

Control commands are intentionally outside this module.  This policy covers
payload bytes: releases, role material, images, snapshots, seeds, backups,
evidence, and immutable replication batches.
"""

from __future__ import annotations

from dataclasses import dataclass


FINLAND_ROLES = frozenset({"bot_fi", "webapp_fi"})
IRAN_ROLES = frozenset({"webapp_ir", "witness"})
ROLES = FINLAND_ROLES | IRAN_ROLES

DIRECT_FINLAND_TRANSPORT = "direct-rsync-over-ssh-finland"
IRAN_OBJECT_STORAGE_TRANSPORT = "private-versioned-arvan-object-storage"


class ThreeSiteTransportPolicyError(RuntimeError):
    """Raised when a payload transport crosses an unapproved data plane."""


@dataclass(frozen=True)
class PayloadTransportDecision:
    source_role: str
    destination_role: str
    required_transport: str
    object_storage_allowed: bool
    crosses_iran_finland_boundary: bool


def payload_transport_decision(
    source_role: str, destination_role: str
) -> PayloadTransportDecision:
    source = str(source_role).replace("-", "_")
    destination = str(destination_role).replace("-", "_")
    if source not in ROLES or destination not in ROLES or source == destination:
        raise ThreeSiteTransportPolicyError(
            "payload transfer roles are invalid or identical"
        )
    both_finland = source in FINLAND_ROLES and destination in FINLAND_ROLES
    crosses_boundary = (
        source in FINLAND_ROLES and destination in IRAN_ROLES
    ) or (
        source in IRAN_ROLES and destination in FINLAND_ROLES
    )
    if both_finland:
        return PayloadTransportDecision(
            source_role=source,
            destination_role=destination,
            required_transport=DIRECT_FINLAND_TRANSPORT,
            object_storage_allowed=False,
            crosses_iran_finland_boundary=False,
        )
    return PayloadTransportDecision(
        source_role=source,
        destination_role=destination,
        required_transport=IRAN_OBJECT_STORAGE_TRANSPORT,
        object_storage_allowed=True,
        crosses_iran_finland_boundary=crosses_boundary,
    )


def verify_payload_transport(
    *,
    source_role: str,
    destination_role: str,
    transport: str,
    object_storage_used: bool,
    arvan_endpoint_contacted: bool,
) -> PayloadTransportDecision:
    decision = payload_transport_decision(source_role, destination_role)
    if transport != decision.required_transport:
        raise ThreeSiteTransportPolicyError(
            "payload transport differs from the required regional data plane"
        )
    if decision.object_storage_allowed:
        if object_storage_used is not True or arvan_endpoint_contacted is not True:
            raise ThreeSiteTransportPolicyError(
                "Iran-side payload transport lacks Object Storage evidence"
            )
    elif object_storage_used is not False or arvan_endpoint_contacted is not False:
        raise ThreeSiteTransportPolicyError(
            "Finland-local payload must not contact Arvan Object Storage"
        )
    return decision
