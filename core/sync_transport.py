"""Transport security helpers for cross-server sync HTTP calls."""

from __future__ import annotations

from typing import Any


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def sync_tls_verify_setting_from_values(
    *,
    sync_verify_tls: Any = True,
    sync_ca_bundle: Any = None,
) -> bool | str:
    """Return the httpx ``verify`` value for sync clients.

    A configured CA bundle is the strongest explicit setting and keeps TLS
    verification enabled for private/self-signed internal certificates.
    """
    ca_bundle = _text_or_none(sync_ca_bundle)
    if ca_bundle:
        return ca_bundle
    return _truthy(sync_verify_tls, default=True)


def sync_transport_security_status_from_values(
    *,
    environment: Any = "production",
    sync_verify_tls: Any = True,
    sync_ca_bundle: Any = None,
) -> dict[str, Any]:
    verify_setting = sync_tls_verify_setting_from_values(
        sync_verify_tls=sync_verify_tls,
        sync_ca_bundle=sync_ca_bundle,
    )
    env = str(environment or "production").strip().lower()
    secure = verify_setting is not False
    return {
        "environment": env,
        "secure": secure,
        "verify_setting": "ca_bundle" if isinstance(verify_setting, str) else verify_setting,
        "ca_bundle_configured": isinstance(verify_setting, str),
        "production_allowed": secure or env not in {"production", "prod"},
        "reason": None if secure else "sync_tls_verification_disabled",
    }


def runtime_sync_tls_verify_setting() -> bool | str:
    from core.config import settings

    return sync_tls_verify_setting_from_values(
        sync_verify_tls=getattr(settings, "sync_verify_tls", True),
        sync_ca_bundle=getattr(settings, "sync_ca_bundle", None),
    )


def assert_runtime_sync_transport_allowed() -> None:
    from core.config import settings

    status = sync_transport_security_status_from_values(
        environment=getattr(settings, "environment", "production"),
        sync_verify_tls=getattr(settings, "sync_verify_tls", True),
        sync_ca_bundle=getattr(settings, "sync_ca_bundle", None),
    )
    if not status["production_allowed"]:
        raise RuntimeError("SYNC_VERIFY_TLS=false is not allowed in production without SYNC_CA_BUNDLE")
