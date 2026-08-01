"""Canonical non-secret commitments for the reverse Arvan data route.

The reverse preflight intentionally keeps provider endpoint and bucket details
out of its portable public observation.  A pure arbitrary hash is not enough,
however: both local role factories must prove that their independently pinned
endpoint/region/bucket policies describe the same
``IR -> Object Storage -> FI`` route before either opens a credential.

This module supplies the one canonical domain-separated commitment grammar.
It has no credential, network, filesystem, SDK, subprocess, or host action.
The resulting digests are public binding facts, never secrets or authority.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
from core.physical_wal_object_manifest import PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "PHYSICAL_ARVAN_S3_FAILBACK_FOUR_ROLE_ROUTE_BINDING_SCHEMA",
    "PHYSICAL_ARVAN_S3_FAILBACK_ROUTE_SCOPE_SCHEMA",
    "PhysicalArvanS3FailbackRouteCommitmentError",
    "derive_physical_arvan_s3_failback_four_role_route_binding_sha256",
    "derive_physical_arvan_s3_failback_route_scope_sha256",
    "physical_arvan_s3_failback_exact_prefix",
)


PHYSICAL_ARVAN_S3_FAILBACK_ROUTE_SCOPE_SCHEMA = (
    "gold-trade-physical-arvan-s3-failback-route-scope-v1"
)
PHYSICAL_ARVAN_S3_FAILBACK_FOUR_ROLE_ROUTE_BINDING_SCHEMA = (
    "gold-trade-physical-arvan-s3-failback-four-role-route-binding-v1"
)

_ENDPOINT_RE = re.compile(
    r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir$", re.ASCII
)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class PhysicalArvanS3FailbackRouteCommitmentError(ValueError):
    """A fixed, non-sensitive commitment grammar error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalArvanS3FailbackRouteCommitmentError(code)


def _campaign(value: object) -> str:
    if type(value) is not str or CAMPAIGN_ID_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_CAMPAIGN_INVALID")
    return value


def _release(value: object) -> str:
    if type(value) is not str or RELEASE_SHA_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_RELEASE_INVALID")
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _endpoint_region(endpoint: object, region: object) -> tuple[str, str]:
    if type(endpoint) is not str or type(region) is not str:
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_ENDPOINT_INVALID")
    match = _ENDPOINT_RE.fullmatch(endpoint)
    if match is None or match.group(1) != region:
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_ENDPOINT_INVALID")
    return endpoint, region


def _bucket(value: object) -> str:
    if type(value) is not str or _BUCKET_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_BUCKET_INVALID")
    return value


def physical_arvan_s3_failback_exact_prefix(*, campaign_id: str, release_sha: str) -> str:
    """Return the only reverse prefix admissible for one campaign/release."""

    return (
        f"{PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE}/"
        f"{_campaign(campaign_id)}/{_release(release_sha)}/"
    )


def derive_physical_arvan_s3_failback_route_scope_sha256(
    *,
    campaign_id: str,
    release_sha: str,
    endpoint: str,
    region: str,
    bucket: str,
) -> str:
    """Commit exact endpoint/bucket/reverse prefix and the two local roles."""

    campaign = _campaign(campaign_id)
    release = _release(release_sha)
    checked_endpoint, checked_region = _endpoint_region(endpoint, region)
    checked_bucket = _bucket(bucket)
    payload: dict[str, Any] = {
        "schema": PHYSICAL_ARVAN_S3_FAILBACK_ROUTE_SCOPE_SCHEMA,
        "campaign_id": campaign,
        "release_sha": release,
        "source_site": "webapp_ir",
        "destination_site": "webapp_fi",
        "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        "exact_prefix": physical_arvan_s3_failback_exact_prefix(
            campaign_id=campaign,
            release_sha=release,
        ),
        "endpoint": checked_endpoint,
        "region": checked_region,
        "bucket": checked_bucket,
        "publisher_role": "ir-publisher",
        "receiver_role": "fi-receiver",
        "publication_policy": "create-only-versioned-readback-v1",
        "receiver_policy": "exact-version-get-head-only-v1",
        "direct_site_control": "forbidden",
        "destination_object_ingest": "pull-only",
    }
    try:
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - normalized fields above.
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_CANONICAL_INVALID")


def derive_physical_arvan_s3_failback_four_role_route_binding_sha256(
    *,
    campaign_id: str,
    release_sha: str,
    normal_route_scope_sha256: str,
    reverse_route_scope_sha256: str,
    fi_publisher_identity_sha256: str,
    ir_receiver_identity_sha256: str,
    ir_publisher_identity_sha256: str,
    fi_receiver_identity_sha256: str,
) -> str:
    """Commit all public role identities and both direction scope digests.

    The normal scope is intentionally supplied as an independently produced
    commitment: a reverse host must not open or inspect normal-direction
    credentials just to recompute it.  The Witness live-IAM receipt later
    proves its provenance; this helper still makes all public fields
    tamper-evident relative to the reverse factory's exact local scope.
    """

    identities = (
        _sha256(fi_publisher_identity_sha256, code="ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_IDENTITY_INVALID"),
        _sha256(ir_receiver_identity_sha256, code="ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_IDENTITY_INVALID"),
        _sha256(ir_publisher_identity_sha256, code="ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_IDENTITY_INVALID"),
        _sha256(fi_receiver_identity_sha256, code="ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_IDENTITY_INVALID"),
    )
    if len(set(identities)) != len(identities):
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_IDENTITIES_NOT_SEPARATE")
    payload: dict[str, Any] = {
        "schema": PHYSICAL_ARVAN_S3_FAILBACK_FOUR_ROLE_ROUTE_BINDING_SCHEMA,
        "campaign_id": _campaign(campaign_id),
        "release_sha": _release(release_sha),
        "normal_route_scope_sha256": _sha256(
            normal_route_scope_sha256,
            code="ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_SCOPE_INVALID",
        ),
        "reverse_route_scope_sha256": _sha256(
            reverse_route_scope_sha256,
            code="ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_SCOPE_INVALID",
        ),
        "fi_publisher_identity_sha256": identities[0],
        "ir_receiver_identity_sha256": identities[1],
        "ir_publisher_identity_sha256": identities[2],
        "fi_receiver_identity_sha256": identities[3],
        "source_site": "webapp_ir",
        "destination_site": "webapp_fi",
        "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        "direct_site_control": "forbidden",
        "destination_object_ingest": "pull-only",
    }
    try:
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - normalized fields above.
        _fail("ARVAN_S3_FAILBACK_ROUTE_COMMITMENT_CANONICAL_INVALID")
