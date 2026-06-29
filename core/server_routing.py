"""Helpers for server affinity and cross-server authority routing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from core.config import settings
from core.deployment_surface import extract_host, foreign_server_aliases, iran_server_aliases
from core.request_logging import trusted_forwarded_host_from_request

SERVER_FOREIGN = "foreign"
SERVER_IRAN = "iran"
KNOWN_SERVERS = {SERVER_FOREIGN, SERVER_IRAN}
_CURRENT_SERVER_OVERRIDE: ContextVar[str | None] = ContextVar("current_server_override", default=None)


def normalize_server(value: Optional[str], default: str = SERVER_FOREIGN) -> str:
    if not value:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"germany", "de", "foreign", "german"}:
        return SERVER_FOREIGN
    if normalized in {"iran", "ir"}:
        return SERVER_IRAN
    return normalized if normalized in KNOWN_SERVERS else default


def current_server() -> str:
    override = _CURRENT_SERVER_OVERRIDE.get()
    if override:
        return normalize_server(override)
    return normalize_server(settings.server_mode)


@contextmanager
def override_current_server(server: str):
    """Temporarily override server affinity for isolated probes/tests."""
    token = _CURRENT_SERVER_OVERRIDE.set(normalize_server(server, current_server()))
    try:
        yield
    finally:
        _CURRENT_SERVER_OVERRIDE.reset(token)


def peer_server_name() -> str:
    return SERVER_IRAN if current_server() == SERVER_FOREIGN else SERVER_FOREIGN


def _host_from_request(request) -> str:
    if not request or not hasattr(request, "headers"):
        return ""
    forwarded_host = trusted_forwarded_host_from_request(request)
    host = forwarded_host or request.headers.get("host", "")
    return extract_host(host)


def server_from_request(request, *, force_telegram_foreign: bool = False) -> str:
    if force_telegram_foreign:
        return SERVER_FOREIGN

    host = _host_from_request(request)
    iran_aliases = iran_server_aliases(settings)
    foreign_aliases = foreign_server_aliases(settings)
    if host in iran_aliases and host in foreign_aliases:
        return current_server()
    if host in iran_aliases:
        return SERVER_IRAN
    if host in foreign_aliases:
        return SERVER_FOREIGN

    return current_server()


def peer_server_url_for(target_server: str) -> Optional[str]:
    target = normalize_server(target_server)
    if target == current_server():
        return None

    if target == SERVER_IRAN and settings.iran_server_url:
        return settings.iran_server_url.rstrip("/")
    if target == SERVER_FOREIGN and settings.germany_server_url:
        return settings.germany_server_url.rstrip("/")

    legacy_peer = settings.peer_server_url or settings.foreign_server_url
    return legacy_peer.rstrip("/") if legacy_peer else None


def default_peer_server_url() -> Optional[str]:
    return peer_server_url_for(peer_server_name())


def is_remote_home(home_server: Optional[str]) -> bool:
    return normalize_server(home_server, current_server()) != current_server()
