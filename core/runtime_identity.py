"""Separate durable business authority from physical deployment identity."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import settings
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, normalize_server


AUTHORITY_FOREIGN = "foreign"
AUTHORITY_WEBAPP = "webapp"

SITE_BOT_FI = "bot_fi"
SITE_WEBAPP_FI = "webapp_fi"
SITE_WEBAPP_IR = "webapp_ir"

WEBAPP_SITES = frozenset({SITE_WEBAPP_FI, SITE_WEBAPP_IR})
PHYSICAL_SITES = frozenset({SITE_BOT_FI, *WEBAPP_SITES})
LOGICAL_AUTHORITIES = frozenset({AUTHORITY_FOREIGN, AUTHORITY_WEBAPP})
LEGACY_SERVER_ALIASES = frozenset({"germany", "de", "foreign", "german", "iran", "ir"})


class RuntimeIdentityError(RuntimeError):
    """Raised when deployment identity and logical authority conflict."""


@dataclass(frozen=True)
class RuntimeIdentity:
    logical_authority: str
    physical_site: str
    legacy_server_mode: str
    compatibility_inferred: bool

    @property
    def is_webapp_authority(self) -> bool:
        return self.logical_authority == AUTHORITY_WEBAPP

    @property
    def is_webapp_site(self) -> bool:
        return self.physical_site in WEBAPP_SITES

    @property
    def is_bot_site(self) -> bool:
        return self.physical_site == SITE_BOT_FI


def _clean(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def resolve_runtime_identity(settings_obj=settings) -> RuntimeIdentity:
    raw_server_mode = _clean(getattr(settings_obj, "server_mode", None)) or AUTHORITY_FOREIGN
    if raw_server_mode not in LEGACY_SERVER_ALIASES:
        raise RuntimeIdentityError(f"unsupported server_mode={raw_server_mode!r}")
    server_mode = normalize_server(raw_server_mode)
    configured_authority = _clean(getattr(settings_obj, "logical_authority", None))
    configured_site = _clean(getattr(settings_obj, "physical_site", None))

    inferred_authority = AUTHORITY_FOREIGN if server_mode == SERVER_FOREIGN else AUTHORITY_WEBAPP
    inferred_site = SITE_BOT_FI if server_mode == SERVER_FOREIGN else SITE_WEBAPP_FI
    logical_authority = configured_authority or inferred_authority
    physical_site = configured_site or inferred_site

    if logical_authority not in LOGICAL_AUTHORITIES:
        raise RuntimeIdentityError(f"unsupported logical_authority={logical_authority!r}")
    if physical_site not in PHYSICAL_SITES:
        raise RuntimeIdentityError(f"unsupported physical_site={physical_site!r}")
    if server_mode == SERVER_FOREIGN and logical_authority != AUTHORITY_FOREIGN:
        raise RuntimeIdentityError("legacy server_mode=foreign must retain foreign logical authority")
    if server_mode == SERVER_IRAN and logical_authority != AUTHORITY_WEBAPP:
        raise RuntimeIdentityError("legacy server_mode=iran must retain WebApp logical authority")
    if physical_site == SITE_BOT_FI and logical_authority != AUTHORITY_FOREIGN:
        raise RuntimeIdentityError("bot_fi can host only foreign logical authority")
    if physical_site in WEBAPP_SITES and logical_authority != AUTHORITY_WEBAPP:
        raise RuntimeIdentityError("webapp_fi/webapp_ir can host only WebApp logical authority")

    return RuntimeIdentity(
        logical_authority=logical_authority,
        physical_site=physical_site,
        legacy_server_mode=server_mode,
        compatibility_inferred=configured_authority is None and configured_site is None,
    )
