"""Prepare the complete public V2R site-manifest set without publishing it.

The V2R public full bundle already binds the eight fixed host-role claims.  A
future root-owned publisher must never pick and choose a site slice, or join
three slices from different bundles.  This narrow, default-off boundary
therefore prepares the *complete* WA-FI / WA-IR / Witness public manifest set
from one opaque, already-admitted bundle and pins the expected bundle digest.

It is intentionally not a publisher: it does not open Object Storage, read a
credential, resolve a path, create a deployment, or call a network client.
The returned manifests are canonical public bytes only.  They carry no
election, writer, promotion, Phase-5, or Full-Matrix authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from weakref import WeakKeyDictionary

from core import (
    physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as _bundle,
)
from core import (
    physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer as _renderer,
)


__all__ = (
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_SCHEMA",
    "PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig",
    "PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError",
    "PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet",
    "prepare_physical_wal_v2r_witness_roundtrip_public_site_manifest_set",
    "render_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set",
    "require_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_SCHEMA = (
    "gold-trade-physical-wal-v2r-public-site-manifest-set-preparation-v1"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_DEFAULT_ENABLED = (
    False
)

_STATUS = "v2r-public-site-manifest-set-prepared"
_SITES = ("wa-fi", "wa-ir", "witness")
_ROLE_COUNTS = {"wa-fi": 2, "wa-ir": 2, "witness": 4}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ZERO_SHA256 = "0" * 64
_CAPABILITY = object()


class PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError(ValueError):
    """A public V2R site-manifest-set preparation request failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError(code)


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig:
    """Root-gated pin for one complete public V2R bundle projection."""

    expected_full_bundle_sha256: str = ""
    enabled: bool = (
        PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_DEFAULT_ENABLED
    )


@dataclass(frozen=True, eq=False, init=False)
class PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet:
    """Opaque same-process preparation; it is not a publication capability."""

    schema: str
    status: str
    preparation_sha256: str
    full_bundle_sha256: str
    release_sha256: str
    deployment_binding_sha256: str
    delivery_binding_sha256: str
    v2r_iam_catalog_sha256: str
    wa_fi_manifest_sha256: str
    wa_ir_manifest_sha256: str
    witness_manifest_sha256: str
    provider_facts_verified: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_authorized: bool = False
    phase5_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(self, *, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _PreparationFacts:
    config: PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig
    full_bundle: object
    manifests: tuple[bytes, bytes, bytes]
    prepared_values: dict[str, object]


_PREPARED_STATES: WeakKeyDictionary[
    PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet, _PreparationFacts
] = WeakKeyDictionary()


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError(
            code
        ) from exc


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or _SHA256_RE.fullmatch(value) is None
        or value == _ZERO_SHA256
    ):
        _fail(code)
    return value


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_CLOCK_INVALID")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError(
            "V2R_PUBLIC_SITE_MANIFEST_SET_CLOCK_INVALID"
        ) from exc


def _config(
    value: object,
) -> PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig:
    if (
        type(value)
        is not PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig
        or value.enabled is not True
    ):
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_CONFIG_INVALID")
    _sha256(
        value.expected_full_bundle_sha256,
        code="V2R_PUBLIC_SITE_MANIFEST_SET_CONFIG_INVALID",
    )
    return value


