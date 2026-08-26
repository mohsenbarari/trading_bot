"""Allowlist for public sources required by the coin-rate snapshot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PublicTelegramSource:
    """Public channel metadata; no private peer ID is stored here."""

    code: str
    public_username: str
    compact_latest_per_minute: bool = False


PUBLIC_TELEGRAM_SOURCES = (
    PublicTelegramSource("MELTED_AGGREGATE", "abshdh"),
    PublicTelegramSource("MELTED_FLOW", "NaghdP"),
    PublicTelegramSource("USD_HERAT", "ToofanHarirodOfficial"),
    # Every real quote is an estimator input candidate.  Minute compaction
    # destroys the exact event sequence and cannot be reproduced causally.
    PublicTelegramSource("XAUUSD", "qheimat_ounce"),
)
SOURCES_BY_CODE = {source.code: source for source in PUBLIC_TELEGRAM_SOURCES}
SOURCES_BY_USERNAME = {
    source.public_username.casefold(): source for source in PUBLIC_TELEGRAM_SOURCES
}


def source_for_code(source_code: str) -> PublicTelegramSource:
    try:
        return SOURCES_BY_CODE[str(source_code).strip().upper()]
    except KeyError as exc:
        raise ValueError("public_market_source_not_allowed") from exc


def source_for_username(username: str) -> PublicTelegramSource:
    try:
        return SOURCES_BY_USERNAME[str(username).lstrip("@").casefold()]
    except KeyError as exc:
        raise ValueError("public_market_source_not_allowed") from exc
