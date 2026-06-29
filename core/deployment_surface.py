from __future__ import annotations

from urllib.parse import urlparse


LOCAL_DEVELOPMENT_ORIGINS = {
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
}


def normalize_origin(raw_value: str | None, *, default_scheme: str = "https") -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None

    candidate = value if "://" in value else f"{default_scheme}://{value}"
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def extract_host(raw_value: str | None) -> str:
    value = (raw_value or "").strip()
    if not value:
        return ""

    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    host = parsed.netloc or parsed.path or value
    return host.split(",")[0].split(":")[0].strip().lower()


def csv_hosts(raw_value: str | None) -> set[str]:
    return {
        extract_host(item)
        for item in (raw_value or "").split(",")
        if extract_host(item)
    }


def iran_server_aliases(settings) -> set[str]:
    aliases = set()
    aliases.update(csv_hosts(getattr(settings, "iran_server_aliases", None)))
    aliases.add(extract_host(getattr(settings, "iran_server_domain", None)))
    aliases.add(extract_host(getattr(settings, "iran_server_url", None)))
    aliases.add(extract_host(getattr(settings, "frontend_url", None)))
    aliases.discard("")
    return aliases


def foreign_server_aliases(settings) -> set[str]:
    aliases = set()
    aliases.update(csv_hosts(getattr(settings, "foreign_server_aliases", None)))
    aliases.add(extract_host(getattr(settings, "foreign_server_domain", None)))
    aliases.add(extract_host(getattr(settings, "foreign_server_url", None)))
    aliases.add(extract_host(getattr(settings, "germany_server_url", None)))
    aliases.discard("")
    return aliases


def extra_cors_origins(settings) -> set[str]:
    return {
        origin
        for origin in (
            normalize_origin(item, default_scheme="http")
            for item in (getattr(settings, "extra_cors_origins", None) or "").split(",")
        )
        if origin
    }


def _allow_local_development_origins(settings) -> bool:
    environment = (getattr(settings, "environment", None) or "").strip().lower()
    return environment in {"", "dev", "development", "local", "test", "testing", "staging"}


def allowed_cors_origins(settings) -> list[str]:
    allowed = set()
    if _allow_local_development_origins(settings):
        allowed.update(LOCAL_DEVELOPMENT_ORIGINS)
    allowed.update(extra_cors_origins(settings))

    for raw_candidate in (
        getattr(settings, "frontend_url", None),
        getattr(settings, "foreign_server_domain", None),
        getattr(settings, "iran_server_domain", None),
        getattr(settings, "foreign_server_url", None),
        getattr(settings, "iran_server_url", None),
    ):
        normalized = normalize_origin(raw_candidate)
        if normalized:
            allowed.add(normalized)

    return sorted(allowed)


def sms_public_host(settings) -> str:
    explicit = extract_host(getattr(settings, "sms_public_host", None))
    if explicit:
        return explicit

    for candidate in (
        getattr(settings, "frontend_url", None),
        getattr(settings, "iran_server_domain", None),
        getattr(settings, "iran_server_url", None),
    ):
        host = extract_host(candidate)
        if host:
            return host

    return "localhost"