def _facts(*, config: object, full_bundle: object, now: datetime) -> _PreparationFacts:
    checked_config = _config(config)
    observed = _utc(now)
    try:
        admitted_bundle, _ = (
            _bundle.require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle_site_manifest_slice(
                full_bundle=full_bundle,
                site="wa-fi",
                now=observed,
            )
        )
    except _bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError as exc:
        raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError(
            "V2R_PUBLIC_SITE_MANIFEST_SET_BUNDLE_INVALID"
        ) from exc
    if admitted_bundle.full_bundle_sha256 != checked_config.expected_full_bundle_sha256:
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_BUNDLE_PIN_MISMATCH")
    if (
        admitted_bundle.is_operational is not False
        or admitted_bundle.authorizes_phase5 is not False
        or admitted_bundle.authorizes_full_matrix is not False
    ):
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_BUNDLE_INVALID")

    manifests: list[bytes] = []
    digests: dict[str, str] = {}
    for site in _SITES:
        try:
            manifest = _renderer.render_physical_wal_v2r_witness_roundtrip_public_site_manifest(
                config=_renderer.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig(
                    site=site,
                    enabled=True,
                ),
                full_bundle=admitted_bundle,
                now=observed,
            )
            item = json.loads(manifest.decode("ascii"))
        except (
            _renderer.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationError(
                "V2R_PUBLIC_SITE_MANIFEST_SET_RENDER_INVALID"
            ) from exc
        if (
            type(item) is not dict
            or set(item) != _bundle._MANIFEST_FIELDS
            or _canonical(item, code="V2R_PUBLIC_SITE_MANIFEST_SET_RENDER_INVALID")
            != manifest
            or item["site"] != site
            or item["full_bundle_sha256"] != admitted_bundle.full_bundle_sha256
            or type(item["roles"]) is not list
            or len(item["roles"]) != _ROLE_COUNTS[site]
        ):
            _fail("V2R_PUBLIC_SITE_MANIFEST_SET_RENDER_INVALID")
        manifests.append(manifest)
        digests[site] = hashlib.sha256(manifest).hexdigest()
    if len(set(digests.values())) != len(_SITES):
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_RENDER_INVALID")
    prepared_values: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_SCHEMA,
        "status": _STATUS,
        "preparation_sha256": hashlib.sha256(
            _canonical(
                {
                    "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PUBLIC_SITE_MANIFEST_SET_PREPARATION_SCHEMA,
                    "status": _STATUS,
                    "full_bundle_sha256": admitted_bundle.full_bundle_sha256,
                    "wa_fi_manifest_sha256": digests["wa-fi"],
                    "wa_ir_manifest_sha256": digests["wa-ir"],
                    "witness_manifest_sha256": digests["witness"],
                },
                code="V2R_PUBLIC_SITE_MANIFEST_SET_RENDER_INVALID",
            )
        ).hexdigest(),
        "full_bundle_sha256": admitted_bundle.full_bundle_sha256,
        "release_sha256": admitted_bundle.release_sha256,
        "deployment_binding_sha256": admitted_bundle.deployment_binding_sha256,
        "delivery_binding_sha256": admitted_bundle.delivery_binding_sha256,
        "v2r_iam_catalog_sha256": admitted_bundle.v2r_iam_catalog_sha256,
        "wa_fi_manifest_sha256": digests["wa-fi"],
        "wa_ir_manifest_sha256": digests["wa-ir"],
        "witness_manifest_sha256": digests["witness"],
        "provider_facts_verified": False,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
        "phase5_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return _PreparationFacts(
        config=checked_config,
        full_bundle=admitted_bundle,
        manifests=(manifests[0], manifests[1], manifests[2]),
        prepared_values=prepared_values,
    )


def prepare_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
    *,
    config: PhysicalWalV2rWitnessRoundtripPublicSiteManifestSetPreparationConfig,
    full_bundle: object,
    now: datetime,
) -> PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet:
    """Prepare all 2/2/4 public V2R site manifests from one admitted bundle."""

    facts = _facts(config=config, full_bundle=full_bundle, now=now)
    result = PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet(
        capability=_CAPABILITY,
        **facts.prepared_values,
    )
    _PREPARED_STATES[result] = facts
    return result


def require_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
    *,
    prepared: object,
    now: datetime,
) -> PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet:
    """Revalidate a same-process preparation before exposing public bytes."""

    if (
        type(prepared) is not PreparedPhysicalWalV2rWitnessRoundtripPublicSiteManifestSet
        or prepared._capability is not _CAPABILITY
    ):
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_CAPABILITY_INVALID")
    facts = _PREPARED_STATES.get(prepared)
    if facts is None:
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_CAPABILITY_INVALID")
    refreshed = _facts(config=facts.config, full_bundle=facts.full_bundle, now=now)
    if (
        refreshed.manifests != facts.manifests
        or refreshed.prepared_values != facts.prepared_values
        or any(getattr(prepared, name) != value for name, value in facts.prepared_values.items())
    ):
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_TAMPERED")
    return prepared


def render_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
    *,
    prepared: object,
    now: datetime,
) -> tuple[bytes, bytes, bytes]:
    """Return canonical public WA-FI, WA-IR, Witness bytes; never publish them."""

    checked = require_prepared_physical_wal_v2r_witness_roundtrip_public_site_manifest_set(
        prepared=prepared,
        now=now,
    )
    facts = _PREPARED_STATES.get(checked)
    if facts is None:
        _fail("V2R_PUBLIC_SITE_MANIFEST_SET_PREPARED_CAPABILITY_INVALID")
    return facts.manifests
