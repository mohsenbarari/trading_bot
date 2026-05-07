"""Helpers for server affinity and cross-server authority routing."""
from __future__ import annotations

from typing import Optional

from core.config import settings

SERVER_FOREIGN = "foreign"
SERVER_IRAN = "iran"
KNOWN_SERVERS = {SERVER_FOREIGN, SERVER_IRAN}


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
    return normalize_server(settings.server_mode)


def _host_from_request(request) -> str:
    if not request or not hasattr(request, "headers"):
        return ""
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("x-original-host")
    host = forwarded_host or request.headers.get("host", "")
    return host.split(",")[0].split(":")[0].strip().lower()


def server_from_request(request, *, force_telegram_foreign: bool = False) -> str:
    if force_telegram_foreign:
        return SERVER_FOREIGN

    host = _host_from_request(request)
    iran_domain = (settings.iran_server_domain or "").strip().lower()
    foreign_domain = (settings.foreign_server_domain or "").strip().lower()

    if iran_domain and host == iran_domain:
        return SERVER_IRAN
    if foreign_domain and host == foreign_domain:
        return SERVER_FOREIGN

    if host in {"iran.server.ir", "coin.gold-trade.ir"}:
        return SERVER_IRAN
    if host in {"germany.server.com", "mini-app.362514.ir", "coin.362514.ir"}:
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


def is_remote_home(home_server: Optional[str]) -> bool:
    return normalize_server(home_server, current_server()) != current_server()
