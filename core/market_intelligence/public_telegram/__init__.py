"""Offline-safe adapters for the four approved public market channels.

The package does not start a client or read environment credentials on import.
Transport activation is an explicit later operational decision.
"""

from .ingest import PublicTelegramMessage, ingest_public_message
from .sources import PUBLIC_TELEGRAM_SOURCES, PublicTelegramSource

__all__ = [
    "PUBLIC_TELEGRAM_SOURCES",
    "PublicTelegramMessage",
    "PublicTelegramSource",
    "ingest_public_message",
]
