"""Shared admin-write authority policy for cross-server product data."""
from __future__ import annotations

from dataclasses import dataclass

from core.server_routing import SERVER_IRAN, current_server, normalize_server


ADMIN_SHARED_AUTHORITY_SERVER = SERVER_IRAN

ADMIN_SHARED_TABLES: frozenset[str] = frozenset(
    {
        "admin_broadcast_messages",
        "admin_market_messages",
        "commodities",
        "commodity_aliases",
        "market_schedule_overrides",
        "trading_settings",
        "users",
    }
)


@dataclass(frozen=True)
class AdminWriteAuthorityDecision:
    ok: bool
    table_name: str
    operation: str
    surface: str
    current_server: str
    authority_server: str = ADMIN_SHARED_AUTHORITY_SERVER
    reason: str | None = None

    def as_error_detail(self) -> dict[str, str]:
        return {
            "error": self.reason or "admin_write_not_authoritative",
            "table": self.table_name,
            "operation": self.operation,
            "surface": self.surface,
            "current_server": self.current_server,
            "authority_server": self.authority_server,
        }


def check_shared_admin_write_authority(
    table_name: str,
    *,
    operation: str = "write",
    surface: str = "admin",
    server_mode: str | None = None,
) -> AdminWriteAuthorityDecision:
    """Return whether this server may mutate shared admin/product data.

    Step 9A intentionally uses a single-writer rule. Commodity rows do not carry
    a reliable version/timestamp today, so accepting admin writes on both servers
    would create silent forks that sync cannot deterministically resolve.
    """

    normalized_server = (
        normalize_server(server_mode, current_server())
        if server_mode is not None
        else current_server()
    )
    normalized_table = str(table_name).strip()
    normalized_operation = str(operation or "write").strip() or "write"
    normalized_surface = str(surface or "admin").strip() or "admin"

    if normalized_table not in ADMIN_SHARED_TABLES:
        return AdminWriteAuthorityDecision(
            ok=True,
            table_name=normalized_table,
            operation=normalized_operation,
            surface=normalized_surface,
            current_server=normalized_server,
        )

    if normalized_server == ADMIN_SHARED_AUTHORITY_SERVER:
        return AdminWriteAuthorityDecision(
            ok=True,
            table_name=normalized_table,
            operation=normalized_operation,
            surface=normalized_surface,
            current_server=normalized_server,
        )

    return AdminWriteAuthorityDecision(
        ok=False,
        table_name=normalized_table,
        operation=normalized_operation,
        surface=normalized_surface,
        current_server=normalized_server,
        reason="admin_write_not_authoritative",
    )


def admin_write_rejection_message(decision: AdminWriteAuthorityDecision) -> str:
    return (
        "این تغییر برای جلوگیری از دوشاخه شدن داده‌های مشترک فقط روی سرور ایران قابل انجام است. "
        f"سرور فعلی: {decision.current_server}، مرجع مجاز: {decision.authority_server}."
    )
