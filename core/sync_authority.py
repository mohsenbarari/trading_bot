"""Shared cross-server authority constants for sync receiver and worker paths."""

from __future__ import annotations


IRAN_AUTHORITATIVE_SYNC_TABLES = frozenset(
    {
        "admin_broadcast_messages",
        "admin_market_messages",
        "commodities",
        "commodity_aliases",
        "accountant_relations",
        "customer_relations",
        "invitations",
        "market_runtime_state",
        "market_schedule_overrides",
        "trading_settings",
    }
)
