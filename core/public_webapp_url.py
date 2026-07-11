"""Validation for the user-facing Iran WebApp origin."""

from __future__ import annotations

from urllib.parse import urlparse

from core.config import settings
from core.deployment_surface import csv_hosts, extract_host
from core.server_routing import SERVER_IRAN, normalize_server


class PublicWebAppURLConfigurationError(ValueError):
    pass


def _normalize_host(value: str | None) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _environment_allows_http(settings_obj) -> bool:
    environment = str(getattr(settings_obj, "environment", "") or "").strip().lower()
    return environment in {"dev", "development", "local", "test", "testing"}


def _explicit_iran_hosts(settings_obj) -> set[str]:
    hosts = {
        _normalize_host(host)
        for host in csv_hosts(getattr(settings_obj, "iran_server_aliases", None))
    }
    hosts.add(_normalize_host(extract_host(getattr(settings_obj, "iran_server_domain", None))))
    hosts.add(_normalize_host(extract_host(getattr(settings_obj, "iran_server_url", None))))
    hosts.discard("")
    return hosts


def _foreign_hosts(settings_obj) -> set[str]:
    hosts = {
        _normalize_host(host)
        for host in csv_hosts(getattr(settings_obj, "foreign_server_aliases", None))
    }
    for field_name in (
        "foreign_server_domain",
        "foreign_server_url",
        "germany_server_url",
    ):
        hosts.add(_normalize_host(extract_host(getattr(settings_obj, field_name, None))))
    if normalize_server(getattr(settings_obj, "server_mode", None), default="") == SERVER_IRAN:
        hosts.add(
            _normalize_host(extract_host(getattr(settings_obj, "peer_server_url", None)))
        )
    hosts.discard("")
    return hosts


def validate_public_webapp_url(raw_value: str | None, *, settings_obj=settings) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL is required")

    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL is invalid") from exc
    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL must be an absolute URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL must contain only an origin")
    if parsed.path not in {"", "/"}:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL must not contain a path")

    scheme = parsed.scheme.lower()
    if scheme != "https" and not (_environment_allows_http(settings_obj) and scheme == "http"):
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL must use HTTPS")

    host = _normalize_host(parsed.hostname)
    if host in _foreign_hosts(settings_obj):
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL must not target a foreign server")

    iran_hosts = _explicit_iran_hosts(settings_obj)
    if not iran_hosts:
        raise PublicWebAppURLConfigurationError("No Iran host is configured for PUBLIC_WEBAPP_URL")
    if host not in iran_hosts:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL must target a configured Iran host")

    default_port = 443 if scheme == "https" else 80
    try:
        port = parsed.port
    except ValueError as exc:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL has an invalid port") from exc
    if port is not None and port <= 0:
        raise PublicWebAppURLConfigurationError("PUBLIC_WEBAPP_URL has an invalid port")
    port_suffix = f":{port}" if port and port != default_port else ""
    return f"{scheme}://{host}{port_suffix}"


def public_webapp_url_for_links(*, settings_obj=settings) -> str:
    return validate_public_webapp_url(
        getattr(settings_obj, "public_webapp_url", None),
        settings_obj=settings_obj,
    )
