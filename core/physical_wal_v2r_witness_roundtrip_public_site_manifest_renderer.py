"""Pure default-off renderer for one public V2R site-manifest slice.

This is deliberately not a deployment-plan renderer.  It receives only a
default-off site selector and one opaque, already admitted V2R public bundle,
then emits the exact existing public site-manifest wire schema for that site's
fixed 2/2/4 role slice.  It never accepts a raw role, identity, prefix, IAM
claim, provider fact, credential, path, endpoint, installer, service config,
or runtime object.

The emitted bytes are public evidence only.  They contain no activation or
authority field, and the renderer refuses any bundle whose admission result is
not explicitly non-operational.  A rendered manifest cannot authorize a
writer, promotion, traffic, Phase 5, or Full Matrix execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from core import (
    physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as _bundle,
)


__all__ = (
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_RENDERER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_RENDERER_SCHEMA",
    "PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig",
    "PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError",
    "render_physical_wal_v2r_witness_roundtrip_public_site_manifest",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_RENDERER_SCHEMA = (
    "gold-trade-physical-wal-v2r-public-site-manifest-renderer-v1"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_RENDERER_DEFAULT_ENABLED = (
    False
)

_SITES = frozenset({"wa-fi", "wa-ir", "witness"})


class PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError(ValueError):
    """A claims-only V2R public site-manifest rendering request is invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError(code)


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig:
    """Default-off selector for one fixed public V2R site slice only."""

    site: str = ""
    enabled: bool = (
        PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_RENDERER_DEFAULT_ENABLED
    )


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail("V2R_PUBLIC_SITE_MANIFEST_RENDERER_CLOCK_INVALID")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError(
            "V2R_PUBLIC_SITE_MANIFEST_RENDERER_CLOCK_INVALID"
        ) from exc


def _config(
    value: object,
) -> PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig:
    if (
        type(value) is not PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig
        or value.enabled is not True
        or type(value.site) is not str
        or value.site not in _SITES
    ):
        _fail("V2R_PUBLIC_SITE_MANIFEST_RENDERER_CONFIG_INVALID")
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError(
            "V2R_PUBLIC_SITE_MANIFEST_RENDERER_CANONICAL_INVALID"
        ) from exc


def render_physical_wal_v2r_witness_roundtrip_public_site_manifest(
    *,
    config: PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig,
    full_bundle: object,
    now: datetime,
) -> bytes:
    """Render only one canonical public 2/2/4 V2R site-manifest projection.

    The narrow bundle accessor both verifies the opaque same-process admission
    seal and derives the exact role slice itself.  Callers therefore cannot
    inject, reorder, relabel, or cross-pin role projections at this boundary.
    """

    checked_config = _config(config)
    observed = _utc(now)
    try:
        bundle, roles = (
            _bundle.require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle_site_manifest_slice(
                full_bundle=full_bundle,
                site=checked_config.site,
                now=observed,
            )
        )
    except _bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError(
            "V2R_PUBLIC_SITE_MANIFEST_RENDERER_BUNDLE_INVALID"
        ) from exc
    manifest = {
        "schema": _bundle._MANIFEST_SCHEMA,
        "version": 1,
        "site": checked_config.site,
        "release_sha256": bundle.release_sha256,
        "deployment_binding_sha256": bundle.deployment_binding_sha256,
        "delivery_binding_sha256": bundle.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": bundle.v2r_iam_catalog_sha256,
        "full_bundle_sha256": bundle.full_bundle_sha256,
        "roles": list(roles),
    }
    result = _canonical(manifest)
    # Keep this guard local as well as relying on the downstream admission
    # grammar: the renderer must never grow activation, path, service, or
    # authority fields alongside the public site-manifest schema.
    try:
        parsed = json.loads(result.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError(
            "V2R_PUBLIC_SITE_MANIFEST_RENDERER_CANONICAL_INVALID"
        ) from exc
    if (
        type(parsed) is not dict
        or set(parsed) != _bundle._MANIFEST_FIELDS
        or parsed != manifest
    ):
        _fail("V2R_PUBLIC_SITE_MANIFEST_RENDERER_SCHEMA_INVALID")
    return result
